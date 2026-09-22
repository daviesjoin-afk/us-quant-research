"""Behaviour of the v2O-C3 backtest capability, without a window.

The capability is constructed with plain callables, so every rule it owns can be
asserted here -- refusal order, busy ownership, what a failure does to the last
good result -- without starting a Qt event loop or a worker thread.  What these
tests cannot prove (that the page button reaches the capability, that the window
holds no second copy) belongs to the wiring and architecture files.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from us_quant.backtest import BacktestResult
from us_quant.backtest_workspace import (
    STRATEGY_SPECS,
    BacktestMetrics,
    BacktestRequest,
    BacktestRun,
    StrategySpec,
)
from us_quant.desktop_v2.orchestration.research.backtest import (
    BACKTEST_RESOURCE_GROUP,
    BacktestOrchestrator,
)
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestFormDraft,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _version(
    strategy_id: str,
    *,
    semver: str = "1.0.0",
    version_id: str | None = None,
    parameters: dict | None = None,
) -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name=f"{strategy_id} name",
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=version_id or f"{strategy_id}-{semver}",
            parameter_hash=f"ph-{strategy_id}-{semver}",
        ),
        semver=semver,
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters=parameters if parameters is not None else {"window": 20},
        universe_hash="uh",
        code_hash=f"ch-{strategy_id}",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=True,
        gate_reason="",
        created_at=NOW,
        updated_at=NOW,
    )


def _draft(**overrides) -> BacktestFormDraft:
    base = dict(
        strategy_version_id="buy-hold-1.0.0",
        symbol="XLF",
        start_date=date(2018, 1, 1),
        end_date=date(2024, 1, 1),
        initial_equity=1500,
        target_weight_percent=100,
        per_share_commission="0.005",
        minimum_commission="1.0",
        slippage_bps="2.5",
    )
    base.update(overrides)
    return BacktestFormDraft(**base)


def _run(run_id: str, request: BacktestRequest | None = None) -> BacktestRun:
    request = request or BacktestRequest(
        strategy_id="buy-hold",
        strategy_version_id="buy-hold-1.0.0",
        parameter_hash="ph",
        code_hash="ch",
        parameters={},
        symbol="XLF",
        start_date=date(2018, 1, 1),
        end_date=date(2024, 1, 1),
        initial_equity=Decimal("1500"),
        target_weight=Decimal("1"),
        per_share_commission=Decimal("0.005"),
        minimum_commission=Decimal("1.0"),
        slippage_bps=Decimal("2.5"),
    )
    return BacktestRun(
        run_id=run_id,
        request=request,
        strategy=StrategySpec(
            strategy_id="buy-hold",
            name="买入并持有基准",
            description="",
            default_parameters={},
        ),
        data_source="local",
        data_hash="dh",
        price_basis="adjusted",
        first_date=date(2018, 1, 2),
        last_date=date(2023, 12, 29),
        result=BacktestResult(
            initial_equity=Decimal("1500"),
            final_equity=Decimal("1600"),
            total_return=Decimal("0.0667"),
            max_drawdown=Decimal("-0.10"),
            total_commission=Decimal("3.00"),
            trades=(),
            equity_curve=(),
        ),
        metrics=BacktestMetrics(
            annualized_return=Decimal("0.01"),
            annualized_sharpe=Decimal("0.5"),
            annualized_sortino=Decimal("0.6"),
            calmar_ratio=Decimal("0.7"),
            annualized_volatility=Decimal("0.1"),
            turnover=Decimal("1.0"),
            worst_day=Decimal("-0.02"),
            positive_day_ratio=Decimal("0.55"),
        ),
    )


class _Page:
    """Records what the capability asked the page to draw."""

    def __init__(self) -> None:
        self.renders: list[object] = []
        self.options: list[tuple] = []

    def render(self, view: object) -> None:
        self.renders.append(view)

    def set_strategy_options(self, options: tuple) -> None:
        self.options.append(options)


class _Service:
    def __init__(self, runs: tuple[BacktestRun, ...] = ()) -> None:
        self.runs = runs
        self.calls: list[tuple] = []

    def run(self, requests, *, on_progress=None):
        self.calls.append(tuple(requests))
        if on_progress is not None:
            for index, request in enumerate(requests, start=1):
                on_progress(index, len(requests), request)
        return self.runs


class _Submitter:
    """A ``TaskSubmitter`` that records instead of starting a thread.

    When it accepts, it runs the task on the spot and keeps the result.  That is
    what a real window does eventually -- the worker executes the task and the
    result reaches ``on_success`` -- but running it here rather than calling
    ``on_success`` keeps the outcome explicit: a test that wants to assert the
    success path drives ``_runs_finished`` itself.
    """

    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.submissions: list[tuple] = []
        self.results: list[object] = []

    def __call__(self, task, **kwargs) -> bool:
        self.submissions.append((task, kwargs))
        if not self.accept:
            return False
        self.results.append(task(lambda message: None))
        return True


def _orchestrator(
    *,
    service=None,
    page=None,
    submitter=None,
    available: bool = True,
    versions=(),
):
    page = page if page is not None else _Page()
    service = service if service is not None else _Service()
    submitter = submitter if submitter is not None else _Submitter()
    orchestrator = BacktestOrchestrator(
        service=service,
        page=page,
        submit_task=submitter,
        task_available=lambda group: available,
        strategy_versions_provider=lambda: tuple(versions),
    )
    return orchestrator, page, service, submitter


# -- initial state -----------------------------------------------------


def test_initial_state_is_empty_and_not_busy() -> None:
    orchestrator, _, _, _ = _orchestrator()
    assert orchestrator._runs == ()
    assert orchestrator._selected_run_id is None
    assert orchestrator._busy is False


# -- strategy options --------------------------------------------------


def test_strategy_options_keep_the_family_then_semver_rule() -> None:
    versions = (
        _version("rsi-mean-reversion", semver="1.0.0"),
        _version("buy-hold", semver="2.0.0"),
        _version("buy-hold", semver="1.0.0"),
        _version("unknown-family", semver="9.9.9"),
    )
    orchestrator, page, _, _ = _orchestrator(versions=versions)

    orchestrator.refresh_strategy_options()

    (options,) = page.options
    assert [option.version_id for option in options] == [
        "buy-hold-1.0.0",
        "buy-hold-2.0.0",
        "rsi-mean-reversion-1.0.0",
        "unknown-family-9.9.9",
    ]


def test_strategy_options_use_the_provider_and_never_a_service() -> None:
    """The catalogue arrives through the callable, read at refresh time."""

    page = _Page()
    seen: list[int] = []

    def provider() -> tuple[StrategyVersion, ...]:
        seen.append(1)
        return (_version("buy-hold"),)

    orchestrator = BacktestOrchestrator(
        service=_Service(),
        page=page,
        submit_task=_Submitter(),
        task_available=lambda group: True,
        strategy_versions_provider=provider,
    )

    orchestrator.refresh_strategy_options()
    orchestrator.refresh_strategy_options()

    assert seen == [1, 1]
    assert len(page.options) == 2


# -- version selection -------------------------------------------------


def test_single_run_selects_only_the_requested_version() -> None:
    versions = (
        _version("buy-hold", semver="1.0.0"),
        _version("buy-hold", semver="2.0.0"),
    )
    orchestrator, _, service, _ = _orchestrator(versions=versions)

    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))

    (requests,) = service.calls
    assert [request.strategy_version_id for request in requests] == [
        "buy-hold-1.0.0"
    ]


def test_a_single_run_with_an_unknown_version_is_refused() -> None:
    orchestrator, _, service, submitter = _orchestrator(
        versions=(_version("buy-hold"),)
    )
    refusals: list[tuple[str, str, str]] = []
    orchestrator.refused.connect(
        lambda level, title, message: refusals.append((level, title, message))
    )

    orchestrator.request_selected(_draft(strategy_version_id="missing"))

    assert service.calls == []
    assert submitter.submissions == []
    assert refusals[0][1] == "没有可运行版本"
    assert refusals[0][0] == "warning"


def test_compare_all_takes_the_newest_version_per_family() -> None:
    """Newest means *first in the provider's list*, per family."""

    versions = (
        _version("buy-hold", semver="2.0.0", version_id="buy-hold-new"),
        _version("buy-hold", semver="1.0.0", version_id="buy-hold-old"),
        _version(
            "dual-ma-trend", semver="3.0.0", version_id="dual-ma-new"
        ),
        _version(
            "dual-ma-trend", semver="1.0.0", version_id="dual-ma-old"
        ),
    )
    orchestrator, _, service, _ = _orchestrator(versions=versions)

    orchestrator.request_compare_all(_draft())

    (requests,) = service.calls
    assert [request.strategy_version_id for request in requests] == [
        "buy-hold-new",
        "dual-ma-new",
    ]


