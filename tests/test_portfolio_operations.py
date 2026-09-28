from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from us_quant.trading.application.portfolio_operations import (
    PortfolioCycleRequest,
    PortfolioExecutionGates,
    PortfolioOperationsApplication,
)
from us_quant.desktop_v2.orchestration.execution.portfolio_projection import (
    build_portfolio_operations_view,
    build_unavailable_portfolio_operations_view,
)
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioCapitalPolicy,
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
    PortfolioVerdict,
)
from us_quant.trading.domain.portfolio_ledger import PortfolioDecisionRecord
from us_quant.trading.domain.portfolio_ledger import PortfolioStoreUnreadable
from us_quant.trading.composition.runtime import PortfolioRuntimeRegistry
from us_quant.trading.adapters.sqlite.portfolio_operating_plan_repository import SQLitePortfolioOperatingPlanRepository
from us_quant.trading.application.portfolio_operations import PortfolioOperatingPlanApplication, PortfolioPlanRefused
from us_quant.trading.domain.strategy import StrategyDefinition, StrategyIdentity, StrategyMode, StrategyStatus, StrategyVersion, parameter_hash_for
from us_quant.trading.ports.portfolio_operating_plan import PortfolioOperatingPlanConflict
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan
from us_quant.desktop_v2.orchestration.paper.queries import freeze_portfolio_plan, portfolio_plan_matches
from us_quant.desktop_v2.orchestration.paper.models import PaperOrderChannel
from us_quant.desktop_v2.orchestration.paper.queries import freeze_launch
from us_quant.trading.runtime.models import AutoQuantCandidate


def _governed_version(version_id: str, *, status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW, gate_passed=True):
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    return StrategyVersion(
        definition=StrategyDefinition("intraday-auto-rotation", version_id, "test"),
        identity=StrategyIdentity("intraday-auto-rotation", version_id, parameter_hash_for({})),
        semver="1.0.0", status=status, mode=mode, parameters={},
        universe_hash="u", code_hash="c", risk_budget_pct=Decimal("0.01"),
        gate_passed=gate_passed, gate_reason="test", created_at=now, updated_at=now,
    )


def _configured_policy(version_ids=("strategy-a",)):
    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"), max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"), max_single_position_notional=Decimal("500"),
        max_symbol_concentration=Decimal("1"), max_strategy_concentration=Decimal("1"),
        max_positions=10, max_open_orders=10,
        allocations=tuple(PortfolioStrategyAllocation(
            version_id, Decimal("1") / len(version_ids), Decimal("1000") / len(version_ids),
            Decimal("1000") / len(version_ids), True
        ) for version_id in version_ids),
    )


class _GovernedStrategies:
    def __init__(self, versions):
        self.versions = tuple(versions)

    def list_versions(self):
        return self.versions


class _Repository:
    def __init__(self) -> None:
        self.reads = 0

    def decisions(self):
        self.reads += 1
        return ()

    def execution_attributions(self):
        return ()


def _runtime() -> PortfolioRuntime:
    return object.__new__(PortfolioRuntime)


def _request() -> PortfolioCycleRequest:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    return PortfolioCycleRequest(
        portfolio_cycle_id="cycle-1",
        observed_at=now,
        proposal_cutoff=now,
        snapshot_identity="snapshot-1",
        policy=PortfolioCapitalPolicy(),
        policy_identity="policy",
        policy_revision="1",
        selected_version_ids=frozenset(),
    )


def test_closed_halt_gate_never_reaches_portfolio_runtime() -> None:
    repo = _Repository()
    app = PortfolioOperationsApplication(runtime=_runtime(), repository=repo)
    gates = PortfolioExecutionGates(
        reconciliation_clear=True,
        execution_clear=True,
        paper_clear=False,
        live_kill_clear=True,
        live_recovery_clear=True,
        ledger_readable=True,
    )

    assert app.evaluate_cycle(gates=gates, request=_request()) is None
    assert repo.reads == 0


