"""Production composition for the Paper autonomy supervisor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.paper_autonomy_schedule import (
    PaperAutonomyScheduleAdapter,
)
from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.application.paper_autonomy_supervisor import (
    PaperAutonomySupervisor,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyPolicy,
    PaperAutonomySupervisorEvent,
)
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyExecutorPort,
    PaperAutonomyIntentReaderPort,
    PaperAutonomyRuntimeFactsPort,
    PaperAutonomyStartupFactsPort,
)


@dataclass(frozen=True, slots=True)
class PaperAutonomySupervisorComposition:
    supervisor: PaperAutonomySupervisor
    actions: SQLitePaperAutonomyActionRepository
    schedule: PaperAutonomyScheduleAdapter


def build_paper_autonomy_action_repository(
    database_path: str | Path,
) -> SQLitePaperAutonomyActionRepository:
    """Construct the sole production action ledger adapter."""

    return SQLitePaperAutonomyActionRepository(database_path)


def build_paper_autonomy_supervisor(
    *,
    intent: PaperAutonomyIntentReaderPort,
    runtime_facts: PaperAutonomyRuntimeFactsPort,
    startup_facts: PaperAutonomyStartupFactsPort,
    executor: PaperAutonomyExecutorPort,
    policy: PaperAutonomyPolicy,
    action_database_path: str | Path | None = None,
    actions: SQLitePaperAutonomyActionRepository | None = None,
    emit: Callable[[PaperAutonomySupervisorEvent], None] | None = None,
) -> PaperAutonomySupervisorComposition:
    """Construct the supervisor and its production persistence/calendar adapters."""

    actions = actions or (
        SQLitePaperAutonomyActionRepository(action_database_path)
        if action_database_path is not None
        else None
    )
    if actions is None:
        raise ValueError("an action repository or database path is required")
    schedule = PaperAutonomyScheduleAdapter(policy)
    supervisor = PaperAutonomySupervisor(
        intent=intent,
        runtime_facts=runtime_facts,
        startup_facts=startup_facts,
        schedule=schedule,
        actions=actions,
        executor=executor,
        policy=policy,
        emit=emit,
    )
    return PaperAutonomySupervisorComposition(
        supervisor=supervisor,
        actions=actions,
        schedule=schedule,
    )


__all__ = [
    "PaperAutonomySupervisorComposition",
    "build_paper_autonomy_action_repository",
    "build_paper_autonomy_supervisor",
]