def test_compare_all_orders_the_batch_by_strategy_specs() -> None:
    """The comparison table reads in ``STRATEGY_SPECS`` order, not input order."""

    versions = (
        _version("rsi-mean-reversion", version_id="rsi"),
        _version("donchian-breakout", version_id="donchian"),
        _version("buy-hold", version_id="buy-hold"),
        _version("dual-ma-trend", version_id="dual-ma"),
    )
    orchestrator, _, service, _ = _orchestrator(versions=versions)

    orchestrator.request_compare_all(_draft())

    (requests,) = service.calls
    assert [request.strategy_id for request in requests] == [
        spec.strategy_id for spec in STRATEGY_SPECS
    ]


def test_compare_all_ignores_the_selected_version_id() -> None:
    versions = (
        _version("buy-hold", version_id="buy-hold-v1"),
        _version("dual-ma-trend", version_id="dual-ma-v1"),
    )
    orchestrator, _, service, _ = _orchestrator(versions=versions)

    orchestrator.request_compare_all(
        _draft(strategy_version_id="dual-ma-v1")
    )

    (requests,) = service.calls
    assert len(requests) == 2


# -- request construction ----------------------------------------------


def test_requests_keep_every_field_and_decimal_precision() -> None:
    version = _version(
        "buy-hold", parameters={"short_window": 20, "label": "x"}
    )
    orchestrator, _, service, _ = _orchestrator(versions=(version,))

    orchestrator.request_selected(
        _draft(
            strategy_version_id=version.version_id,
            symbol="MSFT",
            start_date=date(2019, 3, 4),
            end_date=date(2021, 6, 7),
            initial_equity=250_000,
            target_weight_percent=35,
            per_share_commission="0.0075",
            minimum_commission="1.25",
            slippage_bps="3.5",
        )
    )

    (requests,) = service.calls
    (request,) = requests
    assert request.strategy_id == "buy-hold"
    assert request.strategy_version_id == version.version_id
    assert request.parameter_hash == version.parameter_hash
    assert request.code_hash == version.code_hash
    assert request.parameters == version.parameters
    assert request.symbol == "MSFT"
    assert request.start_date == date(2019, 3, 4)
    assert request.end_date == date(2021, 6, 7)
    assert request.initial_equity == Decimal("250000")
    assert request.target_weight == Decimal("0.35")
    assert request.per_share_commission == Decimal("0.0075")
    assert request.minimum_commission == Decimal("1.25")
    assert request.slippage_bps == Decimal("3.5")
    assert all(
        isinstance(value, Decimal)
        for value in (
            request.initial_equity,
            request.target_weight,
            request.per_share_commission,
            request.minimum_commission,
            request.slippage_bps,
        )
    )