@pytest.mark.parametrize(
    "field",
    (
        "reconciliation_clear", "execution_clear", "paper_clear",
        "live_kill_clear", "live_recovery_clear", "ledger_readable",
    ),
)
def test_every_independent_halt_blocks_new_exposure(field: str) -> None:
    values = {
        "reconciliation_clear": True,
        "execution_clear": True,
        "paper_clear": True,
        "live_kill_clear": True,
        "live_recovery_clear": True,
        "ledger_readable": True,
    }
    values[field] = False
    assert not PortfolioExecutionGates(**values).may_open_exposure


def test_open_gate_reads_durable_ledger_before_cycle() -> None:
    runtime = _runtime()

    def cycle(**_kwargs):
        return "cycle"

    runtime.evaluate_cycle = cycle  # type: ignore[method-assign]
    repo = _Repository()
    app = PortfolioOperationsApplication(runtime=runtime, repository=repo)
    gates = PortfolioExecutionGates(True, True, True, True, True, True)

    assert app.evaluate_cycle(gates=gates, request=_request()) == "cycle"
    assert repo.reads == 1


def test_unreadable_ledger_fails_closed_before_runtime():
    class BrokenRepository(_Repository):
        def decisions(self):
            raise OSError("database unavailable")

    app = PortfolioOperationsApplication(runtime=_runtime(), repository=BrokenRepository())
    with pytest.raises(PortfolioStoreUnreadable):
        app.evaluate_cycle(
            gates=PortfolioExecutionGates(True, True, True, True, True, True),
            request=_request(),
        )


def test_operations_view_contains_allocations_reconciliation_and_attribution():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    policy = PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        allocations=(PortfolioStrategyAllocation(
            "strategy-a", Decimal("1"), Decimal("1000"), Decimal("900"), True
        ),),
    )
    snapshot = PortfolioSnapshot(
        cash=Decimal("700"),
        equity=Decimal("1000"),
        gross_exposure=Decimal("300"),
        net_exposure=Decimal("300"),
        positions=(PortfolioPosition(" aapl ", 30, Decimal("300")),),
        strategy_exposure=(PortfolioStrategyExposure(
            "strategy-a", "AAPL", Decimal("300"), 30
        ),),
        observed_at=now,
    )
    view = build_portfolio_operations_view(
        snapshot=snapshot,
        policy=policy,
        decisions=(),
        reconciliation=None,
        runtime_state="HALTED",
    )

    assert view.runtime_state == "HALTED"
    assert view.reconciliation_state.startswith("未知")
    assert view.strategy_allocations[0].strategy_version_id == "strategy-a"
    assert view.positions[0].symbol == "AAPL"
    assert view.positions[0].contributing_strategies == "strategy-a"


def test_unavailable_portfolio_projection_shows_unknown_money_not_zero():
    view = build_unavailable_portfolio_operations_view(
        policy=_configured_policy(),
        runtime_state="HALTED",
        reconciliation=None,
    )
    assert view.cash == view.equity == view.gross_exposure == view.net_exposure == "未知"
    assert view.strategy_allocations[0].exposure == "未知"
    assert view.strategy_allocations[0].pending_exposure == "未知"
    assert view.reconciliation_state == "未知 / 阻止新开仓"


def test_pending_actions_show_all_strategy_attributions():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    decision_id = "decision-1"
    decision = PortfolioDecision(
        decision_id=decision_id,
        decision=PortfolioVerdict.APPROVE,
        symbol="AAPL",
        strategy_version_ids=("strategy-a", "strategy-b"),
        requested_quantity=4,
        net_quantity=2,
        blocker=None,
        attribution=(
            PortfolioOrderAttribution(decision_id, "strategy-a", "proposal-a", "AAPL", 3),
            PortfolioOrderAttribution(decision_id, "strategy-b", "proposal-b", "AAPL", -1),
        ),
        action=PortfolioAction("AAPL", PortfolioSide.BUY, 2, Decimal("10")),
    )
    record = PortfolioDecisionRecord(
        decision=decision,
        portfolio_cycle_id="cycle-1",
        observed_at=now,
        policy_identity="policy",
        policy_revision="1",
        created_at=now,
        risk_outcome="approved",
    )
    view = build_portfolio_operations_view(
        snapshot=PortfolioSnapshot(observed_at=now),
        policy=PortfolioCapitalPolicy(),
        decisions=(record,),
        reconciliation=None,
        runtime_state="RUNNING",
    )
    assert "strategy-a:+3" in view.pending_actions[0]
    assert "strategy-b:-1" in view.pending_actions[0]


