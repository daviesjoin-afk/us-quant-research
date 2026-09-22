"""Unit tests for ``ScannerOrchestrator``.

The orchestrator is driven with a recording service, a recording page and a
recording ``submit_task`` -- no ``MainWindow``, no threads, no Qt widgets beyond
the ``QObject`` the signals need, and no real scan JSON.

The subject is the *decision* the capability makes: which of the three arrival
paths publishes what, when the universe and the run inputs are read, and what a
chart failure does.  The window's half of the deal is asserted separately in
``test_desktop_scanner_wiring.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.orchestration.research.scanner import (
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    SCAN_PROGRESS_MESSAGE,
    SCAN_RESOURCE_GROUP,
    SCAN_START_MESSAGE,
    ScannerOrchestrator,
    ScannerRunInputs,
)
from us_quant.scanner import MarketScan, ScanResult
from us_quant.universe import UniverseRecord, UniverseSnapshot

_APP = QApplication.instance() or QApplication([])


def _result(symbol: str = "AAPL") -> ScanResult:
    return ScanResult(
        symbol=symbol,
        execution_symbol=symbol,
        name=f"{symbol} Inc.",
        sector="Technology",
        leader_tier=1,
        security_type="STK",
        trading_date=date(2026, 9, 18),
        close=100.0,
        execution_price=100.0,
        whole_share_capacity=10,
        average_dollar_volume_20d=1_000_000.0,
        return_20d=0.01,
        return_63d=0.02,
        volatility_20d=0.2,
        drawdown_252d=-0.1,
        rsi_14d=55.0,
        atr_pct_14d=0.02,
        above_sma_50=True,
        above_sma_200=True,
        score=50.0,
        signal="观察",
        research_eligible=True,
        trade_eligible=False,
        reason="test",
    )


def _scan(results: int = 1, skipped: int = 0) -> MarketScan:
    return MarketScan(
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        capital=1500.0,
        data_date=date(2026, 9, 18),
        results=tuple(_result(f"S{i:02d}") for i in range(results)),
        skipped={f"K{i}": "missing" for i in range(skipped)},
    )


def _universe(research_eligible: int = 3) -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=tuple(
            UniverseRecord(
                symbol=f"S{i:02d}",
                name=f"S{i:02d}",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            )
            for i in range(research_eligible)
        ),
    )


class _Page:
    """The scanner page: records renders and chart paints."""

    def __init__(self) -> None:
        self.views: list = []
        self.charts: list = []

    def render(self, view) -> None:  # noqa: ANN001
        self.views.append(view)

    def render_chart(self, view) -> None:  # noqa: ANN001
        self.charts.append(view)


class _Service:
    """The scanner data boundary: records scans, restores and chart reads."""

    def __init__(
        self,
        *,
        scan_result: object = None,
        saved: object = None,
        saved_raises: Exception | None = None,
        chart: object = None,
        chart_raises: Exception | None = None,
    ) -> None:
        self.scan_result = scan_result
        self.saved = saved
        self.saved_raises = saved_raises
        self.chart = chart
        self.chart_raises = chart_raises
        self.scan_calls: list[dict] = []
        self.load_saved_calls = 0
        self.load_chart_calls: list[str] = []

    def scan(self, universe, **kwargs):  # noqa: ANN001
        self.scan_calls.append({"universe": universe, **kwargs})
        return self.scan_result

    def load_saved(self):  # noqa: ANN201
        self.load_saved_calls += 1
        if self.saved_raises is not None:
            raise self.saved_raises
        return self.saved

    def load_chart(self, symbol: str):  # noqa: ANN201
        self.load_chart_calls.append(symbol)
        if self.chart_raises is not None:
            raise self.chart_raises
        return self.chart


class _Submit:
    """Records admissions; never runs the task."""

    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.calls: list[dict] = []

    def __call__(self, task, **kwargs):  # noqa: ANN001
        self.calls.append({"task": task, **kwargs})
        return self.admitted

    @property
    def last(self) -> dict:
        assert self.calls, "submit_task was never called"
        return self.calls[-1]


def _inputs(
    *,
    capital: str = "1500",
    risk: str = "0.10",
    substitutions: dict | None = None,
) -> ScannerRunInputs:
    return ScannerRunInputs.of(
        capital=Decimal(capital),
        max_position_risk_pct=Decimal(risk),
        substitutions=substitutions or {},
    )


def _build(
    *,
    universe: object | None = None,
    service: _Service | None = None,
    admitted: bool = True,
    inputs: ScannerRunInputs | None = None,
):
    page = _Page()
    service = service or _Service(scan_result=_scan())
    submit = _Submit(admitted)
    universes = [universe]
    input_sets = [inputs or _inputs()]
    input_calls: list[int] = []

    def run_inputs() -> ScannerRunInputs:
        input_calls.append(1)
        return input_sets[-1]

    orchestrator = ScannerOrchestrator(
        service=service,
        page=page,
        submit_task=submit,
        universe_provider=lambda: universes[0],
        run_inputs_provider=run_inputs,
    )
    return (
        orchestrator,
        page,
        service,
        submit,
        universes,
        input_sets,
        input_calls,
    )


def _changes(orchestrator: ScannerOrchestrator) -> list:
    seen: list = []
    orchestrator.scan_changed.connect(seen.append)
    return seen


def _logs(orchestrator: ScannerOrchestrator) -> list[str]:
    seen: list[str] = []
    orchestrator.log_requested.connect(seen.append)
    return seen


def _refusals(orchestrator: ScannerOrchestrator) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    orchestrator.refused.connect(
        lambda title, message: seen.append((title, message))
    )
    return seen


# -- the initial fact ---------------------------------------------------


def test_the_initial_scan_is_none() -> None:
    """Before anything happens there is no scan, and the page says so."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build()

    assert orchestrator.scan is None
    assert page.views == []


