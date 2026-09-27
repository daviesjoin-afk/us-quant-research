from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
import ast
import json
from pathlib import Path
from threading import Event, Thread

import pytest

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveArmBlocker,
    LiveArmRefused,
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveKillLatch,
    LiveOperation,
    LiveOperatorAuthorization,
    LiveSafetyRecord,
)
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyConflict,
    LiveSafetyStoreUnreadable,
)

_ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def _fingerprint(account: str = "U1234567") -> LiveAccountFingerprint:
    return LiveAccountFingerprint.from_identity(
        provider="IBKR",
        environment="LIVE",
        account_id=account,
        endpoint_identity="ib-gateway-live-loopback",
    )


def _limits(**overrides: object) -> LiveCanaryLimits:
    values: dict[str, object] = {
        "capital_limit": Decimal("1000"),
        "max_order_notional": Decimal("250"),
        "max_daily_loss": Decimal("50"),
        "max_positions": 1,
        "max_open_orders": 1,
        "allowed_symbols": ("AAPL",),
        "allowed_strategy_versions": ("strategy-v1",),
    }
    values.update(overrides)
    return LiveCanaryLimits(**values)  # type: ignore[arg-type]


def _authorization(
    *,
    limits: LiveCanaryLimits | None = None,
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> LiveOperatorAuthorization:
    return LiveOperatorAuthorization(
        authorization_id="auth-1",
        created_at=NOW - timedelta(hours=1),
        expires_at=expires_at or NOW + timedelta(days=1),
        expected_account_fingerprint=_fingerprint(),
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=limits or _limits(),
        revoked_at=revoked_at,
    )


def _armed_state(
    *,
    authorization: LiveOperatorAuthorization | None = None,
    kill_latch: LiveKillLatch | None = None,
) -> LiveAuthorizationState:
    return LiveAuthorizationState(
        authorization=authorization or _authorization(),
        kill_latch=kill_latch or LiveKillLatch(),
    )


def _arm(state: LiveAuthorizationState) -> LiveAuthorizationState:
    return state.request_session_arm(
        now=NOW,
        account_fingerprint=_fingerprint(),
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )


def test_new_safety_store_is_disabled_unarmed_and_unlatched(tmp_path):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")

    record = repository.load()
    state = LiveAuthorizationState(record.authorization, record.kill_latch)

    assert record == LiveSafetyRecord()
    assert state.session_armed is False
    assert not record.kill_latch.is_latched
    with pytest.raises(LiveArmRefused) as error:
        _arm(state)
    assert error.value.blockers == (LiveArmBlocker.AUTHORIZATION_MISSING,)


def test_session_arm_is_not_a_persistable_field_and_restart_drops_it(tmp_path):
    armed = _arm(_armed_state())
    assert armed.session_armed
    assert "session_armed" not in {field.name for field in fields(LiveSafetyRecord)}

    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    persisted = LiveSafetyRecord(
        revision=1,
        authorization=armed.authorization,
        kill_latch=armed.kill_latch,
    )
    repository.save(expected_revision=0, replacement=persisted)
    reopened = SQLiteLiveSafetyRepository(repository.path)
    durable = reopened.load()
    restarted = LiveAuthorizationState(durable.authorization, durable.kill_latch)

    assert restarted.session_armed is False
    assert armed.after_restart().session_armed is False
    assert _arm(restarted).session_armed


def test_kill_latch_survives_restart_and_only_blocks_exposure_increase(tmp_path):
    latch = LiveKillLatch().engage(at=NOW, reason="operator kill")
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, _authorization(), latch),
    )

    restored = SQLiteLiveSafetyRepository(repository.path).load()

    assert restored.kill_latch.is_latched
    assert restored.kill_latch.blocks(LiveOperation.INCREASE_EXPOSURE)
    assert restored.kill_latch.blocks("unknown_operation")
    for operation in (
        LiveOperation.READ_BROKER_TRUTH,
        LiveOperation.RECONCILE,
        LiveOperation.CANCEL_TRACKED_ORDER,
        LiveOperation.REDUCE_POSITION,
        LiveOperation.DISCONNECT,
        LiveOperation.FINALIZE,
    ):
        assert not restored.kill_latch.blocks(operation)
    with pytest.raises(LiveArmRefused) as error:
        _arm(LiveAuthorizationState(restored.authorization, restored.kill_latch))
    assert LiveArmBlocker.KILL_LATCHED in error.value.blockers


