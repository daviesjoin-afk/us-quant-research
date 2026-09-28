from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.application.live_recovery import (
    LiveCanaryRecovery,
    LiveRecoveryRefused,
)
from us_quant.trading.domain.live_recovery import LiveRecoveryEvidence
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveKillLatch,
    LiveOperatorAuthorization,
    LiveRecoveryLatch,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_startup import LIVE_STARTUP_PROOF_TTL


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
FINGERPRINT = LiveAccountFingerprint.from_identity(
    provider="IBKR",
    environment="LIVE",
    account_id="U1234567",
    endpoint_identity="ibkr-live-loopback:127.0.0.1:4001",
)
OTHER_FINGERPRINT = LiveAccountFingerprint.from_identity(
    provider="IBKR",
    environment="LIVE",
    account_id="U9876543",
    endpoint_identity="ibkr-live-loopback:127.0.0.1:4001",
)


def _authorization() -> LiveOperatorAuthorization:
    return LiveOperatorAuthorization(
        authorization_id="recovery-test-authorization",
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        expected_account_fingerprint=FINGERPRINT,
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=LiveCanaryLimits(
            capital_limit=Decimal("2000"),
            max_order_notional=Decimal("500"),
            max_daily_loss=Decimal("100"),
            max_positions=1,
            max_open_orders=1,
            allowed_symbols=("AAPL",),
            allowed_strategy_versions=("strategy-v1",),
        ),
    )


def _repository(path) -> SQLiteLiveSafetyRepository:
    repository = SQLiteLiveSafetyRepository(path)
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, _authorization(), LiveKillLatch()),
    )
    return repository


def _evidence(**changes: object) -> LiveRecoveryEvidence:
    values: dict[str, object] = {
        "observed_at": NOW + timedelta(seconds=1),
        "account_fingerprint": FINGERPRINT,
        "broker_connected": True,
        "open_orders_known": True,
        "positions_known": True,
        "fills_known": True,
        "reconciliation_clean": True,
        "uncertain_submission_resolved": True,
        "reconciled_broker_order_ids": (),
    }
    values.update(changes)
    return LiveRecoveryEvidence.capture(**values)  # type: ignore[arg-type]