# -- restore_saved ------------------------------------------------------


def test_restoring_a_valid_scan_seeds_the_truth_and_paints_once() -> None:
    """Adoption stores the fact and paints exactly once."""

    scan = _scan(results=2)
    orchestrator, page, service, _submit, _u, _i, _c = _build(
        service=_Service(saved=scan)
    )
    changes = _changes(orchestrator)

    orchestrator.restore_saved()

    assert orchestrator.scan is scan
    assert service.load_saved_calls == 1
    assert len(page.views) == 1
    assert len(page.views[0].rows) == 2


def test_restoring_announces_nothing() -> None:
    """Re-reading a local file is not a new scan.

    No ``scan_changed`` and no log line: neither the market-scope summary nor
    the operator should be told a scan just completed when the process only
    re-read an artifact.
    """

    orchestrator, _page, _service, _submit, _u, _i, _c = _build(
        service=_Service(saved=_scan())
    )
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)

    orchestrator.restore_saved()

    assert changes == []
    assert logs == []


def test_restoring_a_missing_artifact_leaves_no_scan_and_still_paints() -> None:
    """The ordinary first-run state: nothing cached, and a painted page."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build(
        service=_Service(saved=None)
    )

    orchestrator.restore_saved()

    assert orchestrator.scan is None
    assert len(page.views) == 1
    assert page.views[0].has_scan is False


def test_a_malformed_artifact_does_not_stop_startup() -> None:
    """A corrupt scan file must not prevent the desktop from starting.

    The service raises; the capability degrades to "no scan" and paints once,
    which is the same tolerance the window had when it parsed the JSON itself.
    """

    orchestrator, page, service, _submit, _u, _i, _c = _build(
        service=_Service(saved_raises=ValueError("bad json"))
    )
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)

    orchestrator.restore_saved()

    assert orchestrator.scan is None
    assert service.load_saved_calls == 1
    assert len(page.views) == 1
    assert changes == []
    assert logs == []


def test_restoring_paints_exactly_once_even_when_it_succeeds() -> None:
    """The exactly-once rule, pinned separately from the value assertions.

    ``restore_saved`` is the single painter on this path; a caller that also
    painted would rebuild the whole table for nothing.
    """

    orchestrator, page, _service, _submit, _u, _i, _c = _build(
        service=_Service(saved=_scan())
    )

    orchestrator.restore_saved()

    assert len(page.views) == 1


# -- request_scan: refusal ---------------------------------------------


def test_requesting_a_scan_without_a_universe_is_refused() -> None:
    """Refuse, and do not dial.  The window owns the dialog."""

    orchestrator, page, service, submit, _u, _i, _c = _build(universe=None)
    refusals = _refusals(orchestrator)

    orchestrator.request_scan()

    assert refusals == [(MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE)]
    assert submit.calls == []
    assert service.scan_calls == []
    assert page.views == []


def test_a_refused_request_does_not_freeze_the_run_inputs() -> None:
    """Nothing was scanned, so nothing should have been read for it."""

    orchestrator, _page, _service, _submit, _u, _i, input_calls = _build(
        universe=None
    )

    orchestrator.request_scan()

    assert input_calls == []


# -- request_scan: admission and timing --------------------------------


def test_the_scan_task_contract_is_unchanged() -> None:
    """The progress line, start message and resource group are the same."""

    orchestrator, _page, _service, submit, _u, _i, _c = _build(
        universe=_universe()
    )

    orchestrator.request_scan()

    assert submit.last["start_message"] == SCAN_START_MESSAGE
    assert submit.last["resource_group"] == SCAN_RESOURCE_GROUP
    assert submit.last["on_success"] == orchestrator._scan_finished
    assert set(submit.last) == {
        "task",
        "on_success",
        "start_message",
        "resource_group",
    }

    seen: list[str] = []
    submit.last["task"](seen.append)
    assert seen == [SCAN_PROGRESS_MESSAGE]


def test_the_run_inputs_are_frozen_at_request_time() -> None:
    """The capital and risk the operator saw are the ones the scan uses."""

    orchestrator, _page, _service, submit, _u, input_sets, input_calls = _build(
        universe=_universe(),
        inputs=_inputs(capital="1500", risk="0.10"),
    )

    orchestrator.request_scan()

    # The provider is called once, before the task is submitted...
    assert input_calls == [1]

    # ...and a later edit is not what the task runs with.
    input_sets[-1] = _inputs(capital="9999", risk="0.99")
    submit.last["task"](lambda _message: None)

    assert _service.scan_calls[0]["capital"] == Decimal("1500")
    assert _service.scan_calls[0]["max_position_risk_pct"] == Decimal("0.10")


def test_the_universe_is_read_again_at_execution_time() -> None:
    """A refresh that landed while the task queued is the universe scanned."""

    first = _universe(1)
    second = _universe(4)
    orchestrator, _page, service, submit, universes, _i, _c = _build(
        universe=first
    )

    orchestrator.request_scan()
    universes[0] = second
    submit.last["task"](lambda _message: None)

    assert service.scan_calls[0]["universe"] is second


def test_the_substitutions_are_passed_as_a_mapping() -> None:
    """The frozen tuple is projected back to the mapping the scanner takes."""

    rule = object()
    orchestrator, _page, service, submit, _u, _i, _c = _build(
        universe=_universe(),
        inputs=_inputs(substitutions={"AAPL": rule}),
    )

    orchestrator.request_scan()
    submit.last["task"](lambda _message: None)

    assert service.scan_calls[0]["substitutions"] == {"AAPL": rule}
    assert service.scan_calls[0]["substitutions"] is not (
        _inputs().substitutions_mapping()
    )


def test_a_universe_that_vanishes_before_execution_fails_closed() -> None:
    """Scanning the captured universe would scan something nobody can see."""

    orchestrator, _page, service, submit, universes, _i, _c = _build(
        universe=_universe()
    )

    orchestrator.request_scan()
    universes[0] = None

    with pytest.raises(RuntimeError):
        submit.last["task"](lambda _message: None)

    assert service.scan_calls == []


def test_a_refused_admission_is_not_treated_as_a_scan() -> None:
    """A refused duplicate must not leave the capability believing it scanned."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build(
        universe=_universe(), admitted=False
    )
    changes = _changes(orchestrator)

    orchestrator.request_scan()

    assert orchestrator.scan is None
    assert changes == []
    assert page.views == []