def test_one_account_cannot_register_a_second_portfolio_runtime() -> None:
    registry = PortfolioRuntimeRegistry()
    first = _runtime()
    second = _runtime()
    assert registry.register(account_alias="DU***01", runtime=first) is first
    assert registry.runtime_for("DU***01") is first
    with pytest.raises(ValueError, match="already has an active"):
        registry.register(account_alias="DU***01", runtime=second)
    with pytest.raises(ValueError, match="masked account alias"):
        registry.register(account_alias="DU1234567", runtime=second)
    assert registry.register(account_alias="DU***02", runtime=second) is second


def test_page_source_has_no_trading_authority_imports():
    source = Path(__file__).resolve().parents[1] / "src/us_quant/desktop_v2/pages/execution/page.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in (
        "CapitalAllocator", "PortfolioStateRepositoryPort", "RiskApplication",
        "OrderDispatch", "ExecutionApplication", "BrokerExecutionPort",
        "save_editor", "_plan_application",
    ):
        assert forbidden not in text


def test_plan_missing_refuses_and_save_round_trips_with_audit(tmp_path):
    repository = SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite")
    application = PortfolioOperatingPlanApplication(
        repository=repository,
        strategies=_GovernedStrategies((_governed_version("strategy-a"),)),
    )
    with pytest.raises(PortfolioPlanRefused, match="no portfolio operating plan"):
        application.load()
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    saved = application.save(
        selected_version_ids=("strategy-a",), policy=_configured_policy(),
        expected_revision=0, operator_reason="Initial Paper allocation", changed_at=now,
    )
    assert saved.revision == 1
    assert application.load() == saved
    audit, = repository.audit_events()
    assert audit.selected_version_ids == ("strategy-a",)
    assert audit.policy_limits == (
        "1000", "1000", "1000", "500", "1", "1", "10", "10"
    )
    assert audit.operator_reason == "Initial Paper allocation"
    assert "IBKR" not in repr(audit)


def test_plan_stale_cas_and_active_session_edits_are_refused(tmp_path):
    repository = SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite")
    active = False
    application = PortfolioOperatingPlanApplication(
        repository=repository,
        strategies=_GovernedStrategies((_governed_version("strategy-a"),)),
        active_session=lambda: active,
    )
    saved = application.save(
        selected_version_ids=("strategy-a",), policy=_configured_policy(),
        expected_revision=0, operator_reason="initial",
    )
    with pytest.raises(PortfolioOperatingPlanConflict):
        application.save(
            selected_version_ids=("strategy-a",), policy=_configured_policy(),
            expected_revision=0, operator_reason="stale",
        )
    active = True
    with pytest.raises(PortfolioPlanRefused, match="active Paper session"):
        application.save(
            selected_version_ids=("strategy-a",), policy=_configured_policy(),
            expected_revision=saved.revision, operator_reason="hot change",
        )


@pytest.mark.parametrize("version", [
    _governed_version("strategy-a", status=StrategyStatus.RESEARCH),
    _governed_version("strategy-a", mode=StrategyMode.RESEARCH),
    _governed_version("strategy-a", gate_passed=False),
])
def test_rejects_non_governed_selection(tmp_path, version):
    application = PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite"),
        strategies=_GovernedStrategies((version,)),
    )
    with pytest.raises(PortfolioPlanRefused, match="not governed"):
        application.save(
            selected_version_ids=("strategy-a",), policy=_configured_policy(),
            expected_revision=0, operator_reason="must refuse",
        )


