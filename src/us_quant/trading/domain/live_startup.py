"""Stage 4-B immutable evidence for a single Live startup attempt.

This module proves current broker/account facts only. It does not connect to a
broker, arm a session, construct an execution adapter, or submit an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveAuthorizationState,
)


LIVE_STARTUP_PROOF_TTL = timedelta(seconds=30)


class LiveStartupError(ValueError):
    """Invalid input to a Live startup proof capture."""


class LiveStartupBlocker(StrEnum):
    BROKER_DISCONNECTED = "broker_disconnected"
    BROKER_CONNECTION_UNKNOWN = "broker_connection_unknown"
    BROKER_CONNECTION_STALE = "broker_connection_stale"
    BROKER_ACCOUNT_UNAVAILABLE = "broker_account_unavailable"
    EXPECTED_ACCOUNT_UNAVAILABLE = "expected_account_unavailable"
    MULTIPLE_MANAGED_ACCOUNTS = "multiple_managed_accounts"
    ACCOUNT_MISMATCH = "account_mismatch"
    ACCOUNT_TRUTH_UNKNOWN = "account_truth_unknown"
    ACCOUNT_TRUTH_STALE = "account_truth_stale"
    MARKET_TRUTH_UNKNOWN = "market_truth_unknown"
    MARKET_TRUTH_STALE = "market_truth_stale"
    OPEN_ORDERS_UNKNOWN = "open_orders_unknown"
    OPEN_ORDERS_STALE = "open_orders_stale"
    POSITIONS_UNKNOWN = "positions_unknown"
    POSITIONS_STALE = "positions_stale"
    RECONCILIATION_UNKNOWN = "reconciliation_unknown"
    RECONCILIATION_STALE = "reconciliation_stale"
    RECONCILIATION_UNCLEAN = "reconciliation_unclean"
    KILL_LATCHED = "kill_latched"
    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    AUTHORIZATION_REVOKED = "authorization_revoked"
    AUTHORIZATION_ACCOUNT_MISMATCH = "authorization_account_mismatch"
    SESSION_NOT_ARMED = "session_not_armed"
    INVALID_LIMITS = "invalid_limits"


@dataclass(frozen=True, slots=True)
class LiveEndpointIdentity:
    """The only Stage 4 v1 Live endpoint: IBKR Gateway on local port 4001."""

    host: str = "127.0.0.1"
    port: int = 4001

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or self.host.strip().casefold() not in {
            "127.0.0.1",
            "localhost",
        }:
            raise LiveStartupError("Stage 4 Live endpoint must use loopback")
        if type(self.port) is not int or self.port != 4001:
            raise LiveStartupError("Stage 4 Live endpoint must use port 4001")

    @property
    def identity(self) -> str:
        """Canonical endpoint input for the account fingerprint."""

        return "ibkr-live-loopback:127.0.0.1:4001"


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LiveStartupError(f"{name} must be timezone-aware")


def _fresh(observed_at: datetime | None, *, now: datetime) -> bool:
    if observed_at is None:
        return False
    age = now - observed_at
    return timedelta(0) <= age < LIVE_STARTUP_PROOF_TTL


@dataclass(frozen=True, slots=True, init=False)
class LiveStartupProof:
    """A frozen, non-secret snapshot of facts required before Live can start."""

    observed_at: datetime
    expires_at: datetime
    endpoint_identity: str | None
    account_fingerprint: LiveAccountFingerprint | None
    authorization_fingerprint: str | None
    session_arm_id: str | None
    broker_connected: bool
    connection_observed_at: datetime | None
    account_truth_known: bool
    account_truth_observed_at: datetime | None
    market_truth_known: bool
    market_truth_observed_at: datetime | None
    open_orders_known: bool
    open_orders_observed_at: datetime | None
    positions_known: bool
    positions_observed_at: datetime | None
    reconciliation_clean: bool
    reconciliation_observed_at: datetime | None
    authorization_valid: bool
    session_armed: bool
    kill_latched: bool
    limits_valid: bool
    blockers: tuple[LiveStartupBlocker, ...]

    def __new__(cls, *args: object, **kwargs: object) -> "LiveStartupProof":
        raise LiveStartupError("LiveStartupProof must be created by capture")

    @property
    def ready(self) -> bool:
        return not self.blockers

    def is_fresh_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        return self.ready and self.observed_at <= now < self.expires_at

    @classmethod
    def capture(
        cls,
        *,
        now: datetime,
        broker_connected: bool,
        observed_endpoint: LiveEndpointIdentity,
        managed_account_ids: tuple[str, ...],
        broker_account_id: str | None,
        connection_observed_at: datetime | None,
        account_truth_known: bool,
        account_truth_observed_at: datetime | None,
        market_truth_known: bool,
        market_truth_observed_at: datetime | None,
        open_orders_known: bool,
        open_orders_observed_at: datetime | None,
        positions_known: bool,
        positions_observed_at: datetime | None,
        reconciliation_clean: bool,
        reconciliation_observed_at: datetime | None,
        authorization_state: LiveAuthorizationState,
    ) -> "LiveStartupProof":
        """Capture facts and report every blocker without retaining raw IDs."""

        _require_aware(now, "now")
        if type(broker_connected) is not bool:
            raise LiveStartupError("broker_connected must be a boolean")
        if not isinstance(observed_endpoint, LiveEndpointIdentity):
            raise LiveStartupError("observed_endpoint has an invalid type")
        if not isinstance(managed_account_ids, tuple) or any(
            not isinstance(account_id, str) or not account_id.strip()
            for account_id in managed_account_ids
        ):
            raise LiveStartupError("managed_account_ids must be a tuple of nonblank strings")
        if broker_account_id is not None and (
            not isinstance(broker_account_id, str) or not broker_account_id.strip()
        ):
            raise LiveStartupError("broker_account_id must be None or a nonblank string")
        if type(open_orders_known) is not bool or type(positions_known) is not bool:
            raise LiveStartupError("broker truth known flags must be booleans")
        if type(account_truth_known) is not bool or type(market_truth_known) is not bool:
            raise LiveStartupError("account and market truth known flags must be booleans")
        if type(reconciliation_clean) is not bool:
            raise LiveStartupError("reconciliation_clean must be a boolean")
        if not isinstance(authorization_state, LiveAuthorizationState):
            raise LiveStartupError("authorization_state has an invalid type")
        for name, timestamp in (
            ("connection_observed_at", connection_observed_at),
            ("account_truth_observed_at", account_truth_observed_at),
            ("market_truth_observed_at", market_truth_observed_at),
            ("open_orders_observed_at", open_orders_observed_at),
            ("positions_observed_at", positions_observed_at),
            ("reconciliation_observed_at", reconciliation_observed_at),
        ):
            if timestamp is not None:
                _require_aware(timestamp, name)

        blockers: list[LiveStartupBlocker] = []
        if not broker_connected:
            blockers.append(LiveStartupBlocker.BROKER_DISCONNECTED)
        if connection_observed_at is None:
            blockers.append(LiveStartupBlocker.BROKER_CONNECTION_UNKNOWN)
        elif not _fresh(connection_observed_at, now=now):
            blockers.append(LiveStartupBlocker.BROKER_CONNECTION_STALE)

        endpoint_identity = observed_endpoint.identity
        authorization = authorization_state.authorization
        expected = (
            authorization.expected_account_fingerprint
            if authorization is not None
            else None
        )
        managed_fingerprints: list[LiveAccountFingerprint] = []
        for account_id in managed_account_ids:
            candidate = LiveAccountFingerprint.from_identity(
                provider="IBKR",
                environment="LIVE",
                account_id=account_id,
                endpoint_identity=endpoint_identity,
            )
            managed_fingerprints.append(candidate)

        selected_fingerprint = (
            LiveAccountFingerprint.from_identity(
                provider="IBKR",
                environment="LIVE",
                account_id=broker_account_id,
                endpoint_identity=endpoint_identity,
            )
            if broker_account_id is not None
            else None
        )
        selected_managed_matches = (
            [item for item in managed_fingerprints if item == selected_fingerprint]
            if selected_fingerprint is not None
            else []
        )
        expected_managed_matches = (
            [item for item in managed_fingerprints if item == expected]
            if expected is not None
            else []
        )

        if expected is None:
            blockers.append(LiveStartupBlocker.EXPECTED_ACCOUNT_UNAVAILABLE)
        if len(managed_account_ids) > 1:
            blockers.append(LiveStartupBlocker.MULTIPLE_MANAGED_ACCOUNTS)
        elif expected is not None and not expected_managed_matches:
            if not managed_account_ids:
                blockers.append(LiveStartupBlocker.EXPECTED_ACCOUNT_UNAVAILABLE)
            else:
                blockers.append(LiveStartupBlocker.ACCOUNT_MISMATCH)
        elif expected is not None and selected_fingerprint != expected:
            blockers.append(LiveStartupBlocker.ACCOUNT_MISMATCH)
        if broker_account_id is None:
            blockers.append(LiveStartupBlocker.BROKER_ACCOUNT_UNAVAILABLE)
        elif not selected_managed_matches:
            blockers.append(LiveStartupBlocker.ACCOUNT_MISMATCH)

        if not account_truth_known:
            blockers.append(LiveStartupBlocker.ACCOUNT_TRUTH_UNKNOWN)
        if account_truth_observed_at is None:
            blockers.append(LiveStartupBlocker.ACCOUNT_TRUTH_UNKNOWN)
        elif not _fresh(account_truth_observed_at, now=now):
            blockers.append(LiveStartupBlocker.ACCOUNT_TRUTH_STALE)
        if not market_truth_known:
            blockers.append(LiveStartupBlocker.MARKET_TRUTH_UNKNOWN)
        if market_truth_observed_at is None:
            blockers.append(LiveStartupBlocker.MARKET_TRUTH_UNKNOWN)
        elif not _fresh(market_truth_observed_at, now=now):
            blockers.append(LiveStartupBlocker.MARKET_TRUTH_STALE)
        if not open_orders_known:
            blockers.append(LiveStartupBlocker.OPEN_ORDERS_UNKNOWN)
        if open_orders_observed_at is None:
            blockers.append(LiveStartupBlocker.OPEN_ORDERS_UNKNOWN)
        elif not _fresh(open_orders_observed_at, now=now):
            blockers.append(LiveStartupBlocker.OPEN_ORDERS_STALE)
        if not positions_known:
            blockers.append(LiveStartupBlocker.POSITIONS_UNKNOWN)
        if positions_observed_at is None:
            blockers.append(LiveStartupBlocker.POSITIONS_UNKNOWN)
        elif not _fresh(positions_observed_at, now=now):
            blockers.append(LiveStartupBlocker.POSITIONS_STALE)
        if reconciliation_observed_at is None:
            blockers.append(LiveStartupBlocker.RECONCILIATION_UNKNOWN)
        elif not _fresh(reconciliation_observed_at, now=now):
            blockers.append(LiveStartupBlocker.RECONCILIATION_STALE)
        if not reconciliation_clean:
            blockers.append(LiveStartupBlocker.RECONCILIATION_UNCLEAN)

        if authorization is None:
            blockers.append(LiveStartupBlocker.AUTHORIZATION_MISSING)
            authorization_valid = False
            limits_valid = False
        else:
            if authorization.revoked_at is not None:
                blockers.append(LiveStartupBlocker.AUTHORIZATION_REVOKED)
            elif not authorization.is_valid_at(now):
                blockers.append(LiveStartupBlocker.AUTHORIZATION_EXPIRED)
            authorization_valid = authorization.is_valid_at(now)
            if expected is not None and (
                selected_fingerprint != expected
                or len(managed_account_ids) != 1
                or len(expected_managed_matches) != 1
                or len(selected_managed_matches) != 1
            ):
                blockers.append(LiveStartupBlocker.AUTHORIZATION_ACCOUNT_MISMATCH)
            limits_valid = not authorization.approved_canary_limits.blockers()
            if not limits_valid:
                blockers.append(LiveStartupBlocker.INVALID_LIMITS)

        if authorization_state.kill_latch.is_latched:
            blockers.append(LiveStartupBlocker.KILL_LATCHED)
        if not authorization_state.session_armed:
            blockers.append(LiveStartupBlocker.SESSION_NOT_ARMED)

        timestamps = (
            connection_observed_at,
            account_truth_observed_at,
            market_truth_observed_at,
            open_orders_observed_at,
            positions_observed_at,
            reconciliation_observed_at,
        )
        expiry_candidates = [now + LIVE_STARTUP_PROOF_TTL]
        expiry_candidates.extend(
            timestamp + LIVE_STARTUP_PROOF_TTL
            for timestamp in timestamps
            if timestamp is not None
        )
        if authorization is not None and authorization.is_valid_at(now):
            expiry_candidates.append(authorization.expires_at)
        proof = object.__new__(cls)
        for name, value in {
            "observed_at": now,
            "expires_at": min(expiry_candidates),
            "endpoint_identity": endpoint_identity,
            "account_fingerprint": (
                selected_fingerprint
                if len(managed_account_ids) == 1
                and selected_fingerprint == expected
                and len(expected_managed_matches) == 1
                and len(selected_managed_matches) == 1
                else None
            ),
            "authorization_fingerprint": (
                None
                if authorization is None
                else authorization.authorization_fingerprint
            ),
            "session_arm_id": authorization_state.session_arm_id,
            "broker_connected": broker_connected,
            "connection_observed_at": connection_observed_at,
            "account_truth_known": account_truth_known,
            "account_truth_observed_at": account_truth_observed_at,
            "market_truth_known": market_truth_known,
            "market_truth_observed_at": market_truth_observed_at,
            "open_orders_known": open_orders_known,
            "open_orders_observed_at": open_orders_observed_at,
            "positions_known": positions_known,
            "positions_observed_at": positions_observed_at,
            "reconciliation_clean": reconciliation_clean,
            "reconciliation_observed_at": reconciliation_observed_at,
            "authorization_valid": authorization_valid,
            "session_armed": authorization_state.session_armed,
            "kill_latched": authorization_state.kill_latch.is_latched,
            "limits_valid": limits_valid,
            "blockers": tuple(dict.fromkeys(blockers)),
        }.items():
            object.__setattr__(proof, name, value)
        return proof


__all__ = [
    "LIVE_STARTUP_PROOF_TTL",
    "LiveEndpointIdentity",
    "LiveStartupBlocker",
    "LiveStartupError",
    "LiveStartupProof",
]
