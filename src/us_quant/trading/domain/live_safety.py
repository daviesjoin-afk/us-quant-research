"""Stage 4-A Live safety values.

This module contains only operator authorization, its exact account binding,
canary limits, the persistent kill latch, and the per-process session arm. It
does not construct or authorize a broker channel. In particular, the session
arm is deliberately absent from :class:`LiveSafetyRecord`, the value accepted
by the persistence port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from uuid import uuid4


class LiveSafetyError(ValueError):
    """Base class for invalid Live safety values."""


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LiveSafetyError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LiveAccountFingerprint:
    """Non-secret digest binding authorization to one exact Live account."""

    sha256: str
    masked_account: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise LiveSafetyError("account fingerprint must be a SHA-256 digest")
        if (
            not self.masked_account.startswith("…")
            or len(self.masked_account) > 5
            or any(character.isspace() for character in self.masked_account[1:])
        ):
            raise LiveSafetyError("account display must be masked to at most four characters")

    @classmethod
    def from_identity(
        cls,
        *,
        provider: str,
        environment: str,
        account_id: str,
        endpoint_identity: str,
    ) -> "LiveAccountFingerprint":
        """Build a stable digest without retaining the raw account identifier."""

        parts = (provider, environment, account_id, endpoint_identity)
        if any(not isinstance(part, str) or not part.strip() for part in parts):
            raise LiveSafetyError("account identity fields must not be blank")
        if environment.strip().casefold() != "live":
            raise LiveSafetyError("a Live fingerprint requires environment=live")
        normalized = [
            provider.strip().casefold(),
            "live",
            account_id.strip(),
            endpoint_identity.strip().casefold(),
        ]
        encoded = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
        account = account_id.strip()
        masked = f"…{account[-4:]}" if len(account) > 4 else "…"
        return cls(hashlib.sha256(encoded.encode("utf-8")).hexdigest(), masked)


@dataclass(frozen=True, slots=True)
class LiveCanaryLimits:
    """Explicit, finite exposure caps; defaults grant no usable allowance."""

    capital_limit: Decimal = Decimal("0")
    max_order_notional: Decimal = Decimal("0")
    max_daily_loss: Decimal = Decimal("0")
    max_positions: int = 0
    max_open_orders: int = 0
    allowed_symbols: tuple[str, ...] = ()
    allowed_strategy_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("capital_limit", "max_order_notional", "max_daily_loss"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise LiveSafetyError(f"{name} must be an explicit Decimal")
        if type(self.max_positions) is not int or type(self.max_open_orders) is not int:
            raise LiveSafetyError("position and order caps must be integers")
        for name in ("allowed_symbols", "allowed_strategy_versions"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise LiveSafetyError(f"{name} must be a tuple of nonblank strings")
            if len(set(value)) != len(value):
                raise LiveSafetyError(f"{name} must not contain duplicates")

    def blockers(self) -> tuple["LiveArmBlocker", ...]:
        blockers: list[LiveArmBlocker] = []
        for name, value in (
            ("capital_limit", self.capital_limit),
            ("max_order_notional", self.max_order_notional),
            ("max_daily_loss", self.max_daily_loss),
        ):
            if not value.is_finite() or value <= 0:
                blockers.append(LiveArmBlocker.INVALID_LIMITS)
                break
        if self.max_positions <= 0 or self.max_open_orders <= 0:
            blockers.append(LiveArmBlocker.INVALID_LIMITS)
        if not self.allowed_symbols or not self.allowed_strategy_versions:
            blockers.append(LiveArmBlocker.INVALID_LIMITS)
        return tuple(dict.fromkeys(blockers))


@dataclass(frozen=True, slots=True)
class LiveOperatorAuthorization:
    """Persistent human permission for one account, strategy set, and limits."""

    authorization_id: str
    created_at: datetime
    expires_at: datetime
    expected_account_fingerprint: LiveAccountFingerprint
    approved_strategy_version_ids: tuple[str, ...]
    approved_canary_limits: LiveCanaryLimits
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.authorization_id.strip():
            raise LiveSafetyError("authorization_id must not be blank")
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise LiveSafetyError("authorization must expire after it is created")
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")
            if self.revoked_at < self.created_at:
                raise LiveSafetyError("revoked_at cannot precede created_at")
        if not isinstance(self.expected_account_fingerprint, LiveAccountFingerprint):
            raise LiveSafetyError("authorization requires an account fingerprint")
        if not isinstance(self.approved_canary_limits, LiveCanaryLimits):
            raise LiveSafetyError("authorization requires explicit canary limits")
        if not self.approved_strategy_version_ids or any(
            not isinstance(item, str) or not item.strip()
            for item in self.approved_strategy_version_ids
        ):
            raise LiveSafetyError("authorization requires approved strategy versions")
        if len(set(self.approved_strategy_version_ids)) != len(
            self.approved_strategy_version_ids
        ):
            raise LiveSafetyError("approved strategy versions must not repeat")

    def is_valid_at(self, now: datetime) -> bool:
        _aware(now, "now")
        return self.revoked_at is None and self.created_at <= now < self.expires_at

    @property
    def authorization_fingerprint(self) -> str:
        """Digest the full authorization so startup proof cannot outlive edits."""

        limits = self.approved_canary_limits
        payload = {
            "authorization_id": self.authorization_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": (
                None if self.revoked_at is None else self.revoked_at.isoformat()
            ),
            "account_fingerprint": self.expected_account_fingerprint.sha256,
            "approved_strategy_version_ids": list(self.approved_strategy_version_ids),
            "limits": {
                "capital_limit": str(limits.capital_limit),
                "max_order_notional": str(limits.max_order_notional),
                "max_daily_loss": str(limits.max_daily_loss),
                "max_positions": limits.max_positions,
                "max_open_orders": limits.max_open_orders,
                "allowed_symbols": list(limits.allowed_symbols),
                "allowed_strategy_versions": list(
                    limits.allowed_strategy_versions
                ),
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class LiveOperation(StrEnum):
    INCREASE_EXPOSURE = "increase_exposure"
    READ_BROKER_TRUTH = "read_broker_truth"
    RECONCILE = "reconcile"
    CANCEL_TRACKED_ORDER = "cancel_tracked_order"
    REDUCE_POSITION = "reduce_position"
    DISCONNECT = "disconnect"
    FINALIZE = "finalize"


@dataclass(frozen=True, slots=True)
class LiveKillLatch:
    """Persistent stop for new exposure; safety and cleanup actions remain valid."""

    latched_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.latched_at is None) != (self.reason is None):
            raise LiveSafetyError("kill latch time and reason must be set together")
        if self.latched_at is not None:
            _aware(self.latched_at, "latched_at")
            if not self.reason or not self.reason.strip():
                raise LiveSafetyError("kill latch reason must not be blank")

    @property
    def is_latched(self) -> bool:
        return self.latched_at is not None

    def blocks(self, operation: LiveOperation) -> bool:
        if not self.is_latched:
            return False
        return operation not in {
            LiveOperation.READ_BROKER_TRUTH,
            LiveOperation.RECONCILE,
            LiveOperation.CANCEL_TRACKED_ORDER,
            LiveOperation.REDUCE_POSITION,
            LiveOperation.DISCONNECT,
            LiveOperation.FINALIZE,
        }

    def engage(self, *, at: datetime, reason: str) -> "LiveKillLatch":
        _aware(at, "at")
        if not reason.strip():
            raise LiveSafetyError("kill latch requires an operator reason")
        return LiveKillLatch(at, reason.strip())

    def clear(self) -> "LiveKillLatch":
        """Release the durable latch; this never restores the session arm."""

        return LiveKillLatch()


@dataclass(frozen=True, slots=True)
class LiveRecoveryLatch:
    """Durable barrier requiring fresh reconciliation after an unsafe event."""

    required_at: datetime | None = None
    reason: str | None = None
    broker_order_id: int | None = None

    def __post_init__(self) -> None:
        if (self.required_at is None) != (self.reason is None):
            raise LiveSafetyError("recovery time and reason must be set together")
        if self.required_at is not None:
            _aware(self.required_at, "required_at")
            if not self.reason or not self.reason.strip():
                raise LiveSafetyError("recovery reason must not be blank")
        if self.broker_order_id is not None and (
            type(self.broker_order_id) is not int or self.broker_order_id <= 0
        ):
            raise LiveSafetyError("recovery broker order id must be a positive integer")

    @property
    def is_required(self) -> bool:
        return self.required_at is not None

    def require(
        self,
        *,
        at: datetime,
        reason: str,
        broker_order_id: int | None = None,
    ) -> "LiveRecoveryLatch":
        _aware(at, "at")
        if not isinstance(reason, str) or not reason.strip():
            raise LiveSafetyError("recovery barrier requires a reason")
        return LiveRecoveryLatch(at, reason.strip(), broker_order_id)

    def clear(self) -> "LiveRecoveryLatch":
        return LiveRecoveryLatch()


class LiveArmBlocker(StrEnum):
    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    AUTHORIZATION_REVOKED = "authorization_revoked"
    ACCOUNT_MISMATCH = "account_mismatch"
    STRATEGY_NOT_APPROVED = "strategy_not_approved"
    INVALID_LIMITS = "invalid_limits"
    KILL_LATCHED = "kill_latched"
    RECOVERY_REQUIRED = "recovery_required"
    OPERATOR_CONFIRMATION_REQUIRED = "operator_confirmation_required"


@dataclass(frozen=True, slots=True)
class LiveAuthorizationState:
    """Process-local state; construction and restart always begin unarmed."""

    authorization: LiveOperatorAuthorization | None = None
    kill_latch: LiveKillLatch = LiveKillLatch()
    recovery_latch: LiveRecoveryLatch = LiveRecoveryLatch()
    session_armed: bool = field(default=False, init=False)
    session_arm_id: str | None = field(default=None, init=False, repr=False)
    session_arm_revision: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.authorization is not None and not isinstance(
            self.authorization, LiveOperatorAuthorization
        ):
            raise LiveSafetyError("authorization has an invalid type")
        if not isinstance(self.kill_latch, LiveKillLatch):
            raise LiveSafetyError("kill_latch has an invalid type")
        if not isinstance(self.recovery_latch, LiveRecoveryLatch):
            raise LiveSafetyError("recovery_latch has an invalid type")

    def arm_blockers(
        self,
        *,
        now: datetime,
        account_fingerprint: LiveAccountFingerprint,
        strategy_version_id: str,
        operator_confirmed: bool,
    ) -> tuple[LiveArmBlocker, ...]:
        _aware(now, "now")
        if not isinstance(account_fingerprint, LiveAccountFingerprint):
            raise LiveSafetyError("account_fingerprint has an invalid type")
        if not isinstance(strategy_version_id, str) or not strategy_version_id.strip():
            raise LiveSafetyError("strategy_version_id must not be blank")
        if type(operator_confirmed) is not bool:
            raise LiveSafetyError("operator_confirmed must be a boolean")
        blockers: list[LiveArmBlocker] = []
        authorization = self.authorization
        if authorization is None:
            blockers.append(LiveArmBlocker.AUTHORIZATION_MISSING)
        else:
            if authorization.revoked_at is not None:
                blockers.append(LiveArmBlocker.AUTHORIZATION_REVOKED)
            elif not authorization.is_valid_at(now):
                blockers.append(LiveArmBlocker.AUTHORIZATION_EXPIRED)
            if authorization.expected_account_fingerprint != account_fingerprint:
                blockers.append(LiveArmBlocker.ACCOUNT_MISMATCH)
            if (
                strategy_version_id not in authorization.approved_strategy_version_ids
                or strategy_version_id
                not in authorization.approved_canary_limits.allowed_strategy_versions
            ):
                blockers.append(LiveArmBlocker.STRATEGY_NOT_APPROVED)
            blockers.extend(authorization.approved_canary_limits.blockers())
        if self.kill_latch.is_latched:
            blockers.append(LiveArmBlocker.KILL_LATCHED)
        if self.recovery_latch.is_required:
            blockers.append(LiveArmBlocker.RECOVERY_REQUIRED)
        if not operator_confirmed:
            blockers.append(LiveArmBlocker.OPERATOR_CONFIRMATION_REQUIRED)
        return tuple(dict.fromkeys(blockers))

    def request_session_arm(
        self, *, safety_revision: int, **facts: object
    ) -> "LiveAuthorizationState":
        """Arm once against the exact persisted revision approved by the operator."""

        if type(safety_revision) is not int or safety_revision < 1:
            raise LiveSafetyError("safety_revision must be a persisted positive revision")
        blockers = self.arm_blockers(**facts)  # type: ignore[arg-type]
        if blockers:
            raise LiveArmRefused(blockers)
        armed = LiveAuthorizationState(
            self.authorization, self.kill_latch, self.recovery_latch
        )
        object.__setattr__(armed, "session_armed", True)
        object.__setattr__(armed, "session_arm_id", uuid4().hex)
        object.__setattr__(armed, "session_arm_revision", safety_revision)
        return armed

    def after_restart(self) -> "LiveAuthorizationState":
        """Rebuild process-local state from durable facts without an arm."""

        return LiveAuthorizationState(
            self.authorization, self.kill_latch, self.recovery_latch
        )

    def engage_kill(self, *, at: datetime, reason: str) -> "LiveAuthorizationState":
        """Latch the kill and drop this process's arm in the same value change."""

        return LiveAuthorizationState(
            self.authorization,
            self.kill_latch.engage(at=at, reason=reason),
            self.recovery_latch,
        )


