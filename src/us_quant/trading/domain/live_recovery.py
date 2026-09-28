"""Evidence required to release the durable Stage 4-E recovery barrier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from us_quant.trading.domain.live_safety import LiveAccountFingerprint, LiveSafetyError


@dataclass(frozen=True, slots=True, init=False)
class LiveRecoveryEvidence:
    """One fresh, connected reconciliation snapshot with no unknown facts."""

    observed_at: datetime
    account_fingerprint: LiveAccountFingerprint
    broker_connected: bool
    open_orders_known: bool
    positions_known: bool
    fills_known: bool
    reconciliation_clean: bool
    uncertain_submission_resolved: bool
    reconciled_broker_order_ids: tuple[int, ...]

    def __new__(cls, *args: object, **kwargs: object) -> "LiveRecoveryEvidence":
        raise LiveSafetyError("LiveRecoveryEvidence must be created by capture")

    @classmethod
    def capture(
        cls,
        *,
        observed_at: datetime,
        account_fingerprint: LiveAccountFingerprint,
        broker_connected: bool,
        open_orders_known: bool,
        positions_known: bool,
        fills_known: bool,
        reconciliation_clean: bool,
        uncertain_submission_resolved: bool,
        reconciled_broker_order_ids: tuple[int, ...],
    ) -> "LiveRecoveryEvidence":
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise LiveSafetyError("recovery evidence time must be timezone-aware")
        if not isinstance(account_fingerprint, LiveAccountFingerprint):
            raise LiveSafetyError("recovery evidence requires the exact account fingerprint")
        for name, value in (
            ("broker_connected", broker_connected),
            ("open_orders_known", open_orders_known),
            ("positions_known", positions_known),
            ("fills_known", fills_known),
            ("reconciliation_clean", reconciliation_clean),
            ("uncertain_submission_resolved", uncertain_submission_resolved),
        ):
            if type(value) is not bool:
                raise LiveSafetyError(f"{name} must be a boolean")
        if not isinstance(reconciled_broker_order_ids, tuple) or any(
            type(order_id) is not int or order_id <= 0
            for order_id in reconciled_broker_order_ids
        ):
            raise LiveSafetyError("reconciled broker order ids must be positive integers")
        if len(set(reconciled_broker_order_ids)) != len(reconciled_broker_order_ids):
            raise LiveSafetyError("reconciled broker order ids must not repeat")
        evidence = object.__new__(cls)
        for name, value in locals().copy().items():
            if name in {
                "observed_at",
                "account_fingerprint",
                "broker_connected",
                "open_orders_known",
                "positions_known",
                "fills_known",
                "reconciliation_clean",
                "uncertain_submission_resolved",
                "reconciled_broker_order_ids",
            }:
                object.__setattr__(evidence, name, value)
        return evidence

    @property
    def is_clean(self) -> bool:
        return all(
            (
                self.broker_connected,
                self.open_orders_known,
                self.positions_known,
                self.fills_known,
                self.reconciliation_clean,
                self.uncertain_submission_resolved,
            )
        )


__all__ = ["LiveRecoveryEvidence"]