def test_recovery_barrier_persists_across_restart_and_invalidates_the_old_arm(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    state = LiveAuthorizationState(repository.load().authorization).request_session_arm(
        safety_revision=1,
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    latched = LiveCanaryRecovery(repository, now=lambda: NOW).require_reconciliation(
        reason="broker disconnected during an active session"
    )

    restarted = SQLiteLiveSafetyRepository(repository.path).load()
    restored_state = LiveAuthorizationState(
        restarted.authorization, restarted.kill_latch, restarted.recovery_latch
    )

    assert latched.revision == 2
    assert restarted.recovery_latch.is_required
    assert restored_state.recovery_latch == latched.recovery_latch
    assert restored_state.session_armed is False
    assert not state.recovery_latch.is_required
    assert state.session_arm_revision != restarted.revision


@pytest.mark.parametrize(
    "changes",
    [
        {"broker_connected": False},
        {"open_orders_known": False},
        {"positions_known": False},
        {"fills_known": False},
        {"reconciliation_clean": False},
        {"uncertain_submission_resolved": False},
        {"account_fingerprint": OTHER_FINGERPRINT},
    ],
)
def test_recovery_refuses_incomplete_or_mismatched_broker_evidence(tmp_path, changes):
    repository = _repository(tmp_path / "live.sqlite3")
    clock = [NOW]
    recovery = LiveCanaryRecovery(repository, now=lambda: clock[0])
    recovery.require_reconciliation(reason="uncertain submission")
    clock[0] = NOW + timedelta(seconds=2)

    with pytest.raises(LiveRecoveryRefused):
        recovery.confirm_reconciled(
            _evidence(**changes), operator_confirmed=True
        )
    assert repository.load().recovery_latch.is_required


def test_recovery_requires_fresh_evidence_and_explicit_operator_confirmation(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    clock = [NOW]
    recovery = LiveCanaryRecovery(repository, now=lambda: clock[0])
    recovery.require_reconciliation(reason="disconnect")
    clock[0] = NOW + timedelta(seconds=2)

    with pytest.raises(LiveRecoveryRefused, match="confirmation"):
        recovery.confirm_reconciled(_evidence(), operator_confirmed=False)
    stale = _evidence(observed_at=NOW - LIVE_STARTUP_PROOF_TTL)
    with pytest.raises(LiveRecoveryRefused, match="stale"):
        recovery.confirm_reconciled(stale, operator_confirmed=True)
    future = _evidence(observed_at=NOW + timedelta(seconds=3))
    with pytest.raises(LiveRecoveryRefused, match="stale"):
        recovery.confirm_reconciled(future, operator_confirmed=True)

    assert repository.load().recovery_latch.is_required


def test_recovery_rejects_clean_evidence_captured_before_the_barrier(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    recovery = LiveCanaryRecovery(repository, now=lambda: NOW)
    recovery.require_reconciliation(reason="uncertain submission", broker_order_id=814)

    with pytest.raises(LiveRecoveryRefused, match="predates"):
        recovery.confirm_reconciled(
            _evidence(
                observed_at=NOW - timedelta(microseconds=1),
                reconciled_broker_order_ids=(814,),
            ),
            operator_confirmed=True,
        )
    assert repository.load().recovery_latch.is_required


def test_later_unsafe_event_refreshes_barrier_and_invalidates_earlier_evidence(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    first_event = LiveCanaryRecovery(repository, now=lambda: NOW).require_reconciliation(
        reason="process restart"
    )
    earlier_evidence = _evidence(observed_at=NOW + timedelta(seconds=1))

    with repository.execution_lease() as lease:
        refreshed = lease.require_reconciliation(
            at=NOW + timedelta(seconds=2), reason="broker disconnected"
        )

    assert refreshed.revision == first_event.revision + 1
    assert refreshed.recovery_latch.required_at == NOW + timedelta(seconds=2)
    assert refreshed.recovery_latch.reason == "broker disconnected"
    with pytest.raises(LiveRecoveryRefused, match="predates"):
        LiveCanaryRecovery(repository, now=lambda: NOW + timedelta(seconds=3)).confirm_reconciled(
            earlier_evidence, operator_confirmed=True
        )
    assert repository.load().recovery_latch.is_required


def test_recovery_order_id_cannot_exist_without_active_barrier():
    with pytest.raises(ValueError, match="requires an active barrier"):
        LiveRecoveryLatch(broker_order_id=814)


def test_uncertain_order_must_be_present_in_reconciliation_evidence(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    clock = [NOW]
    recovery = LiveCanaryRecovery(repository, now=lambda: clock[0])
    recovery.require_reconciliation(
        reason="uncertain submission", broker_order_id=814
    )
    clock[0] = NOW + timedelta(seconds=2)

    with pytest.raises(LiveRecoveryRefused, match="broker order id"):
        recovery.confirm_reconciled(_evidence(), operator_confirmed=True)
    cleared = recovery.confirm_reconciled(
        _evidence(reconciled_broker_order_ids=(814,)),
        operator_confirmed=True,
    )
    assert not cleared.recovery_latch.is_required


def test_clean_operator_reconciliation_clears_only_recovery_and_requires_new_arm(tmp_path):
    repository = _repository(tmp_path / "live.sqlite3")
    initial = repository.load()
    killed = LiveSafetyRecord(
        2,
        initial.authorization,
        LiveKillLatch().engage(at=NOW, reason="operator kill"),
        LiveRecoveryLatch().require(at=NOW, reason="disconnect"),
    )
    repository.save(expected_revision=1, replacement=killed)
    old_state = LiveAuthorizationState(
        initial.authorization, initial.kill_latch, initial.recovery_latch
    ).request_session_arm(
        safety_revision=1,
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    recovery = LiveCanaryRecovery(repository, now=lambda: NOW + timedelta(seconds=2))

    cleared = recovery.confirm_reconciled(_evidence(), operator_confirmed=True)

    assert cleared.revision == 3
    assert not cleared.recovery_latch.is_required
    assert cleared.kill_latch.is_latched
    assert old_state.session_arm_revision != cleared.revision
    blocked = LiveAuthorizationState(
        cleared.authorization, cleared.kill_latch, cleared.recovery_latch
    ).arm_blockers(
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    assert "kill_latched" in blocked


def test_existing_stage4a_database_is_migrated_without_losing_safety_state(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-live.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE live_safety_state(
                   key TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                   authorization_json TEXT, kill_latched INTEGER NOT NULL,
                   kill_latched_at TEXT, kill_reason TEXT
               )"""
        )
        connection.execute(
            "INSERT INTO live_safety_state VALUES ('live', 1, NULL, 0, NULL, NULL)"
        )

    record = SQLiteLiveSafetyRepository(path).load()

    assert record.revision == 1
    assert not record.kill_latch.is_latched
    assert not record.recovery_latch.is_required