class LiveArmRefused(LiveSafetyError):
    def __init__(self, blockers: tuple[LiveArmBlocker, ...]) -> None:
        self.blockers = blockers
        super().__init__("Live session arm refused: " + ", ".join(blockers))


@dataclass(frozen=True, slots=True)
class LiveSafetyRecord:
    """Only persistent Live facts; session_armed is intentionally not a field."""

    revision: int = 0
    authorization: LiveOperatorAuthorization | None = None
    kill_latch: LiveKillLatch = LiveKillLatch()
    recovery_latch: LiveRecoveryLatch = LiveRecoveryLatch()

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise LiveSafetyError("Live safety revision must be a nonnegative integer")
        if self.authorization is not None and not isinstance(
            self.authorization, LiveOperatorAuthorization
        ):
            raise LiveSafetyError("authorization has an invalid type")
        if not isinstance(self.kill_latch, LiveKillLatch):
            raise LiveSafetyError("kill_latch has an invalid type")
        if not isinstance(self.recovery_latch, LiveRecoveryLatch):
            raise LiveSafetyError("recovery_latch has an invalid type")


__all__ = [
    "LiveAccountFingerprint",
    "LiveArmBlocker",
    "LiveArmRefused",
    "LiveAuthorizationState",
    "LiveCanaryLimits",
    "LiveKillLatch",
    "LiveRecoveryLatch",
    "LiveOperation",
    "LiveOperatorAuthorization",
    "LiveSafetyError",
    "LiveSafetyRecord",
]
