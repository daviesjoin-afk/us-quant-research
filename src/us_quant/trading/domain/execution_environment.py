"""Fail-closed deployment decision; feature flags never authorize Live orders."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from us_quant.trading.domain.common import Environment


class ExecutionDeploymentError(RuntimeError):
    """The requested execution deployment has no production broker channel."""


class ExecutionDeploymentBlocker(StrEnum):
    NONE = "none"
    BACKTEST_HAS_NO_BROKER_CHANNEL = "backtest_has_no_broker_channel"
    LIVE_FEATURE_DISABLED = "live_feature_disabled"
    LIVE_ADAPTER_UNAVAILABLE = "live_adapter_unavailable"
    LIVE_CANARY_GATES_REQUIRED = "live_canary_gates_required"


@dataclass(frozen=True, slots=True)
class ExecutionDeploymentDecision:
    environment: Environment
    broker_submission_allowed: bool
    blocker: ExecutionDeploymentBlocker
    reason: str


def evaluate_execution_deployment(
    *,
    environment: Environment,
    live_trading_enabled: bool,
) -> ExecutionDeploymentDecision:
    """Select whether this deployment may construct a broker channel.

    PAPER selects the existing Paper-only adapter. BACKTEST has no broker
    channel. LIVE returns a denial until composition supplies the complete
    authorization, fresh-proof, and canary guard context; the flag alone never
    authorizes it.
    """

    if not isinstance(environment, Environment):
        raise ExecutionDeploymentError("unsupported execution environment")
    if not isinstance(live_trading_enabled, bool):
        raise ExecutionDeploymentError("invalid Live deployment feature flag")

    if environment is Environment.BACKTEST:
        return ExecutionDeploymentDecision(
            environment=environment,
            broker_submission_allowed=False,
            blocker=ExecutionDeploymentBlocker.BACKTEST_HAS_NO_BROKER_CHANNEL,
            reason="backtest does not own a broker execution channel",
        )
    if environment is Environment.PAPER:
        return ExecutionDeploymentDecision(
            environment=environment,
            broker_submission_allowed=True,
            blocker=ExecutionDeploymentBlocker.NONE,
            reason="Paper execution is served by the existing Paper-only adapter",
        )
    if not live_trading_enabled:
        return ExecutionDeploymentDecision(
            environment=environment,
            broker_submission_allowed=False,
            blocker=ExecutionDeploymentBlocker.LIVE_FEATURE_DISABLED,
            reason="Live trading feature flag is disabled",
        )
    return ExecutionDeploymentDecision(
        environment=environment,
        broker_submission_allowed=False,
        blocker=ExecutionDeploymentBlocker.LIVE_CANARY_GATES_REQUIRED,
        reason="Live authorization, startup proof, and canary gates are required",
    )


__all__ = [
    "ExecutionDeploymentBlocker",
    "ExecutionDeploymentDecision",
    "ExecutionDeploymentError",
    "evaluate_execution_deployment",
]
