"""Durable Live recovery barrier and operator-confirmed reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from us_quant.trading.domain.live_recovery import LiveRecoveryEvidence
from us_quant.trading.domain.live_safety import (
    LiveRecoveryLatch,
    LiveSafetyError,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_startup import LIVE_STARTUP_PROOF_TTL
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyConflict,
    LiveSafetyRepositoryPort,
)


class LiveRecoveryRefused(RuntimeError):
    """Reconciliation evidence or operator confirmation is insufficient."""


class LiveCanaryRecovery:
    """Own recovery-barrier transitions without reconnecting or resubmitting."""

    def __init__(
        self,
        repository: LiveSafetyRepositoryPort,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(timezone.utc))

    def require_reconciliation(
        self, *, reason: str, broker_order_id: int | None = None
    ) -> LiveSafetyRecord:
        at = self._now()
        _require_aware(at)
        if not isinstance(reason, str) or not reason.strip():
            raise LiveSafetyError("recovery reason must not be blank")
        for _ in range(8):
            current = self._repository.load()
            replacement = LiveSafetyRecord(
                current.revision + 1,
                current.authorization,
                current.kill_latch,
                current.recovery_latch.require(
                    at=at, reason=reason, broker_order_id=broker_order_id
                ),
            )
            try:
                self._repository.save(
                    expected_revision=current.revision,
                    replacement=replacement,
                )
                return replacement
            except LiveSafetyConflict:
                continue
        raise LiveRecoveryRefused("Live safety state kept changing during recovery latch")

    def confirm_reconciled(
        self,
        evidence: LiveRecoveryEvidence,
        *,
        operator_confirmed: bool,
    ) -> LiveSafetyRecord:
        if not isinstance(evidence, LiveRecoveryEvidence) or not evidence.is_clean:
            raise LiveRecoveryRefused("fresh clean broker reconciliation evidence is required")
        if type(operator_confirmed) is not bool or not operator_confirmed:
            raise LiveRecoveryRefused("explicit operator confirmation is required")
        now = self._now()
        _require_aware(now)
        if not evidence.observed_at <= now < evidence.observed_at + LIVE_STARTUP_PROOF_TTL:
            raise LiveRecoveryRefused("Live recovery evidence is stale or from the future")
        for _ in range(8):
            current = self._repository.load()
            if current.authorization is None:
                raise LiveRecoveryRefused("recovery cannot be confirmed without authorization")
            if (
                current.authorization.expected_account_fingerprint
                != evidence.account_fingerprint
            ):
                raise LiveRecoveryRefused("recovery account does not match persistent authorization")
            if not current.recovery_latch.is_required:
                raise LiveRecoveryRefused("there is no pending Live recovery barrier")
            if evidence.observed_at <= current.recovery_latch.required_at:
                raise LiveRecoveryRefused(
                    "Live recovery evidence predates the recovery barrier"
                )
            if (
                current.recovery_latch.broker_order_id is not None
                and current.recovery_latch.broker_order_id
                not in evidence.reconciled_broker_order_ids
            ):
                raise LiveRecoveryRefused(
                    "uncertain broker order id is absent from reconciliation evidence"
                )
            replacement = LiveSafetyRecord(
                current.revision + 1,
                current.authorization,
                current.kill_latch,
                current.recovery_latch.clear(),
            )
            try:
                self._repository.save(
                    expected_revision=current.revision,
                    replacement=replacement,
                )
                return replacement
            except LiveSafetyConflict:
                continue
        raise LiveRecoveryRefused("Live safety state kept changing during recovery confirmation")


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LiveSafetyError("recovery clock must be timezone-aware")


__all__ = ["LiveCanaryRecovery", "LiveRecoveryRefused"]