# -- request_scan: success ---------------------------------------------


def test_a_successful_manual_scan_becomes_the_canonical_truth() -> None:
    """Identity, not equality: the stored scan is the one the service returned."""

    scan = _scan(results=2, skipped=1)
    orchestrator, page, _service, submit, _u, _i, _c = _build(
        universe=_universe(), service=_Service(scan_result=scan)
    )
    changes = _changes(orchestrator)

    orchestrator.request_scan()
    submit.last["task"](lambda _message: None)
    submit.last["on_success"](scan)

    assert orchestrator.scan is scan
    assert changes == [scan]
    assert len(page.views) == 1
    assert len(page.views[0].rows) == 2


def test_a_manual_scan_writes_the_completion_log() -> None:
    """The manual path is the only one that announces itself."""

    scan = _scan(results=3, skipped=2)
    orchestrator, _page, _service, submit, _u, _i, _c = _build(
        universe=_universe()
    )
    logs = _logs(orchestrator)

    orchestrator.request_scan()
    submit.last["on_success"](scan)

    assert logs == ["扫描完成：3 个，趋势候选 0 个。"]


def test_a_wrong_result_type_is_rejected() -> None:
    """The completion handler is typed, so a stray object cannot become truth."""

    orchestrator, _page, _service, submit, _u, _i, _c = _build(
        universe=_universe()
    )

    orchestrator.request_scan()

    with pytest.raises(TypeError):
        submit.last["on_success"](object())
    assert orchestrator.scan is None


# -- adopt_external_scan -----------------------------------------------


def test_an_adopted_scan_becomes_the_canonical_truth() -> None:
    """A finished fact from another workflow moves the truth and repaints."""

    scan = _scan(results=2)
    orchestrator, page, _service, _submit, _u, _i, _c = _build()
    changes = _changes(orchestrator)

    orchestrator.adopt_external_scan(scan)

    assert orchestrator.scan is scan
    assert changes == [scan]
    assert len(page.views) == 1
    assert len(page.views[0].rows) == 2