@pytest.mark.parametrize(
    ("authorization", "account", "strategy", "expected"),
    [
        (None, _fingerprint(), "strategy-v1", LiveArmBlocker.AUTHORIZATION_MISSING),
        (
            _authorization(expires_at=NOW),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.AUTHORIZATION_EXPIRED,
        ),
        (
            _authorization(revoked_at=NOW - timedelta(minutes=1)),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.AUTHORIZATION_REVOKED,
        ),
        (
            _authorization(),
            _fingerprint("U7654321"),
            "strategy-v1",
            LiveArmBlocker.ACCOUNT_MISMATCH,
        ),
        (
            _authorization(),
            _fingerprint(),
            "unapproved-v2",
            LiveArmBlocker.STRATEGY_NOT_APPROVED,
        ),
        (
            _authorization(limits=_limits(capital_limit=Decimal("0"))),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.INVALID_LIMITS,
        ),
        (
            _authorization(limits=_limits(max_order_notional=Decimal("-1"))),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.INVALID_LIMITS,
        ),
        (
            _authorization(limits=_limits(max_daily_loss=Decimal("NaN"))),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.INVALID_LIMITS,
        ),
        (
            _authorization(limits=_limits(max_positions=0)),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.INVALID_LIMITS,
        ),
        (
            _authorization(limits=_limits(allowed_symbols=())),
            _fingerprint(),
            "strategy-v1",
            LiveArmBlocker.INVALID_LIMITS,
        ),
    ],
)
def test_invalid_authorization_account_or_limits_block_session_arm(
    authorization, account, strategy, expected
):
    state = LiveAuthorizationState(authorization)

    blockers = state.arm_blockers(
        now=NOW,
        account_fingerprint=account,
        strategy_version_id=strategy,
        operator_confirmed=True,
    )

    assert expected in blockers
    assert not state.session_armed


def test_zero_defaults_and_confirmation_are_fail_closed():
    defaults = LiveCanaryLimits()
    assert defaults.capital_limit == Decimal("0")
    assert defaults.max_order_notional == Decimal("0")
    assert defaults.max_daily_loss == Decimal("0")
    assert defaults.max_positions == 0
    assert defaults.max_open_orders == 0
    assert defaults.blockers() == (LiveArmBlocker.INVALID_LIMITS,)

    state = _armed_state()
    assert LiveArmBlocker.OPERATOR_CONFIRMATION_REQUIRED in state.arm_blockers(
        now=NOW,
        account_fingerprint=_fingerprint(),
        strategy_version_id="strategy-v1",
        operator_confirmed=False,
    )
    with pytest.raises(ValueError, match="operator_confirmed must be a boolean"):
        state.arm_blockers(
            now=NOW,
            account_fingerprint=_fingerprint(),
            strategy_version_id="strategy-v1",
            operator_confirmed=1,
        )


def test_session_arm_cannot_be_supplied_to_the_state_constructor():
    with pytest.raises(TypeError, match="session_armed"):
        LiveAuthorizationState(session_armed=True)


def test_account_fingerprint_rejects_unmasked_display_values():
    with pytest.raises(ValueError, match="must be masked"):
        LiveAccountFingerprint("a" * 64, "U1234")
    with pytest.raises(ValueError, match="must be masked"):
        LiveAccountFingerprint("a" * 64, "…12345")


def test_repository_compare_and_swap_refuses_stale_writer(tmp_path):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    replacement = LiveSafetyRecord(1, _authorization(), LiveKillLatch())
    repository.save(expected_revision=0, replacement=replacement)

    with pytest.raises(LiveSafetyConflict):
        repository.save(expected_revision=0, replacement=LiveSafetyRecord(1))

    assert repository.load() == replacement


