"""Fail-closed decision for the production execution deployment.

The feature flag is a deployment setting only. It never grants Live order
authority: Stage 3 has no Live adapter or Live authorization boundary.
"""

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

    PAPER always selects the existing Paper-only adapter. BACKTEST has no
    broker channel. LIVE remains unavailable regardless of the feature flag.
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
        blocker=ExecutionDeploymentBlocker.LIVE_ADAPTER_UNAVAILABLE,
        reason="Live execution is not implemented in Stage 3",
    )


__all__ = [
    "ExecutionDeploymentBlocker",
    "ExecutionDeploymentDecision",
    "ExecutionDeploymentError",
    "evaluate_execution_deployment",
]
