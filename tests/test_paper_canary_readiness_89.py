from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from us_quant.sqlite_support import connect_sqlite_readonly
from us_quant.trading.adapters.sqlite.portfolio_operating_plan_repository import (
    PortfolioOperatingPlanStoreUnreadable,
    SQLitePortfolioOperatingPlanRepository,
)
from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadiness,
)
from us_quant.trading.application.paper_canary_readiness import (
    BrokerCheckProjection,
    PaperCanaryEvidenceTarget,
    PaperCanaryInspectionSpec,
    PaperCanaryInspectionStatus,
    PaperCanaryReadinessApplication,
    PaperPerformanceObservation,
)
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioStrategyAllocation,
)
from us_quant.trading.domain.portfolio_operations import (
    PortfolioOperatingPlan,
    PortfolioPlanAuditEvent,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
VERSION_A = "version-a"
VERSION_B = "version-b"
_DEFAULT_PLAN = object()


def _version(
    version_id: str,
    *,
    status: StrategyStatus = StrategyStatus.PAPER_SHADOW,
    mode: StrategyMode = StrategyMode.PAPER_SHADOW,
    gate_passed: bool = False,
) -> StrategyVersion:
    strategy_id = f"strategy-{version_id}"
    return StrategyVersion(
        definition=StrategyDefinition(strategy_id, strategy_id, "test strategy"),
        identity=StrategyIdentity(strategy_id, version_id, f"hash-{version_id}"),
        semver="1.0.0",
        status=status,
        mode=mode,
        parameters={"warmup_minutes": 20, "momentum_lookback_minutes": 5},
        universe_hash=f"universe-{version_id}",
        code_hash=f"code-{version_id}",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=gate_passed,
        gate_reason="legacy flag is display only",
        created_at=NOW - timedelta(days=10),
        updated_at=NOW,
    )


def _plan(version_ids: tuple[str, ...]) -> PortfolioOperatingPlan:
    allocations = tuple(
        PortfolioStrategyAllocation(
            strategy_version_id=version_id,
            capital_weight=Decimal(1) / Decimal(len(version_ids)),
            max_capital=Decimal(500),
            max_gross_exposure=Decimal(1000),
            enabled=True,
        )
        for version_id in version_ids
    )
    return PortfolioOperatingPlan(
        plan_id="plan-1",
        revision=1,
        selected_version_ids=version_ids,
        policy=PortfolioCapitalPolicy(
            total_capital_limit=Decimal(2000),
            max_gross_exposure=Decimal(2000),
            max_net_exposure=Decimal(2000),
            max_single_position_notional=Decimal(1000),
            max_symbol_concentration=Decimal(1),
            max_strategy_concentration=Decimal(1),
            max_positions=4,
            max_open_orders=4,
            allocations=allocations,
        ),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )


def _readiness(status: str = "READY") -> MarketEvidenceReadiness:
    ready = status == "READY"
    return MarketEvidenceReadiness(
        symbol="SPY",
        provider="IBKR",
        status=status,
        captured_session_count=25 if ready else 0,
        robustness_usable_sessions=25 if ready else 0,
        high_quality_sessions=25 if ready else 0,
        review_ready_sessions=25 if ready else 0,
        required_sessions=25,
        remaining_sessions=0 if ready else 25,
        first_session="2026-09-01" if ready else None,
        latest_session="2026-10-02" if ready else None,
        latest_session_quality=None,
        ready_for_targeted_review=ready,
        blockers=() if ready else ("NO_CAPTURED_DATA",),
    )


class _Latest:
    def __init__(self, value=None):
        self.value = value

    def latest_for_version(self, _version_id):
        return self.value


class _Strategies:
    def __init__(self, versions):
        self.versions = tuple(versions)

    def list_versions(self):
        return self.versions


class _Lifecycle:
    def __init__(self, decisions=()):
        self.decisions = tuple(decisions)

    def decisions_for_version(self, _version_id):
        return self.decisions


class _Authorizer:
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = []

    def authorises(self, version_id):
        self.calls.append(version_id)
        return self.answer


class _MarketReadiness:
    def __init__(self, default_status="READY", by_symbol=None):
        self.default_status = default_status
        self.by_symbol = by_symbol or {}
        self.calls = []

    def inspect(self, *, symbol, provider, parameters):
        self.calls.append((symbol, provider, parameters))
        return self.by_symbol.get(symbol, _readiness(self.default_status))


class _PlanRepository:
    def __init__(self, plan):
        self.plan = plan

    def load(self):
        return self.plan


class _PlanApplication:
    def __init__(self, plan, *, valid=True):
        self.plan = plan
        self.valid = valid

    def load(self):
        if self.plan is None or not self.valid:
            raise RuntimeError("canonical portfolio plan validation failed")
        return self.plan


def _spec(targets=None, *, expected=None):
    return PaperCanaryInspectionSpec(
        strategy_version_ids=(VERSION_A, VERSION_B),
        evidence_targets=tuple(
            targets
            or (
                PaperCanaryEvidenceTarget(VERSION_B, "SPY"),
                PaperCanaryEvidenceTarget(VERSION_A, "SPY"),
                PaperCanaryEvidenceTarget(VERSION_A, "QQQ"),
                PaperCanaryEvidenceTarget(VERSION_A, "AAPL"),
                PaperCanaryEvidenceTarget(VERSION_A, "NVDA"),
            )
        ),
        provider="IBKR",
        expected_runtime_revision=expected,
    )


def _broker(*, success=True, freshness="FRESH"):
    return BrokerCheckProjection(
        checked=True,
        refresh_succeeded=success,
        configured_environment="paper",
        configured_host="127.0.0.1",
        configured_port=4002,
        configured_client_id=17,
        paper_order_submission_enabled=False,
        api_read_only=True,
        account_alias="DU***42",
        broker_environment="paper",
        observed_at=NOW if success else None,
        freshness=freshness,
        last_error=None if success else "ConnectionError",
    )


def _app(
    *,
    versions=None,
    readiness=None,
    plan=_DEFAULT_PLAN,
    plan_valid=True,
    authorizer=None,
    lifecycle=None,
    performance=None,
    targets=None,
    expected=None,
):
    versions = versions or (_version(VERSION_A), _version(VERSION_B))
    plan = _plan((VERSION_A, VERSION_B)) if plan is _DEFAULT_PLAN else plan
    market = readiness or _MarketReadiness()
    app = PaperCanaryReadinessApplication(
        spec=_spec(targets, expected=expected),
        strategies=_Strategies(versions),
        authentications=_Latest(SimpleNamespace(verdict="PASS")),
        gates=_Latest(SimpleNamespace(verdict="PASS")),
        coverages=_Latest(SimpleNamespace(verdict="PASS")),
        lifecycle_decisions=lifecycle or _Lifecycle(),
        paper_performance=_Latest(performance),
        paper_launch_authorizer=authorizer or _Authorizer(),
        market_evidence_readiness=market,
        portfolio_plan_repository=_PlanRepository(plan),
        portfolio_plan_application=_PlanApplication(plan, valid=plan_valid),
    )
    return app, market


def test_c89_01_zero_evidence_is_data_collection_with_0_of_25():
    app, _ = _app(readiness=_MarketReadiness("COLLECTING"))
    report = app.inspect(current_runtime_revision="rev")
    assert report.overall_status is PaperCanaryInspectionStatus.DATA_COLLECTION
    assert {
        (row.review_ready_sessions, row.required_sessions)
        for row in report.evidence_targets
    } == {(0, 25)}


def test_c89_02_four_of_five_targets_ready_stays_data_collection():
    targets = _spec().evidence_targets
    by_symbol = {target.symbol: _readiness("READY") for target in targets}
    by_symbol["NVDA"] = _readiness("COLLECTING")
    app, _ = _app(readiness=_MarketReadiness(by_symbol=by_symbol))
    assert (
        app.inspect(current_runtime_revision="rev").overall_status
        is PaperCanaryInspectionStatus.DATA_COLLECTION
    )


def test_c89_03_shared_spy_is_evaluated_with_each_versions_parameters():
    a = replace(
        _version(VERSION_A),
        parameters={"warmup_minutes": 9, "momentum_lookback_minutes": 3},
    )
    b = replace(
        _version(VERSION_B),
        parameters={"warmup_minutes": 40, "momentum_lookback_minutes": 12},
    )
    targets = (
        PaperCanaryEvidenceTarget(VERSION_A, "SPY"),
        PaperCanaryEvidenceTarget(VERSION_B, "SPY"),
    )
    app, market = _app(versions=(a, b), targets=targets)
    app.inspect(current_runtime_revision="rev")
    assert [call[2]["warmup_minutes"] for call in market.calls] == [9, 40]


def test_c89_04_research_version_never_counts_as_governance_ready():
    versions = (
        _version(
            VERSION_A, status=StrategyStatus.RESEARCH, mode=StrategyMode.PAPER_SHADOW
        ),
        _version(VERSION_B),
    )
    app, _ = _app(versions=versions)
    assert (
        app.inspect(current_runtime_revision="rev").overall_status
        is PaperCanaryInspectionStatus.GOVERNANCE_NOT_READY
    )


def test_paper_shadow_status_with_research_mode_is_blocked():
    versions = (_version(VERSION_A, mode=StrategyMode.RESEARCH), _version(VERSION_B))
    app, _ = _app(versions=versions)
    assert (
        app.inspect(current_runtime_revision="rev").overall_status
        is PaperCanaryInspectionStatus.GOVERNANCE_NOT_READY
    )


def test_c89_05_paper_shadow_with_stale_authorization_is_blocked():
    app, _ = _app(authorizer=_Authorizer(False))
    assert (
        app.inspect(current_runtime_revision="rev").overall_status
        is PaperCanaryInspectionStatus.GOVERNANCE_NOT_READY
    )


def test_c89_06_legacy_gate_passed_does_not_authorize():
    app, _ = _app(
        versions=(_version(VERSION_A, gate_passed=True), _version(VERSION_B)),
        authorizer=_Authorizer(False),
    )
    report = app.inspect(current_runtime_revision="rev")
    assert report.strategies[0].legacy_gate_passed is True
    assert report.strategies[0].paper_launch_authorized is False
    assert report.overall_status is PaperCanaryInspectionStatus.GOVERNANCE_NOT_READY


def test_c89_07_reuses_current_paper_launch_authorizer_for_each_version():
    authorizer = _Authorizer(True)
    app, _ = _app(authorizer=authorizer)
    app.inspect(current_runtime_revision="rev")
    assert authorizer.calls == [VERSION_A, VERSION_B]


def test_c89_08_missing_portfolio_plan_is_not_ready():
    app, _ = _app(plan=None)
    report = app.inspect(current_runtime_revision="rev")
    assert report.portfolio_plan.status == "MISSING"
    assert report.overall_status is PaperCanaryInspectionStatus.PORTFOLIO_NOT_READY


def test_c89_08b_canonical_plan_validation_failure_is_not_ready():
    plan = _plan((VERSION_A, VERSION_B))
    app, _ = _app(plan=plan, plan_valid=False)
    report = app.inspect(current_runtime_revision="rev")
    assert report.portfolio_plan.status == "INVALID"
    assert report.overall_status is PaperCanaryInspectionStatus.PORTFOLIO_NOT_READY


@pytest.mark.parametrize("selected", [(VERSION_A,), (VERSION_A, VERSION_B, "extra")])
def test_c89_09_10_portfolio_plan_subset_or_superset_is_rejected(selected):
    app, _ = _app(plan=_plan(selected))
    assert (
        app.inspect(current_runtime_revision="rev").overall_status
        is PaperCanaryInspectionStatus.PORTFOLIO_NOT_READY
    )


def test_c89_11_exact_plan_is_accepted_offline_for_live_preflight_only():
    app, _ = _app()
    report = app.inspect(current_runtime_revision="rev")
    assert report.portfolio_plan.status == "VALID"
    assert report.overall_status is PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT


def test_c89_12_13_offline_never_claims_canary_ready():
    app, _ = _app()
    report = app.inspect(current_runtime_revision="rev")
    assert report.overall_status is PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT
    assert report.overall_status is not PaperCanaryInspectionStatus.READY_FOR_CANARY


def test_c89_14_broker_refresh_failure_is_broker_not_ready():
    app, _ = _app()
    assert (
        app.inspect(
            current_runtime_revision="rev", broker_check=_broker(success=False)
        ).overall_status
        is PaperCanaryInspectionStatus.BROKER_NOT_READY
    )


def test_broker_paper_environment_mismatch_is_not_ready():
    app, _ = _app()
    bad = replace(_broker(), broker_environment="live")
    assert (
        app.inspect(current_runtime_revision="rev", broker_check=bad).overall_status
        is PaperCanaryInspectionStatus.BROKER_NOT_READY
    )


def test_broker_truth_without_safe_account_alias_is_not_ready():
    app, _ = _app()
    bad = replace(_broker(), account_alias=None)
    assert (
        app.inspect(current_runtime_revision="rev", broker_check=bad).overall_status
        is PaperCanaryInspectionStatus.BROKER_NOT_READY
    )


def test_stale_broker_truth_is_not_ready():
    app, _ = _app()
    bad = replace(_broker(), freshness="STALE")
    assert (
        app.inspect(current_runtime_revision="rev", broker_check=bad).overall_status
        is PaperCanaryInspectionStatus.BROKER_NOT_READY
    )


def test_c89_15_broker_success_does_not_clear_unchecked_reconciliation():
    app, _ = _app()
    assert (
        app.inspect(
            current_runtime_revision="rev", broker_check=_broker()
        ).overall_status
        is PaperCanaryInspectionStatus.RECONCILIATION_NOT_READY
    )


def test_c89_16_reconciliation_blocker_is_preserved():
    app, _ = _app()
    result = SimpleNamespace(blockers=("unexplained_fill",))
    report = app.inspect(
        current_runtime_revision="rev",
        broker_check=_broker(),
        reconciliation_result=result,
    )
    assert report.overall_status is PaperCanaryInspectionStatus.RECONCILIATION_NOT_READY
    assert report.reconciliation.blockers == ("unexplained_fill",)


def test_c89_17_only_clean_canonical_reconciliation_allows_ready_diagnostic():
    app, _ = _app()
    report = app.inspect(
        current_runtime_revision="rev",
        broker_check=_broker(),
        reconciliation_result=SimpleNamespace(blockers=()),
    )
    assert report.overall_status is PaperCanaryInspectionStatus.READY_FOR_CANARY


def test_c89_18_missing_performance_does_not_block_first_canary_preflight():
    app, _ = _app(performance=None)
    report = app.inspect(current_runtime_revision="rev")
    assert (
        report.strategies[0].paper_performance_status
        is PaperPerformanceObservation.NOT_YET_OBSERVED
    )
    assert report.overall_status is PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT


def test_c89_19_performance_fail_is_displayed_without_being_hidden():
    failed = SimpleNamespace(verdict=SimpleNamespace(value="FAIL"))
    app, _ = _app(performance=failed)
    report = app.inspect(current_runtime_revision="rev")
    assert (
        report.strategies[0].paper_performance_status
        is PaperPerformanceObservation.FAIL
    )


def test_c89_20_application_performs_no_repository_mutations():
    app, _ = _app()
    report = app.inspect(current_runtime_revision="rev")
    assert report.overall_status is PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT
    assert (
        report.stage6_f_blocker == "Stage 6-D supervised operational canary incomplete"
    )


def test_c89_21_22_no_strategy_transition_or_order_action_surface():
    source = Path(
        "src/us_quant/trading/application/paper_canary_readiness.py"
    ).read_text(encoding="utf-8")
    assert ".transition(" not in source
    assert ".submit(" not in source
    assert ".cancel(" not in source


def test_c89_23_24_report_is_deterministic_and_input_order_independent():
    targets = list(_spec().evidence_targets)
    first, _ = _app(targets=targets)
    second, _ = _app(targets=list(reversed(targets)))
    left = first.inspect(current_runtime_revision="rev")
    right = second.inspect(current_runtime_revision="rev")
    assert left == right


def test_c89_25_lifecycle_latest_uses_utc_instant_then_stable_id():
    earlier = SimpleNamespace(
        authorized_at=datetime.fromisoformat("2026-10-05T08:00:00+00:00"),
        decision_id="z",
    )
    later = SimpleNamespace(
        authorized_at=datetime.fromisoformat("2026-10-05T10:00:00+02:00"),
        decision_id="b",
    )
    tied = SimpleNamespace(
        authorized_at=datetime.fromisoformat("2026-10-05T08:00:00+00:00"),
        decision_id="a",
    )
    app, _ = _app(lifecycle=_Lifecycle((tied, earlier, later)))
    report = app.inspect(current_runtime_revision="rev")
    assert report.strategies[0].latest_lifecycle_decision.decision_id == "z"


def test_c89_26_expected_runtime_revision_mismatch_is_visible():
    app, _ = _app(expected="expected")
    report = app.inspect(current_runtime_revision="actual")
    assert report.overall_status is PaperCanaryInspectionStatus.BASELINE_MISMATCH


def test_c89_27_unknown_revision_fails_closed_only_when_expected_is_required():
    app, _ = _app(expected="expected")
    assert (
        app.inspect(current_runtime_revision=None).overall_status
        is PaperCanaryInspectionStatus.BASELINE_MISMATCH
    )
    app, _ = _app()
    assert (
        app.inspect(current_runtime_revision=None).overall_status
        is PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT
    )


def test_spec_rejects_duplicate_targets_and_targets_outside_version_set():
    duplicate = PaperCanaryEvidenceTarget(VERSION_A, "SPY")
    with pytest.raises(ValueError, match="unique"):
        _spec(targets=(duplicate, duplicate))
    with pytest.raises(ValueError, match="inspected version"):
        PaperCanaryInspectionSpec(
            strategy_version_ids=(VERSION_A,),
            evidence_targets=(PaperCanaryEvidenceTarget(VERSION_B, "SPY"),),
            provider="IBKR",
        )


def test_application_layer_has_no_shell_qt_execution_or_risk_authority():
    path = Path("src/us_quant/trading/application/paper_canary_readiness.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    imports += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    forbidden = (
        "PySide",
        "PyQt",
        "subprocess",
        "execution",
        "risk",
        "portfolio_runtime",
        "paper_autonomy",
        "evolution",
    )
    assert not any(
        any(term.lower() in module.lower() for term in forbidden) for module in imports
    )
    source = path.read_text(encoding="utf-8")
    assert "gate_passed" in source
    assert "MINIMUM_COMPLETENESS" not in source
    assert "0.98" not in source and "98%" not in source
    mutators = {
        "record",
        "save",
        "update",
        "transition",
        "submit",
        "cancel",
        "promote",
        "arm",
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called_attributes & mutators


def test_composition_reuses_canonical_authorities_and_read_only_stores():
    source = Path(
        "src/us_quant/trading/composition/paper_canary_readiness.py"
    ).read_text(encoding="utf-8")
    assert "MarketEvidenceReadinessApplication" in source
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "PaperLaunchAuthorizer" in calls
    assert "StrategyCoverageCurrentValidator" in calls
    assert "read_only=True" in source
    assert "bootstrap" not in source


def test_market_quote_store_is_composed_in_read_only_mode():
    source = Path(
        "src/us_quant/trading/composition/paper_canary_readiness.py"
    ).read_text(encoding="utf-8")
    assert "MinuteQuoteStore(quote_path, read_only=True)" in source


def test_uncreated_performance_table_means_no_observation(tmp_path):
    import sqlite3

    from us_quant.trading.composition.paper_canary_readiness import (
        _performance_read_port,
    )

    database = tmp_path / "governance.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated_fact (value TEXT)")
    reader = _performance_read_port(database)
    assert reader.latest_for_version(VERSION_A) is None


def test_cli_shell_revision_is_outside_application_and_has_no_write_actions():
    source = Path("src/us_quant/paper_canary_readiness.py").read_text(encoding="utf-8")
    assert "subprocess.run" in source and '"git"' in source
    assert "--promote" not in source and "--arm" not in source
    assert "--enable-orders" not in source and "--reconcile-and-write" not in source
    assert "submit(" not in source and "cancel(" not in source


def test_readonly_sqlite_connection_refuses_writes(tmp_path):
    path = tmp_path / "facts.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
        connection.execute("INSERT INTO facts VALUES ('durable')")
    with connect_sqlite_readonly(path) as connection:
        assert connection.execute("SELECT value FROM facts").fetchone()[0] == "durable"
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM facts")


def test_readonly_plan_repository_cannot_save_or_change_durable_file(tmp_path):
    path = tmp_path / "portfolio.sqlite3"
    SQLitePortfolioOperatingPlanRepository(path)
    before = sha256(path.read_bytes()).hexdigest()
    repository = SQLitePortfolioOperatingPlanRepository(path, read_only=True)
    plan = _plan((VERSION_A, VERSION_B))
    audit = PortfolioPlanAuditEvent(
        plan_id=plan.plan_id,
        revision=plan.revision,
        changed_at=plan.updated_at,
        selected_version_ids=plan.selected_version_ids,
        policy_limits=tuple(
            str(getattr(plan.policy, name))
            for name in (
                "total_capital_limit",
                "max_gross_exposure",
                "max_net_exposure",
                "max_single_position_notional",
                "max_symbol_concentration",
                "max_strategy_concentration",
                "max_positions",
                "max_open_orders",
            )
        ),
        allocation_limits=tuple(
            (
                item.strategy_version_id,
                str(item.capital_weight),
                str(item.max_capital),
                str(item.max_gross_exposure),
                item.enabled,
            )
            for item in plan.policy.allocations
        ),
        operator_reason="test read-only write refusal",
    )
    with pytest.raises(PortfolioOperatingPlanStoreUnreadable):
        repository.save(plan, expected_revision=0, audit=audit)
    assert sha256(path.read_bytes()).hexdigest() == before