# -- refusal order -----------------------------------------------------


def test_busy_is_checked_before_anything_else() -> None:
    """A busy resource group is reported before the version/date checks.

    The draft is *also* invalid, so if the order were reversed the operator
    would be sent to fix a form that was never read.
    """

    orchestrator, _, service, submitter = _orchestrator(
        available=False, versions=()
    )
    refusals: list[tuple[str, str, str]] = []
    orchestrator.refused.connect(
        lambda level, title, message: refusals.append((level, title, message))
    )

    orchestrator.request_selected(
        _draft(
            strategy_version_id="missing",
            start_date=date(2030, 1, 1),
            end_date=date(2029, 1, 1),
        )
    )

    assert refusals == [
        ("information", "任务忙", "请等待当前数据或研究任务完成后再运行回测。")
    ]
    assert service.calls == []
    assert submitter.submissions == []


def test_invalid_date_is_refused_before_the_service() -> None:
    orchestrator, _, service, submitter = _orchestrator(
        versions=(_version("buy-hold"),)
    )
    refusals: list[tuple[str, str, str]] = []
    orchestrator.refused.connect(
        lambda level, title, message: refusals.append((level, title, message))
    )

    orchestrator.request_selected(
        _draft(
            strategy_version_id="buy-hold-1.0.0",
            start_date=date(2030, 1, 1),
            end_date=date(2029, 1, 1),
        )
    )

    assert refusals == [("warning", "日期无效", "起始日期不能晚于结束日期。")]
    assert service.calls == []
    assert submitter.submissions == []


