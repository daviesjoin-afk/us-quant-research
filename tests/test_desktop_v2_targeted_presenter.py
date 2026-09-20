"""Pure-Python tests for the targeted validation projection."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from us_quant.desktop_v2.pages.research.targeted.evidence_presenter import (
    evidence_view,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedControlView,
    TargetedRowTone,
)
from us_quant.desktop_v2.pages.research.targeted.rows import money, price, ratio
from us_quant.desktop_v2.pages.research.targeted.session_presenter import (
    preflight_view,
    session_view,
)


CONTROLS = TargetedControlView(
    strategy_enabled=True,
    target_enabled=True,
    subscribe_enabled=True,
    shadow_start_enabled=True,
    shadow_stop_enabled=False,
    replay_enabled=True,
    robustness_enabled=True,
)


def test_money_price_and_ratio_keep_the_legacy_display_rules() -> None:
    assert money(Decimal("1234.5")) == "$1,234.50"
    assert money(Decimal("12.3"), signed=True) == "+$12.30"
    assert money(None) == "—"
    assert price(Decimal("100.1000")) == "100.1"
    assert price(None) == "—"
    assert ratio(Decimal("0.032")) == "3.2%"
    assert ratio(Decimal("-0.0123"), signed=True, places=3) == "-1.230%"
    assert ratio(None) == "不可估计"


def test_session_view_empty_state_matches_the_legacy_cards() -> None:
    view = session_view(
        snapshot=None,
        target_status="未指定",
        minute_status="分钟证据：等待输入",
        preflight=None,
        controls=CONTROLS,
    )
    assert view.status.value == "未启动"
    assert view.equity.value == "—"
    assert view.realized.value == "$0.00"
    assert view.unrealized.value == "$0.00"
    assert view.trades.value == "0 / 4"
    assert view.positions == ()
    assert view.fills == ()
    assert view.preflight.rows == ()


def test_session_view_active_projects_metrics_positions_and_fills() -> None:
    snapshot = SimpleNamespace(
        active=True,
        target_symbol="AAPL",
        equity=Decimal("10010.25"),
        cash=Decimal("9000.00"),
        initial_cash=Decimal("10000.00"),
        realized_pnl=Decimal("10.25"),
        daily_realized_pnl=Decimal("5.00"),
        unrealized_pnl=Decimal("-2.00"),
        trades_today=1,
        fills=(
            SimpleNamespace(
                session_id="session-1",
                occurred_at="2026-09-20T09:31:00",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                price=Decimal("100.10"),
                commission=Decimal("0.35"),
                realized_pnl=None,
                reason="signal",
                provider="IBKR",
                coverage="Type 1",
            ),
        ),
        positions=(
            SimpleNamespace(
                symbol="AAPL",
                quantity=1,
                entry_price=Decimal("100.10"),
                opened_at="2026-09-20T09:31:00",
                high_water=Decimal("101.00"),
                provider="IBKR",
                coverage="Type 1",
            ),
        ),
        status="running",
        session_id="session-1",
        strategy_version_id="v1",
        parameter_hash="abcdef012345",
        trading_day="2026-09-20",
        capital_source="IBKR Paper NetLiquidation",
    )
    view = session_view(
        snapshot=snapshot,
        target_status="AAPL · 已设置",
        minute_status="分钟证据",
        preflight=None,
        controls=CONTROLS,
    )
    assert view.status.value == "运行中"
    assert view.equity.value == "$10,010.25"
    assert view.realized.value == "+$10.25"
    assert view.unrealized.value == "$-2.00"
    assert view.trades.value == "1 / 4"
    assert view.positions[0].values[0] == "AAPL"
    assert view.fills[0].values[2] == "BUY"
    assert view.fills[0].tone is TargetedRowTone.SUCCESS
    assert "session-1" in view.explanation


def test_preflight_view_marks_blocking_and_optional_gates() -> None:
    result = SimpleNamespace(
        symbol="AAPL",
        company_name="Apple",
        security_type="Stock",
        leader_tier=1,
        shadow_ready=False,
        hard_gates_passed=3,
        hard_gate_count=4,
        gates=(
            SimpleNamespace(
                code="identity",
                name="标的身份",
                passed=True,
                blocking=True,
                observed="AAPL",
                required="有效代码",
                category="标的",
            ),
            SimpleNamespace(
                code="market",
                name="行情就绪",
                passed=False,
                blocking=True,
                observed="stale",
                required="READY",
                category="行情",
            ),
            SimpleNamespace(
                code="evidence",
                name="研究证据",
                passed=False,
                blocking=False,
                observed="不足",
                required="20 会话",
                category="研究",
            ),
        ),
    )
    view = preflight_view(result)
    assert "暂不可启动" in view.summary
    assert "硬门 3/4" in view.summary
    assert "1 项研究证据待补" in view.summary
    assert view.rows[1].tone is TargetedRowTone.ERROR
    assert view.rows[2].tone is TargetedRowTone.WARNING


def _replay_result():
    return SimpleNamespace(
        run_id="replay-run",
        symbol="AAPL",
        strategy_semver="1.0.0",
        providers=("IBKR",),
        first_minute="2026-09-20T09:30:00",
        last_minute="2026-09-20T09:31:00",
        row_count=2,
        gap_count=0,
        total_return=Decimal("0.0125"),
        maximum_drawdown=Decimal("0.004"),
        realized_pnl=Decimal("125.00"),
        fills=(1, 2),
        commission_cost=Decimal("0.70"),
    )


def _robustness_result():
    scenario = SimpleNamespace(
        scenario="base",
        session_count=20,
        compounded_return=Decimal("0.10"),
        mean_session_return=Decimal("0.005"),
        median_session_return=Decimal("0.004"),
        worst_session_return=Decimal("-0.01"),
        profitable_session_fraction=Decimal("0.6"),
        maximum_drawdown=Decimal("0.03"),
        total_fills=10,
        commission_cost=Decimal("3.50"),
    )
    return SimpleNamespace(
        run_id="robustness-run",
        symbol="AAPL",
        strategy_semver="1.0.0",
        provider="IBKR",
        first_session="2026-09-01",
        last_session="2026-09-20",
        usable_sessions=20,
        total_sessions=20,
        sign_stability_fraction=Decimal("0.75"),
        evidence_grade="B",
        review_ready=True,
        skipped_sessions=(),
        scenario_summaries=(scenario,),
    )


def _walk_forward_result():
    fold = SimpleNamespace(
        fold_number=1,
        selected_scenario="base",
        train_start="2026-09-01",
        train_end="2026-09-10",
        validation_start="2026-09-11",
        validation_end="2026-09-15",
        validation_metrics=SimpleNamespace(compounded_return=Decimal("0.02")),
        validation_benchmark=SimpleNamespace(compounded_return=Decimal("0.01")),
        validation_passed=True,
        test_start="2026-09-16",
        test_end="2026-09-20",
        test_metrics=SimpleNamespace(compounded_return=Decimal("0.03")),
        test_benchmark=SimpleNamespace(compounded_return=Decimal("0.01")),
        test_excess_return=Decimal("0.02"),
    )
    return SimpleNamespace(
        run_id="walk-run",
        symbol="AAPL",
        strategy_semver="1.0.0",
        folds=(fold,),
        validation_passed_folds=1,
        out_of_sample_metrics=SimpleNamespace(compounded_return=Decimal("0.03")),
        out_of_sample_benchmark=SimpleNamespace(compounded_return=Decimal("0.01")),
        out_of_sample_excess_return=Decimal("0.02"),
        evidence_grade="B",
    )


def _overfit_result():
    return SimpleNamespace(
        run_id="overfit-run",
        symbol="AAPL",
        observations_used=20,
        observations_total=20,
        candidate_count=4,
        cscv_partitions=8,
        cscv_combinations=70,
        pbo=Decimal("0.25"),
        probability_oos_loss=Decimal("0.30"),
        average_performance_degradation=Decimal("-0.005"),
        dsr_probability=Decimal("0.80"),
        dsr_selected_scenario="base",
        evidence_grade="B",
    )


def _quality_result():
    session = SimpleNamespace(
        session_date="2026-09-20",
        raw_rows=346,
        usable_rows=340,
        completeness=Decimal("0.98"),
        missing_minutes=6,
        maximum_consecutive_missing=2,
        stale_rows=1,
        invalid_quote_rows=0,
        p95_source_age_seconds=Decimal("1.20"),
        size_coverage_fraction=Decimal("0.95"),
        high_quality=True,
        failure_reasons=(),
    )
    return SimpleNamespace(
        symbol="AAPL",
        high_quality_sessions=1,
        session_count=1,
        minimum_completeness=Decimal("0.98"),
        maximum_consecutive_missing=2,
        p95_source_age_seconds=Decimal("1.20"),
        size_coverage_fraction=Decimal("0.95"),
        evidence_grade="A",
        sessions=(session,),
    )


def _stress_result():
    scenario = SimpleNamespace(
        scenario="base",
        slippage_bps=2,
        commission_per_order=Decimal("0.35"),
        session_count=20,
        compounded_return=Decimal("0.05"),
        degradation_vs_configured=Decimal("0.00"),
        maximum_drawdown=Decimal("0.02"),
        total_fills=10,
        commission_cost=Decimal("3.50"),
    )
    return SimpleNamespace(
        symbol="AAPL",
        worst_stressed_return=Decimal("-0.02"),
        worst_performance_degradation=Decimal("-0.07"),
        p95_top_of_book_participation=Decimal("0.10"),
        capacity_status="可接受",
        evidence_grade="B",
        scenarios=(scenario,),
    )


def _review_result():
    gate = SimpleNamespace(
        code="complete_sessions",
        name="完整会话",
        passed=True,
        observed="20",
        required="20",
        evidence="20/20",
        severity="hard",
        blocking=True,
    )
    return SimpleNamespace(
        run_id="review-run",
        symbol="AAPL",
        provider="IBKR",
        evidence_origins=("IBKR",),
        gates=(gate,),
        dependence=SimpleNamespace(
            oos_session_count=20,
            effective_sample_size_ar1=Decimal("10.0"),
            probability_mean_positive=Decimal("0.80"),
        ),
        passed_gates=1,
        blocking_failures=0,
        eligible_for_independent_review=True,
        decision="人工评审",
    )


def test_evidence_view_empty_state_has_all_seven_sections() -> None:
    view = evidence_view()
    assert view.replay_rows == ()
    assert "尚未运行" in view.robustness_summary
    assert view.robustness_rows == ()
    assert view.robustness_scenario_rows == ()
    assert "20" in view.walk_forward_summary
    assert view.overfit_rows == ()
    assert view.quality_rows == ()
    assert view.stress_rows == ()
    assert view.review_history_rows == ()
    assert view.review_gate_rows == ()


def test_evidence_view_projects_every_result_family() -> None:
    view = evidence_view(
        replay_results=(_replay_result(),),
        robustness_results=(_robustness_result(),),
        walk_forward_results=(_walk_forward_result(),),
        overfit_results=(_overfit_result(),),
        data_quality_results=(_quality_result(),),
        execution_stress_results=(_stress_result(),),
        review_results=(_review_result(),),
        selected_robustness_run_id="robustness-run",
        selected_review_run_id="review-run",
    )
    assert view.replay_rows[0].values[1] == "AAPL"
    assert view.robustness_rows[0].values[0] == "robustne"
    assert view.robustness_scenario_rows[0].values[0] == "base"
    assert view.walk_forward_rows[0].values[7] == "通过"
    assert view.overfit_rows[0].values[6] == "25.0%"
    assert view.quality_rows[0].values[10] == "通过"
    assert view.stress_rows[0].values[4] == "+5.00%"
    assert view.review_history_rows[0].values[0] == "review-r"
    assert view.review_gate_rows[0].values[1] == "通过"
    assert view.selected_robustness_run_id == "robustness-run"
    assert view.selected_review_run_id == "review-run"
