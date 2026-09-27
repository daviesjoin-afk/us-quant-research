"""Execution composition root.

The only module allowed to know both the execution application and the concrete
adapters it runs against.  Everything above this file -- the window, the
strategy runtime, the session coordinator -- names the ports and the domain
types only, which is what lets the SQLite store and the IBKR channel be replaced
without touching a caller.

Two builders, because the runtime needs the two halves at different moments:

* ``build_order_repository`` opens the store once per window.  It is the same
  file the Paper order journal used, so an existing database keeps its orders.
* ``build_execution_candidate_factory`` binds an immutable deployment decision
  before ``PaperTradingService`` receives a candidate factory.
  ``build_execution_application`` then binds that channel to the store: the
  application is stateless beyond those two references, so binding it at launch
  time cannot drift from the channel the session is actually using.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.execution import IBKRExecutionAdapter
from us_quant.trading.adapters.ibkr.live_execution import IBKRLiveExecutionAdapter
from us_quant.trading.adapters.sqlite.order_repository import (
    SQLiteOrderRepository,
)
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.live_canary_execution import (
    LiveCanaryExecutionGuard,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.execution_environment import (
    ExecutionDeploymentError,
    evaluate_execution_deployment,
)
from us_quant.trading.domain.live_safety import LiveAuthorizationState
from us_quant.trading.domain.live_startup import (
    LiveEndpointIdentity,
    LiveStartupError,
    LiveStartupProof,
)
from us_quant.trading.ports.broker_execution import BrokerExecutionPort
from us_quant.trading.ports.live_canary_truth import LiveCanaryTruthPort
from us_quant.trading.ports.live_safety_repository import LiveSafetyRepositoryPort
from us_quant.trading.ports.order_repository import OrderRepositoryPort


def build_order_repository(path: str | Path) -> SQLiteOrderRepository:
    """Open the order store; the schema is the frozen Paper one."""

    return SQLiteOrderRepository(path)


def _build_paper_execution_candidate(
    config: IBKRConnectionConfig,
    *,
    repository: SQLiteOrderRepository,
    extended_hours_enabled: bool = False,
) -> IBKRExecutionAdapter:
    """Build one IBKR Paper execution channel over the shared order store."""

    return IBKRExecutionAdapter(
        config,
        repository=repository,
        extended_hours_enabled=extended_hours_enabled,
    )


def build_execution_candidate_factory(
    *,
    environment: Environment,
    live_trading_enabled: bool,
) -> Callable[..., BrokerExecutionPort]:
    """Bind environment selection before exposing the Paper service factory.

    A denied deployment raises before adapter construction; it cannot fall back
    to another environment. Live uses its separate guarded factory below, so a
    Paper service can never accidentally receive a Live adapter.
    """

    decision = evaluate_execution_deployment(
        environment=environment,
        live_trading_enabled=live_trading_enabled,
    )

    def build_candidate(
        config: IBKRConnectionConfig,
        *,
        repository: SQLiteOrderRepository,
        extended_hours_enabled: bool = False,
    ) -> BrokerExecutionPort:
        if (
            decision.environment is not Environment.PAPER
            or not decision.broker_submission_allowed
        ):
            raise ExecutionDeploymentError(decision.reason)
        return _build_paper_execution_candidate(
            config,
            repository=repository,
            extended_hours_enabled=extended_hours_enabled,
        )

    return build_candidate


def build_live_execution_candidate_factory(
    *,
    environment: Environment,
    live_trading_enabled: bool,
    live_authorization_state: Callable[[], LiveAuthorizationState] | None = None,
    live_safety_repository: LiveSafetyRepositoryPort | None = None,
    live_startup_proof: Callable[[], LiveStartupProof] | None = None,
    live_truth: LiveCanaryTruthPort | None = None,
) -> Callable[..., BrokerExecutionPort]:
    """Bind a guarded Live channel separately from the Paper runtime factory."""

    decision = evaluate_execution_deployment(
        environment=environment,
        live_trading_enabled=live_trading_enabled,
    )
    if environment is not Environment.LIVE:
        raise ExecutionDeploymentError("Live canary requires environment=LIVE")
    if not live_trading_enabled:
        raise ExecutionDeploymentError(decision.reason)
    if (
        live_authorization_state is None
        or live_safety_repository is None
        or not callable(getattr(live_safety_repository, "execution_lease", None))
        or live_startup_proof is None
        or live_truth is None
        or not callable(getattr(live_truth, "snapshot", None))
    ):
        raise ExecutionDeploymentError(
            "Live construction requires authorization, startup proof, and fresh account truth"
        )

    def build_candidate(
        config: IBKRConnectionConfig,
        *,
        repository: SQLiteOrderRepository,
        extended_hours_enabled: bool = False,
    ) -> BrokerExecutionPort:
        if extended_hours_enabled:
            raise ExecutionDeploymentError(
                "Live canary supports regular-session DAY orders only"
            )
        try:
            LiveEndpointIdentity(host=config.host, port=config.port)
        except (AttributeError, LiveStartupError) as error:
            raise ExecutionDeploymentError(str(error)) from error
        if config.api_read_only or config.paper_order_submission_enabled:
            raise ExecutionDeploymentError(
                "Live canary requires the explicit non-readonly Live Gateway profile"
            )
        try:
            state = live_authorization_state()
        except Exception as error:
            raise ExecutionDeploymentError(
                "Live authorization state is unavailable"
            ) from error
        if not isinstance(state, LiveAuthorizationState):
            raise ExecutionDeploymentError("Live authorization state is invalid")
        authorization = state.authorization
        if authorization is None:
            raise ExecutionDeploymentError(
                "Live construction requires a persistent operator authorization"
            )
        limits = authorization.approved_canary_limits
        broker = IBKRLiveExecutionAdapter(
            config,
            repository=repository,
            expected_account_fingerprint=authorization.expected_account_fingerprint,
            allowed_symbols=frozenset(limits.allowed_symbols),
            allowed_strategy_version_ids=frozenset(
                authorization.approved_strategy_version_ids
            ),
        )
        return LiveCanaryExecutionGuard(
            broker,
            authorization_state=live_authorization_state,
            safety_repository=live_safety_repository,
            startup_proof=live_startup_proof,
            truth=live_truth,
        )

    return build_candidate


def build_execution_application(
    *,
    repository: OrderRepositoryPort,
    broker: BrokerExecutionPort,
) -> ExecutionApplication:
    """Bind the execution application to one channel and store."""

    return ExecutionApplication(repository=repository, broker=broker)


__all__ = [
    "build_execution_application",
    "build_execution_candidate_factory",
    "build_live_execution_candidate_factory",
    "build_order_repository",
]