def test_execution_lease_serializes_kill_writer_until_submit_boundary_releases(tmp_path):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    initial = LiveSafetyRecord(1, _authorization(), LiveKillLatch())
    repository.save(expected_revision=0, replacement=initial)
    writer_started = Event()
    writer_finished = Event()

    def write_kill_latch():
        writer_started.set()
        repository.save(
            expected_revision=1,
            replacement=LiveSafetyRecord(
                2,
                initial.authorization,
                LiveKillLatch().engage(at=NOW, reason="concurrent operator kill"),
            ),
        )
        writer_finished.set()

    with repository.execution_lease() as locked:
        assert locked == initial
        writer = Thread(target=write_kill_latch)
        writer.start()
        assert writer_started.wait(timeout=1)
        assert not writer_finished.wait(timeout=0.05)

    assert writer_finished.wait(timeout=1)
    writer.join(timeout=1)
    assert repository.load().kill_latch.is_latched


def test_repository_persists_no_raw_account_or_session_arm(tmp_path):
    path = tmp_path / "live-safety.sqlite3"
    repository = SQLiteLiveSafetyRepository(path)
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, _authorization(), LiveKillLatch()),
    )
    with sqlite3.connect(path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='live_safety_state'"
        ).fetchone()[0]
        stored = connection.execute(
            "SELECT authorization_json FROM live_safety_state"
        ).fetchone()[0]

    assert "session_armed" not in schema
    assert "U1234567" not in stored
    assert "4567" in stored


def test_corrupt_record_fails_closed(tmp_path):
    path = tmp_path / "live-safety.sqlite3"
    repository = SQLiteLiveSafetyRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_safety_state VALUES ('live', 1, '{broken', 0, NULL, NULL)"
        )

    with pytest.raises(LiveSafetyStoreUnreadable):
        repository.load()


@pytest.mark.parametrize(
    ("field_path", "bad_value"),
    [
        (("limits", "allowed_symbols"), {"AAPL": True}),
        (("limits", "allowed_strategy_versions"), "strategy-v1"),
        (("approved_strategy_version_ids",), {"strategy-v1": True}),
    ],
)
def test_non_array_allowlists_make_the_persisted_record_unreadable(
    tmp_path, field_path, bad_value
):
    path = tmp_path / "live-safety.sqlite3"
    repository = SQLiteLiveSafetyRepository(path)
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, _authorization(), LiveKillLatch()),
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT authorization_json FROM live_safety_state"
        ).fetchone()
        payload = json.loads(row[0])
        target = payload
        for component in field_path[:-1]:
            target = target[component]
        target[field_path[-1]] = bad_value
        connection.execute(
            "UPDATE live_safety_state SET authorization_json = ?",
            (json.dumps(payload),),
        )

    with pytest.raises(LiveSafetyStoreUnreadable):
        repository.load()


def test_write_does_not_overwrite_an_unreadable_safety_record(tmp_path):
    path = tmp_path / "live-safety.sqlite3"
    repository = SQLiteLiveSafetyRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_safety_state VALUES ('live', 1, '{broken', 0, NULL, NULL)"
        )

    with pytest.raises(LiveSafetyStoreUnreadable):
        repository.save(
            expected_revision=1,
            replacement=LiveSafetyRecord(2, _authorization(), LiveKillLatch()),
        )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT authorization_json FROM live_safety_state"
        ).fetchone()[0] == "{broken"


def test_clearing_kill_latch_does_not_restore_session_arm():
    armed = _arm(_armed_state())
    previously_armed = armed.engage_kill(at=NOW, reason="stop")

    cleared = LiveAuthorizationState(
        previously_armed.authorization,
        previously_armed.kill_latch.clear(),
    )

    assert not cleared.kill_latch.is_latched
    assert not cleared.session_armed
    assert cleared.after_restart().session_armed is False


def test_live_safety_domain_and_port_keep_inward_dependencies():
    domain_path = _ROOT / "src/us_quant/trading/domain/live_safety.py"
    port_path = _ROOT / "src/us_quant/trading/ports/live_safety_repository.py"
    domain = ast.parse(domain_path.read_text(encoding="utf-8"))
    port = ast.parse(port_path.read_text(encoding="utf-8"))

    domain_imports = {
        alias.name
        for node in ast.walk(domain)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(domain)
        if isinstance(node, ast.ImportFrom)
    }
    port_imports = {
        alias.name
        for node in ast.walk(port)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(port)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        name.startswith(("sqlite3", "us_quant.trading.adapters", "us_quant.desktop"))
        for name in domain_imports
    )
    assert not any(
        name.startswith(("sqlite3", "us_quant.trading.adapters", "us_quant.desktop"))
        for name in port_imports
    )
