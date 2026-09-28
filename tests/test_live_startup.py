from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveKillLatch,
    LiveOperatorAuthorization,
)
from us_quant.trading.domain.live_startup import (
    LIVE_STARTUP_PROOF_TTL,
    LiveEndpointIdentity,
    LiveStartupBlocker,
    LiveStartupError,
    LiveStartupProof,
)

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
ENDPOINT = LiveEndpointIdentity()
ACCOUNT_ID = "U1234567"


def _fingerprint(account_id: str = ACCOUNT_ID) -> LiveAccountFingerprint:
    return LiveAccountFingerprint.from_identity(
        provider="IBKR",
        environment="LIVE",
        account_id=account_id,
        endpoint_identity=ENDPOINT.identity,
    )


def _limits(**changes: object) -> LiveCanaryLimits:
    values: dict[str, object] = {
        "capital_limit": Decimal("1000"),
        "max_order_notional": Decimal("250"),
        "max_daily_loss": Decimal("50"),
        "max_positions": 1,
        "max_open_orders": 1,
        "allowed_symbols": ("AAPL",),
        "allowed_strategy_versions": ("strategy-v1",),
    }
    values.update(changes)
    return LiveCanaryLimits(**values)  # type: ignore[arg-type]


def _authorization(
    *,
    account_id: str = ACCOUNT_ID,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    limits: LiveCanaryLimits | None = None,
) -> LiveOperatorAuthorization:
    return LiveOperatorAuthorization(
        authorization_id="auth-startup-1",
        created_at=NOW - timedelta(hours=1),
        expires_at=expires_at or NOW + timedelta(days=1),
        expected_account_fingerprint=_fingerprint(account_id),
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=limits or _limits(),
        revoked_at=revoked_at,
    )


def _armed_state(
    *,
    authorization: LiveOperatorAuthorization | None = None,
    kill_latch: LiveKillLatch | None = None,
) -> LiveAuthorizationState:
    state = LiveAuthorizationState(
        authorization=authorization or _authorization(),
        kill_latch=kill_latch or LiveKillLatch(),
    )
    return state.request_session_arm(
        safety_revision=1,
        now=NOW,
        account_fingerprint=_fingerprint(),
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )


def _capture(**changes: object) -> LiveStartupProof:
    facts: dict[str, object] = {
        "now": NOW,
        "broker_connected": True,
        "observed_endpoint": ENDPOINT,
        "managed_account_ids": (ACCOUNT_ID,),
        "broker_account_id": ACCOUNT_ID,
        "connection_observed_at": NOW,
        "account_truth_known": True,
        "account_truth_observed_at": NOW,
        "market_truth_known": True,
        "market_truth_observed_at": NOW,
        "open_orders_known": True,
        "open_orders_observed_at": NOW,
        "positions_known": True,
        "positions_observed_at": NOW,
        "reconciliation_clean": True,
        "reconciliation_observed_at": NOW,
        "authorization_state": _armed_state(),
    }
    facts.update(changes)
    return LiveStartupProof.capture(**facts)  # type: ignore[arg-type]


def test_startup_proof_captures_current_exact_account_facts_without_raw_account_id():
    proof = _capture()

    assert proof.ready
    assert proof.blockers == ()
    assert proof.endpoint_identity == "ibkr-live-loopback:127.0.0.1:4001"
    assert proof.account_fingerprint == _fingerprint()
    assert proof.broker_connected
    assert proof.account_truth_known and proof.market_truth_known
    assert proof.open_orders_known and proof.positions_known
    assert proof.reconciliation_clean
    assert proof.authorization_valid and proof.session_armed
    assert not proof.kill_latched and proof.limits_valid
    assert ACCOUNT_ID not in repr(proof)
    assert proof.is_fresh_at(NOW + LIVE_STARTUP_PROOF_TTL - timedelta(microseconds=1))
    assert not proof.is_fresh_at(NOW + LIVE_STARTUP_PROOF_TTL)


def test_proof_cannot_outlive_its_authorization():
    authorization = _authorization(expires_at=NOW + timedelta(seconds=10))
    proof = _capture(authorization_state=_armed_state(authorization=authorization))

    assert proof.ready
    assert proof.expires_at == NOW + timedelta(seconds=10)
    assert proof.is_fresh_at(NOW + timedelta(seconds=9))
    assert not proof.is_fresh_at(NOW + timedelta(seconds=10, microseconds=1))


def test_account_fingerprint_is_bound_to_the_configured_endpoint():
    other_endpoint_fingerprint = LiveAccountFingerprint.from_identity(
        provider="IBKR",
        environment="LIVE",
        account_id=ACCOUNT_ID,
        endpoint_identity="ibkr-live-loopback:127.0.0.1:4002",
    )
    authorization = LiveOperatorAuthorization(
        authorization_id="auth-other-endpoint",
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
        expected_account_fingerprint=other_endpoint_fingerprint,
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=_limits(),
    )

    proof = _capture(authorization_state=LiveAuthorizationState(authorization))

    assert LiveStartupBlocker.ACCOUNT_MISMATCH in proof.blockers
    assert LiveStartupBlocker.AUTHORIZATION_ACCOUNT_MISMATCH in proof.blockers
    assert proof.account_fingerprint is None