def test_unknown_selected_strategy_refuses_portfolio_plan(tmp_path):
    application = PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite"),
        strategies=_GovernedStrategies((_governed_version("known"),)),
    )
    with pytest.raises(PortfolioPlanRefused, match="does not exist"):
        application.save(
            selected_version_ids=("unknown",),
            policy=_configured_policy(("unknown",)),
            expected_revision=0,
            operator_reason="unknown strategy must fail closed",
        )


def test_selected_but_unallocated_strategy_refuses_portfolio_plan(tmp_path):
    application = PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite"),
        strategies=_GovernedStrategies((_governed_version("selected"),)),
    )
    with pytest.raises(PortfolioPlanRefused, match="lacks an enabled positive allocation"):
        application.save(
            selected_version_ids=("selected",),
            policy=_configured_policy(("other",)),
            expected_revision=0,
            operator_reason="selected strategy must be allocated",
        )


def test_plan_audit_rejects_secrets_and_raw_paper_account_id(tmp_path):
    application = PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite"),
        strategies=_GovernedStrategies((_governed_version("strategy-a"),)),
    )
    for reason in ("password=hunter2", "account DU1234567"):
        with pytest.raises(PortfolioPlanRefused, match="must not contain"):
            application.save(
                selected_version_ids=("strategy-a",), policy=_configured_policy(),
                expected_revision=0, operator_reason=reason,
            )


def test_frozen_portfolio_launch_fact_rejects_plan_revision_drift():
    version = _governed_version("strategy-a")
    plan = PortfolioOperatingPlan(
        plan_id="plan-a", revision=1, selected_version_ids=("strategy-a",),
        policy=_configured_policy(), created_at=version.created_at, updated_at=version.updated_at,
    )
    frozen = freeze_portfolio_plan(plan, (version,))
    assert frozen.selected_version_ids == ("strategy-a",)
    assert portfolio_plan_matches(frozen, plan, (version,))
    changed = PortfolioOperatingPlan(
        plan_id="plan-a", revision=2, selected_version_ids=("strategy-a",),
        policy=_configured_policy(), created_at=version.created_at,
        updated_at=version.updated_at.replace(minute=1),
    )
    assert not portfolio_plan_matches(frozen, changed, (version,))


def test_portfolio_launch_has_no_primary_strategy_and_uses_plan_capital():
    version = _governed_version("strategy-a")
    plan = PortfolioOperatingPlan(
        plan_id="plan-a", revision=1, selected_version_ids=("strategy-a",),
        policy=_configured_policy(), created_at=version.created_at,
        updated_at=version.updated_at,
    )
    request = freeze_launch(
        attempt_id=1,
        strategy=None,
        candidates=(AutoQuantCandidate("AAPL", "Apple", "Technology", 1, Decimal("1"), "BUY"),),
        requested_capital_limit=Decimal("1"),
        order_channel=PaperOrderChannel(
            config="config", repository="repository", extended_hours_enabled=False
        ),
        portfolio_plan=plan,
        portfolio_strategies=(version,),
    )
    assert request.strategy is None
    assert request.plan.strategy_version_id is None
    assert request.plan.parameter_hash is None
    assert request.plan.requested_capital_limit == plan.policy.total_capital_limit


def test_editor_form_is_parsed_by_application_and_saved_with_revision(tmp_path):
    application = PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(tmp_path / "plan.sqlite"),
        strategies=_GovernedStrategies((_governed_version("strategy-a"),)),
    )
    saved = application.save_editor({
        "expected_revision": "0",
        "selected_version_ids": "strategy-a",
        "limits": "1000|1000|1000|500|1|1|10|10",
        "allocations": "strategy-a|1|1000|1000|true",
        "operator_reason": "reviewed initial plan",
    })
    assert saved.revision == 1
    assert saved.policy.allocation_for("strategy-a").enabled
