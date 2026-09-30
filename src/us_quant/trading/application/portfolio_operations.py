"""Fail-closed operations gate for the portfolio runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Callable
from uuid import uuid4

from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioStrategyAllocation,
)
from us_quant.trading.domain.portfolio_ledger import PortfolioStoreUnreadable
from us_quant.trading.domain.portfolio_runtime import PortfolioCycleResult
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.domain.portfolio_operations import (
    PortfolioOperatingPlan,
    PortfolioPlanAuditEvent,
)
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus
from us_quant.trading.ports.portfolio_operating_plan import (
    PortfolioOperatingPlanConflict,
    PortfolioOperatingPlanRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class PortfolioExecutionGates:
    """Fresh safety facts required before a portfolio cycle may submit."""

    reconciliation_clear: bool
    execution_clear: bool
    paper_clear: bool
    live_kill_clear: bool
    live_recovery_clear: bool
    ledger_readable: bool

    @property
    def may_open_exposure(self) -> bool:
        return all((
            self.reconciliation_clear,
            self.execution_clear,
            self.paper_clear,
            self.live_kill_clear,
            self.live_recovery_clear,
            self.ledger_readable,
        ))


@dataclass(frozen=True, slots=True)
class PortfolioCycleRequest:
    portfolio_cycle_id: str
    observed_at: datetime
    proposal_cutoff: datetime
    snapshot_identity: str
    policy: PortfolioCapitalPolicy
    policy_identity: str
    policy_revision: str
    selected_version_ids: frozenset[str]


class PortfolioOperationsApplication:
    """One operations facade for one account's already-composed runtime.

    It owns no portfolio facts and never creates a second allocator/runtime.  A
    cycle is admitted only when all independent safety owners report clear.
    """

    def __init__(
        self,
        *,
        runtime: PortfolioRuntime,
        repository: PortfolioStateRepositoryPort,
    ) -> None:
        if not isinstance(runtime, PortfolioRuntime):
            raise TypeError("runtime must be PortfolioRuntime")
        self._runtime = runtime
        self._repository = repository

    @property
    def runtime(self) -> PortfolioRuntime:
        """The single runtime this account's composition supplied."""

        return self._runtime

    def evaluate_cycle(
        self,
        *,
        gates: PortfolioExecutionGates,
        request: PortfolioCycleRequest,
    ) -> PortfolioCycleResult | None:
        """Run an admitted cycle; closed gates produce no allocation or action."""

        if not isinstance(gates, PortfolioExecutionGates):
            raise TypeError("gates must be PortfolioExecutionGates")
        if not gates.may_open_exposure:
            return None
        try:
            # Check durable state before reaching proposal, Risk, or dispatch.
            self._repository.decisions()
        except Exception as error:  # noqa: BLE001 - unreadable is a hard stop
            raise PortfolioStoreUnreadable("portfolio ledger is unreadable") from error
        return self._runtime.evaluate_cycle(
            portfolio_cycle_id=request.portfolio_cycle_id,
            observed_at=request.observed_at,
            proposal_cutoff=request.proposal_cutoff,
            snapshot_identity=request.snapshot_identity,
            policy=request.policy,
            policy_identity=request.policy_identity,
            policy_revision=request.policy_revision,
            selected_version_ids=request.selected_version_ids,
        )


