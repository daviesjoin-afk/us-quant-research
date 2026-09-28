"""Immutable Live operator panel view model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveOperatorControlView:
    """Current Live safety facts for the operator panel."""

    environment: str
    feature_flag: str
    bound_account: str
    fingerprint: str
    authorization: str
    session_arm: str
    kill_latch: str
    startup_proof: str
    reconciliation: str
    capital_limit: str
    order_limit: str
    daily_loss_limit: str
    position_limit: str
    open_order_limit: str
    allowed_strategies: str
    allowed_symbols: str
    broker_connection: str
    arm_enabled: bool
    arm_block_reason: str


__all__ = ["LiveOperatorControlView"]