def test_no_eligible_version_is_checked_before_the_date() -> None:
    orchestrator, _, _, _ = _orchestrator(versions=())
    refusals: list[tuple[str, str, str]] = []
    orchestrator.refused.connect(
        lambda level, title, message: refusals.append((level, title, message))
    )

    orchestrator.request_selected(
        _draft(
            strategy_version_id="missing",
            start_date=date(2030, 1, 1),
            end_date=date(2029, 1, 1),
        )
    )

    assert refusals[0][1] == "没有可运行版本"


# -- busy ownership ----------------------------------------------------


def test_admission_sets_busy_and_disables_the_page_controls() -> None:
    orchestrator, page, _, submitter = _orchestrator(
        versions=(_version("buy-hold"),)
    )

    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))

    assert orchestrator._busy is True
    assert page.renders[-1].controls.run_selected_enabled is False
    assert page.renders[-1].controls.compare_all_enabled is False
    assert len(submitter.submissions) == 1


def test_the_submission_uses_the_backtest_resource_group() -> None:
    orchestrator, _, _, submitter = _orchestrator(
        versions=(_version("buy-hold"),)
    )

    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))

    (_, kwargs) = submitter.submissions[0]
    assert kwargs["resource_group"] == BACKTEST_RESOURCE_GROUP
    assert kwargs["start_message"] == "正在运行 1 个版本绑定回测…"


def test_a_rejected_submission_rolls_the_busy_flag_back() -> None:
    """``False`` means the task never started, so the flag must come down."""

    orchestrator, page, _, _ = _orchestrator(
        versions=(_version("buy-hold"),),
        submitter=_Submitter(accept=False),
    )

    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))

    assert orchestrator._busy is False
    assert page.renders[-1].controls.run_selected_enabled is True
    assert [view.controls.run_selected_enabled for view in page.renders] == [
        False,
        True,
    ]


def test_failure_releases_busy_and_keeps_the_last_good_runs() -> None:
    previous = (_run("run-A"), _run("run-B"))
    orchestrator, page, _, _ = _orchestrator(
        service=_Service(previous), versions=(_version("buy-hold"),)
    )
    orchestrator._runs = previous
    orchestrator._selected_run_id = "run-A"
    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))
    assert orchestrator._busy is True

    orchestrator._runs_failed("boom")

    assert orchestrator._busy is False
    assert orchestrator._runs == previous
    assert orchestrator._selected_run_id == "run-A"
    assert page.renders[-1].runs


def test_success_replaces_runs_selects_the_first_and_logs() -> None:
    runs = (_run("run-A"), _run("run-B"))
    orchestrator, page, _, _ = _orchestrator(
        service=_Service(runs), versions=(_version("buy-hold"),)
    )
    logs: list[str] = []
    orchestrator.log_requested.connect(logs.append)

    orchestrator.request_selected(_draft(strategy_version_id="buy-hold-1.0.0"))
    orchestrator._runs_finished(runs)

    assert orchestrator._busy is False
    assert orchestrator._runs == runs
    assert orchestrator._selected_run_id == "run-A"
    assert logs == ["回测完成：2 个不可变 run 已保存到用户研究目录"]


def test_success_with_no_runs_selects_nothing() -> None:
    orchestrator, _, _, _ = _orchestrator(versions=(_version("buy-hold"),))

    orchestrator._runs_finished(())

    assert orchestrator._runs == ()
    assert orchestrator._selected_run_id is None
    assert orchestrator._busy is False