class PortfolioOperatingPlanApplication:
    """Validates governed selections and stores operator plans with CAS audit."""

    def __init__(
        self,
        *,
        repository: PortfolioOperatingPlanRepositoryPort,
        strategies: StrategyApplication,
        active_session: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._strategies = strategies
        self._active_session = active_session or (lambda: False)

    def load(self) -> PortfolioOperatingPlan:
        plan = self._repository.load()
        if plan is None:
            raise PortfolioPlanRefused("no portfolio operating plan is configured")
        self._validate(plan.selected_version_ids, plan.policy)
        return plan

    def selected_versions(
        self, plan: PortfolioOperatingPlan | None = None
    ) -> tuple:
        """Read the exact governed versions a frozen plan names."""

        current = plan or self.load()
        versions = tuple(self._strategies.list_versions())
        self._validate(current.selected_version_ids, current.policy, catalogue=versions)
        by_id = {item.version_id: item for item in versions}
        return tuple(by_id[version_id] for version_id in current.selected_version_ids)

    def selected_versions_for_ids(self, version_ids: tuple[str, ...]) -> tuple:
        """Return versions only when the ids still match the durable plan."""

        current = self.load()
        if tuple(version_ids) != current.selected_version_ids:
            raise PortfolioPlanRefused("requested Paper strategies no longer match the portfolio plan")
        return self.selected_versions(current)

    def editor_state(self) -> tuple[tuple[str, ...], dict[str, object] | None]:
        """Return display-only options and editable values for the Desktop page."""

        versions = self._strategies.list_versions()
        options = tuple(
            f"{item.version_id} / {item.status.value} / {item.mode.value} / gate={'PASS' if item.gate_passed else 'BLOCKED'} / worker={'READY' if item.strategy_id == 'intraday-auto-rotation' else 'UNAVAILABLE'}"
            for item in sorted(versions, key=lambda value: value.version_id)
        )
        plan = self._repository.load()
        if plan is None:
            return options, None
        policy = plan.policy
        limits = (
            policy.total_capital_limit, policy.max_gross_exposure,
            policy.max_net_exposure, policy.max_single_position_notional,
            policy.max_symbol_concentration, policy.max_strategy_concentration,
            policy.max_positions, policy.max_open_orders,
        )
        allocations = tuple(
            (item.strategy_version_id, item.capital_weight, item.max_capital,
             item.max_gross_exposure, str(item.enabled).lower())
            for item in policy.allocations
        )
        return options, {
            "revision": plan.revision,
            "selected_version_ids": plan.selected_version_ids,
            "limits": limits,
            "allocations": allocations,
        }

    def save_editor(self, command: object) -> PortfolioOperatingPlan:
        """Parse the Page's raw form values, then run the canonical validation."""

        if not isinstance(command, dict):
            raise PortfolioPlanRefused("portfolio plan form is invalid")
        try:
            expected_revision = int(command["expected_revision"])
            selected = tuple(sorted({item.strip() for item in command["selected_version_ids"].split(",") if item.strip()}))
            raw_limits = tuple(item.strip() for item in command["limits"].split("|"))
            if len(raw_limits) != 8:
                raise ValueError("exactly eight portfolio hard limits are required")
            allocations = []
            for line in command["allocations"].splitlines():
                if not line.strip():
                    continue
                parts = tuple(item.strip() for item in line.split("|"))
                if len(parts) != 5 or parts[4].lower() not in {"true", "false"}:
                    raise ValueError("allocation rows require id|weight|capital|gross|enabled")
                allocations.append(PortfolioStrategyAllocation(
                    strategy_version_id=parts[0],
                    capital_weight=Decimal(parts[1]),
                    max_capital=Decimal(parts[2]),
                    max_gross_exposure=Decimal(parts[3]),
                    enabled=parts[4].lower() == "true",
                ))
            policy = PortfolioCapitalPolicy(
                total_capital_limit=Decimal(raw_limits[0]),
                max_gross_exposure=Decimal(raw_limits[1]),
                max_net_exposure=Decimal(raw_limits[2]),
                max_single_position_notional=Decimal(raw_limits[3]),
                max_symbol_concentration=Decimal(raw_limits[4]),
                max_strategy_concentration=Decimal(raw_limits[5]),
                max_positions=int(raw_limits[6]),
                max_open_orders=int(raw_limits[7]),
                allocations=tuple(allocations),
            )
            reason = str(command["operator_reason"])
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise PortfolioPlanRefused(f"portfolio plan form is invalid: {error}") from error
        return self.save(
            selected_version_ids=selected,
            policy=policy,
            expected_revision=expected_revision,
            operator_reason=reason,
        )

    def save(
        self,
        *,
        selected_version_ids: tuple[str, ...],
        policy: PortfolioCapitalPolicy,
        expected_revision: int,
        operator_reason: str,
        changed_at: datetime | None = None,
    ) -> PortfolioOperatingPlan:
        if self._active_session():
            raise PortfolioPlanRefused("portfolio plan cannot change during an active Paper session")
        self._validate(selected_version_ids, policy)
        if not isinstance(operator_reason, str) or not operator_reason.strip():
            raise PortfolioPlanRefused("operator reason is required")
        if re.search(r"(?i)password|credential|secret|api[_ -]?key|token|\bDU\d{6,}\b", operator_reason):
            raise PortfolioPlanRefused("operator reason must not contain credentials or raw account identifiers")
        now = changed_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise PortfolioPlanRefused("plan timestamp must be timezone-aware")
        current = self._repository.load()
        current_revision = 0 if current is None else current.revision
        if type(expected_revision) is not int or expected_revision != current_revision:
            raise PortfolioOperatingPlanConflict("portfolio plan changed; stale update was refused")
        selected = tuple(sorted(selected_version_ids))
        plan = PortfolioOperatingPlan(
            plan_id=current.plan_id if current else uuid4().hex,
            revision=current_revision + 1,
            selected_version_ids=selected,
            policy=policy,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        limits = tuple(
            (item.strategy_version_id, str(item.capital_weight), str(item.max_capital), str(item.max_gross_exposure), item.enabled)
            for item in sorted(policy.allocations, key=lambda value: value.strategy_version_id)
        )
        audit = PortfolioPlanAuditEvent(
            plan_id=plan.plan_id,
            revision=plan.revision,
            changed_at=now,
            selected_version_ids=selected,
            policy_limits=(
                str(policy.total_capital_limit),
                str(policy.max_gross_exposure),
                str(policy.max_net_exposure),
                str(policy.max_single_position_notional),
                str(policy.max_symbol_concentration),
                str(policy.max_strategy_concentration),
                str(policy.max_positions),
                str(policy.max_open_orders),
            ),
            allocation_limits=limits,
            operator_reason=operator_reason.strip(),
        )
        return self._repository.save(plan, expected_revision=current_revision, audit=audit)

    def _validate(
        self,
        selected_version_ids: tuple[str, ...],
        policy: PortfolioCapitalPolicy,
        *,
        catalogue: tuple | None = None,
    ) -> None:
        if not isinstance(selected_version_ids, tuple) or not selected_version_ids:
            raise PortfolioPlanRefused("select at least one governed Paper strategy")
        if len(set(selected_version_ids)) != len(selected_version_ids):
            raise PortfolioPlanRefused("selected strategy versions must be unique")
        if not isinstance(policy, PortfolioCapitalPolicy) or not policy.is_configured:
            raise PortfolioPlanRefused("portfolio hard capital limits must all be configured")
        versions = {
            item.version_id: item
            for item in (catalogue if catalogue is not None else self._strategies.list_versions())
        }
        for version_id in selected_version_ids:
            version = versions.get(version_id)
            if version is None:
                raise PortfolioPlanRefused(f"selected strategy version does not exist: {version_id}")
            if version.status is not StrategyStatus.PAPER_SHADOW or version.mode is not StrategyMode.PAPER_SHADOW:
                raise PortfolioPlanRefused(f"selected strategy version is not governed for Paper: {version_id}")
            # TODO(6-C): this is the launch boundary where instruction 25 wants the
            # lifecycle authorization checked.  The check is not wired yet, so the
            # legacy flag is still consulted here -- which means an
            # evidence-authorised promotion (gate_passed stays False) is currently
            # refused.  See ``PaperLaunchAuthorizer`` for the intended replacement;
            # wiring it also requires updating the desktop paper-wiring fixtures,
            # which build plans for versions that have no lifecycle decision.
            if not version.gate_passed:
                raise PortfolioPlanRefused(f"selected strategy version is not governed for Paper: {version_id}")
            if version.strategy_id != "intraday-auto-rotation":
                raise PortfolioPlanRefused(
                    f"no production Paper signal worker is registered for strategy family: {version.strategy_id}"
                )
            allocation = policy.allocation_for(version_id)
            if allocation is None or not allocation.enabled or allocation.capital_weight <= 0 or allocation.max_capital <= 0 or allocation.max_gross_exposure <= 0:
                raise PortfolioPlanRefused(f"selected strategy lacks an enabled positive allocation: {version_id}")


class PortfolioPlanRefused(RuntimeError):
    """An operating plan is missing or violates a Paper launch constraint."""

__all__ = [
    "PortfolioCycleRequest",
    "PortfolioExecutionGates",
    "PortfolioOperatingPlanApplication",
    "PortfolioPlanRefused",
    "PortfolioOperationsApplication",
]