def test_an_adopted_scan_writes_no_manual_completion_log() -> None:
    """Nobody clicked 扫描, so the manual line would be a lie."""

    orchestrator, _page, _service, _submit, _u, _i, _c = _build()
    logs = _logs(orchestrator)

    orchestrator.adopt_external_scan(_scan())

    assert logs == []


def test_adoption_and_restoration_are_different_entry_points() -> None:
    """Two methods with two semantics, and no boolean-flag API.

    Restoration publishes nothing; adoption publishes.  Collapsing them into
    ``set_scan(scan, emit=..., log=...)`` would make the side effects guessable
    only by reading the call site, which is exactly what this round forbids.
    """

    assert hasattr(ScannerOrchestrator, "restore_saved")
    assert hasattr(ScannerOrchestrator, "adopt_external_scan")
    for forbidden in ("set_scan", "update_state"):
        assert not hasattr(ScannerOrchestrator, forbidden)


# -- request_chart ------------------------------------------------------


def test_a_chart_request_paints_the_series() -> None:
    points = ((date(2026, 9, 18), 10.0),)
    orchestrator, page, service, _submit, _u, _i, _c = _build(
        service=_Service(chart=points)
    )

    orchestrator.request_chart("AAPL")

    assert service.load_chart_calls == ["AAPL"]
    assert len(page.charts) == 1
    assert page.charts[0].symbol == "AAPL"
    assert page.charts[0].points == points


def test_a_chart_failure_only_logs() -> None:
    """A failed read is a status line, not a dialog and not a cleared chart."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build(
        service=_Service(chart_raises=FileNotFoundError("nope"))
    )
    logs = _logs(orchestrator)

    orchestrator.request_chart("AAPL")

    assert page.charts == []
    assert logs == ["AAPL 图表读取失败：nope"]


def test_a_chart_failure_does_not_clear_an_existing_chart() -> None:
    """The chart that is already drawn must survive a failed re-read."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build(
        service=_Service(chart=((date(2026, 9, 18), 10.0),))
    )
    orchestrator.request_chart("AAPL")

    orchestrator._service.chart_raises = FileNotFoundError("nope")
    orchestrator.request_chart("MSFT")

    assert len(page.charts) == 1
    assert page.charts[0].symbol == "AAPL"


# -- render_current -----------------------------------------------------


def test_the_research_count_prefers_the_universe() -> None:
    """The universe is the intended scope; the scan is only what scored."""

    orchestrator, page, _service, _submit, universes, _i, _c = _build(
        universe=_universe(7)
    )
    orchestrator.adopt_external_scan(_scan(results=2, skipped=5))

    orchestrator.render_current()

    assert page.views[-1].coverage.research_count == 7


def test_the_research_count_falls_back_to_the_scan() -> None:
    """Without a universe, the rows plus the skipped names are the count."""

    orchestrator, page, _service, _submit, _u, _i, _c = _build(universe=None)
    orchestrator.adopt_external_scan(_scan(results=2, skipped=5))

    orchestrator.render_current()

    assert page.views[-1].coverage.research_count == 7


def test_the_research_count_is_zero_with_neither() -> None:
    orchestrator, page, _service, _submit, _u, _i, _c = _build(universe=None)

    orchestrator.render_current()

    assert page.views[-1].coverage.research_count == 0
    assert page.views[-1].has_scan is False


def test_render_current_never_fetches() -> None:
    """A render that could start a read would be a second scan path."""

    orchestrator, _page, service, _submit, _u, _i, _c = _build(
        universe=_universe(), service=_Service(saved=_scan())
    )

    orchestrator.render_current()
    orchestrator.render_current()

    assert service.scan_calls == []
    assert service.load_saved_calls == 0


# -- the run-inputs value object ---------------------------------------


def test_the_run_inputs_are_immutable() -> None:
    """The freeze point is a property of the data, not a convention."""

    inputs = _inputs(substitutions={"AAPL": object()})

    with pytest.raises(Exception):
        inputs.capital = Decimal("1")  # type: ignore[misc]
    with pytest.raises(Exception):
        inputs.substitutions = ()  # type: ignore[misc]


def test_the_run_inputs_projection_is_a_fresh_mapping() -> None:
    """The caller cannot mutate the frozen inputs through the projection."""

    inputs = _inputs(substitutions={"AAPL": object()})

    first = inputs.substitutions_mapping()
    first["MSFT"] = object()

    assert set(inputs.substitutions_mapping()) == {"AAPL"}


def test_the_run_inputs_keep_the_decimals_they_were_given() -> None:
    """No coercion: the values the window produced are passed through."""

    capital = Decimal("1500")
    risk = Decimal("0.07")

    inputs = ScannerRunInputs.of(
        capital=capital, max_position_risk_pct=risk
    )

    assert inputs.capital is capital
    assert inputs.max_position_risk_pct is risk