def test_broker_truth_must_be_scoped_to_the_exactly_matched_managed_account():
    proof = _capture(broker_account_id="DU0000000")

    assert LiveStartupBlocker.ACCOUNT_MISMATCH in proof.blockers
    assert LiveStartupBlocker.AUTHORIZATION_ACCOUNT_MISMATCH in proof.blockers
    assert proof.account_fingerprint is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"broker_connected": False}, LiveStartupBlocker.BROKER_DISCONNECTED),
        (
            {"connection_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.BROKER_CONNECTION_STALE,
        ),
        ({"managed_account_ids": ()}, LiveStartupBlocker.EXPECTED_ACCOUNT_UNAVAILABLE),
        (
            {"managed_account_ids": (ACCOUNT_ID, "DU0000000")},
            LiveStartupBlocker.MULTIPLE_MANAGED_ACCOUNTS,
        ),
        (
            {"managed_account_ids": (ACCOUNT_ID, ACCOUNT_ID)},
            LiveStartupBlocker.MULTIPLE_MANAGED_ACCOUNTS,
        ),
        (
            {"managed_account_ids": ("DU0000000",)},
            LiveStartupBlocker.ACCOUNT_MISMATCH,
        ),
        (
            {"account_truth_observed_at": None},
            LiveStartupBlocker.ACCOUNT_TRUTH_UNKNOWN,
        ),
        (
            {"account_truth_known": False},
            LiveStartupBlocker.ACCOUNT_TRUTH_UNKNOWN,
        ),
        (
            {"account_truth_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.ACCOUNT_TRUTH_STALE,
        ),
        (
            {"account_truth_observed_at": NOW - LIVE_STARTUP_PROOF_TTL},
            LiveStartupBlocker.ACCOUNT_TRUTH_STALE,
        ),
        (
            {"market_truth_observed_at": None},
            LiveStartupBlocker.MARKET_TRUTH_UNKNOWN,
        ),
        (
            {"market_truth_known": False},
            LiveStartupBlocker.MARKET_TRUTH_UNKNOWN,
        ),
        (
            {"market_truth_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.MARKET_TRUTH_STALE,
        ),
        ({"open_orders_known": False}, LiveStartupBlocker.OPEN_ORDERS_UNKNOWN),
        (
            {"open_orders_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.OPEN_ORDERS_STALE,
        ),
        ({"positions_known": False}, LiveStartupBlocker.POSITIONS_UNKNOWN),
        (
            {"positions_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.POSITIONS_STALE,
        ),
        (
            {"reconciliation_observed_at": NOW - LIVE_STARTUP_PROOF_TTL - timedelta(seconds=1)},
            LiveStartupBlocker.RECONCILIATION_STALE,
        ),
        ({"reconciliation_clean": False}, LiveStartupBlocker.RECONCILIATION_UNCLEAN),
        (
            {"authorization_state": LiveAuthorizationState()},
            LiveStartupBlocker.AUTHORIZATION_MISSING,
        ),
        (
            {"authorization_state": LiveAuthorizationState(_authorization(expires_at=NOW))},
            LiveStartupBlocker.AUTHORIZATION_EXPIRED,
        ),
        (
            {
                "authorization_state": LiveAuthorizationState(
                    _authorization(revoked_at=NOW - timedelta(minutes=1))
                )
            },
            LiveStartupBlocker.AUTHORIZATION_REVOKED,
        ),
        (
            {"authorization_state": LiveAuthorizationState(
                _authorization(limits=_limits(capital_limit=Decimal("0")))
            )},
            LiveStartupBlocker.INVALID_LIMITS,
        ),
        (
            {"authorization_state": LiveAuthorizationState(
                _authorization(), LiveKillLatch().engage(at=NOW, reason="test")
            )},
            LiveStartupBlocker.KILL_LATCHED,
        ),
        (
            {"authorization_state": LiveAuthorizationState(_authorization())},
            LiveStartupBlocker.SESSION_NOT_ARMED,
        ),
    ],
)
def test_any_missing_or_unsafe_startup_fact_blocks_proof(changes, expected):
    proof = _capture(**changes)

    assert not proof.ready
    assert expected in proof.blockers
    assert not proof.is_fresh_at(NOW)


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("192.168.1.20", 4001),
        ("localhost", 7496),
        ("", 4001),
    ],
)
def test_stage4_endpoint_is_restricted_to_loopback_gateway_live(host, port):
    with pytest.raises(LiveStartupError):
        LiveEndpointIdentity(host=host, port=port)


def test_proof_timestamps_must_be_timezone_aware():
    with pytest.raises(LiveStartupError, match="now must be timezone-aware"):
        _capture(now=datetime(2026, 9, 28))

    with pytest.raises(LiveStartupError, match="account_truth_observed_at"):
        _capture(account_truth_observed_at=datetime(2026, 9, 28))


def test_missing_broker_selected_account_blocks_startup():
    proof = _capture(broker_account_id=None)

    assert LiveStartupBlocker.BROKER_ACCOUNT_UNAVAILABLE in proof.blockers
    assert not proof.ready


def test_callers_cannot_construct_a_startup_proof_without_capture():
    with pytest.raises(LiveStartupError, match="must be created by capture"):
        LiveStartupProof()  # type: ignore[call-arg]