@pytest.mark.parametrize(
    "bad_result",
    [
        pytest.param(object(), id="not-a-sequence"),
        pytest.param(("not-a-run",), id="sequence-of-non-runs"),
        pytest.param(None, id="none"),
        pytest.param(42, id="int"),
    ],
)
def test_a_wrong_result_releases_busy_and_preserves_last_good(
    bad_result: object,
) -> None:
    """A wrong success result must not wedge the capability.

    This handler runs as the worker's ``succeeded`` slot, so an exception here
    escapes the signal emission and the generic cleanup that follows releases
    the *worker* -- not this capability's busy flag.  Raising before releasing
    it would therefore leave ``_busy`` set forever, with the run buttons
    disabled and no task left to clear them.

    The state below is the real one: a batch is genuinely in flight
    (``_busy`` True) and a previous good result is on the page, so this asserts
    the three things at once -- the failure is loud, the last good result
    survives, and the controls come back.
    """

    last_good = (_run("run-A"), _run("run-B"))
    orchestrator, page, _, _ = _orchestrator(
        versions=(_version("buy-hold"),)
    )
    orchestrator._runs = last_good
    orchestrator._selected_run_id = "run-A"
    orchestrator._busy = True
    logs: list[str] = []
    orchestrator.log_requested.connect(logs.append)

    with pytest.raises(TypeError):
        orchestrator._runs_finished(bad_result)

    # Loud, not swallowed.
    assert logs == []
    # The capability is operable again.
    assert orchestrator._busy is False
    assert page.renders[-1].controls.run_selected_enabled is True
    assert page.renders[-1].controls.compare_all_enabled is True
    # The last good result is intact and still displayed.
    assert orchestrator._runs == last_good
    assert orchestrator._selected_run_id == "run-A"
    assert page.renders[-1].selected_run_id == "run-A"
    assert page.renders[-1].detail.run_id == "run-A"


def test_a_mixed_batch_releases_busy_and_preserves_last_good() -> None:
    """The partial case: a real run mixed with a non-run is still invalid.

    Worth its own test rather than a parametrized case, because the guard is
    ``all(...)``: an implementation that checked only the first element would
    pass the empty/None cases above and fail here.
    """

    last_good = (_run("run-A"),)
    orchestrator, page, _, _ = _orchestrator(
        versions=(_version("buy-hold"),)
    )
    orchestrator._runs = last_good
    orchestrator._selected_run_id = "run-A"
    orchestrator._busy = True
    logs: list[str] = []
    orchestrator.log_requested.connect(logs.append)

    with pytest.raises(TypeError):
        orchestrator._runs_finished((_run("run-B"), object()))

    assert logs == []
    assert orchestrator._busy is False
    assert page.renders[-1].controls.run_selected_enabled is True
    assert orchestrator._runs == last_good
    assert orchestrator._selected_run_id == "run-A"


def test_a_valid_result_is_still_committed() -> None:
    """The positive half: the reordering did not turn success into failure."""

    orchestrator, page, _, _ = _orchestrator(
        versions=(_version("buy-hold"),)
    )
    orchestrator._busy = True
    runs = (_run("run-A"), _run("run-B"))

    orchestrator._runs_finished(runs)

    assert orchestrator._busy is False
    assert orchestrator._runs == runs
    assert orchestrator._selected_run_id == "run-A"
    assert page.renders[-1].controls.run_selected_enabled is True


# -- selection ---------------------------------------------------------


def test_selecting_a_run_changes_the_detail_and_renders() -> None:
    runs = (_run("run-A"), _run("run-B"))
    orchestrator, page, _, _ = _orchestrator(versions=(_version("buy-hold"),))
    orchestrator._runs = runs
    orchestrator._selected_run_id = "run-A"

    orchestrator.select_run("run-B")

    assert orchestrator._selected_run_id == "run-B"
    assert page.renders[-1].selected_run_id == "run-B"
    assert page.renders[-1].detail.run_id == "run-B"


def test_selecting_an_unknown_run_falls_back_to_the_first() -> None:
    orchestrator, page, _, _ = _orchestrator(versions=(_version("buy-hold"),))
    orchestrator._runs = (_run("run-A"), _run("run-B"))

    orchestrator.select_run("gone")

    assert orchestrator._selected_run_id == "gone"
    assert page.renders[-1].detail.run_id == "run-A"


def test_render_never_starts_a_run() -> None:
    orchestrator, _, service, submitter = _orchestrator(
        versions=(_version("buy-hold"),)
    )

    orchestrator.render_current()
    orchestrator.select_run("anything")

    assert service.calls == []
    assert submitter.submissions == []
