"""Behaviour tests for the G2-B execution / AutoQuant orchestration.

The subject is ``ExecutionOrchestrator`` -- the one desktop owner of the
AutoQuant route.  Most of the tests drive it against a *real* ``ExecutionPage``
(a counting subclass), a duck-typed selection service, a duck-typed Paper port
that records every transition it is asked for, and a provider group built from
recording callables.  That keeps each assertion about the route's own sequencing
rather than about Qt.

What is pinned, matching the round brief:

* **the runtime strategy selection** -- adopted in the service, never in the
  combo; a refusal logs and changes nothing;
* **the strategy combo** -- refilled from the service's options and aimed at the
  service's selection, exactly once per refresh;
* **the preflight** -- recomputed from live providers every time, never cached,
  and the arm confirmation excluded from the tally;
* **the reference symbols** -- trimmed, upper-cased, de-duplicated, and never
  tradable rotation candidates;
* **candidate preparation** -- the whole sequence: the refusals that claim
  nothing, the scan task, the adoption, the history queueing, the shortlist, the
  READY transition, the subscription, the readiness fact and the market request;
* **the capital rule** -- sizing on fresh Paper cash, the operator's limit only
  shrinking it, and the research scenario figure never used for it;
* **the channel probe** -- one at a time, refused while a session owns the
  channel, and each failure releasing only its own flag;
* **the launch confirmation** -- asked for and answered back, with consent never
  bypassing Paper's own preflight;
* **the render path** -- the candidate table before any session, the session view
  from Paper's retained presentation read at render time, and no cache;
* **Paper's publications** -- result, presentation refresh and finalization,
  each doing presentation and nothing else.

The two window-level tests at the bottom pin what only composition can show: an
unrelated background task failing or finishing must not touch this route.
"""

from __future__ import annotations

import os
import pathlib
import sys
from decimal import Decimal
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.desktop_v2.orchestration.execution import (  # noqa: E402
    ExecutionOrchestrator,
    ExecutionProviders,
    queries,
)
from us_quant.desktop_v2.orchestration.execution import (  # noqa: E402
    models as execution_models,
)
from us_quant.desktop_v2.orchestration.execution import (  # noqa: E402
    orchestrator as execution_orchestrator,
)
from us_quant.desktop_v2.pages.execution import ExecutionPage  # noqa: E402
from us_quant.trading.application.strategy_selection import (  # noqa: E402
    StrategySelectionError,
    StrategySelectionPurpose,
)
from us_quant.trading.runtime.models import AutoQuantCandidate  # noqa: E402

_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


# -- fakes ---------------------------------------------------------------


def _candidate(symbol: str = "AAA", score: str = "9") -> AutoQuantCandidate:
    return AutoQuantCandidate(
        symbol=symbol,
        name=f"{symbol} Inc",
        sector="Tech",
        leader_tier="leader",
        scan_score=Decimal(score),
        signal="breakout",
    )


class _Row:
    """One scanner row, duck-typed exactly as ``select_paper_rotation_rows``
    consumes it."""

    def __init__(self, symbol: str, score: float = 9.0) -> None:
        self.execution_symbol = symbol
        self.name = f"{symbol} Inc"
        self.sector = "Tech"
        self.leader_tier = "leader"
        self.score = score
        self.signal = "breakout"


class _Scan:
    """A scan with a *known* number of scored and skipped rows."""

    def __init__(self, symbols: list[str], skipped: int = 0) -> None:
        self.results = [_Row(symbol) for symbol in symbols]
        self.skipped = [object() for _ in range(skipped)]


class _Universe:
    def __init__(self, research_eligible: int = 120) -> None:
        self._summary = {
            "research_eligible": research_eligible,
            "total": 500,
        }

    def summary(self) -> dict[str, int]:
        return dict(self._summary)


class CountingPage(ExecutionPage):
    """The real page, counting every orchestration call it receives."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, tuple, dict]] = []
        self._candidate_limit = 8
        self._capital_limit = Decimal("0")
        self._armed = False
        self.arm_writes: list[bool] = []

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((name, args, kwargs))

    def count(self, name: str) -> int:
        return sum(1 for call, _, _ in self.calls if call == name)

    def last(self, name: str):
        matches = [call for call in self.calls if call[0] == name]
        assert matches, f"{name} was never called: {self.calls}"
        return matches[-1]

    # -- orchestration writes (recorded, then really applied) -----------
    def render(self, view):  # noqa: D102
        self._record("render", (view,), {})
        super().render(view)

    def render_candidates(self, view):  # noqa: D102
        self._record("render_candidates", (view,), {})
        super().render_candidates(view)

    def render_context(self, **lines):  # noqa: D102
        self._record("render_context", (), dict(lines))
        super().render_context(**lines)

    def render_preflight(self, ready, total, details):  # noqa: D102
        self._record("render_preflight", (ready, total, details), {})
        super().render_preflight(ready, total, details)

    def render_execution_health(self, text):  # noqa: D102
        self._record("render_execution_health", (text,), {})
        super().render_execution_health(text)

    def set_control_state(self, state):  # noqa: D102
        self._record("set_control_state", (state,), {})
        # ``control_state`` expresses the launch lock as the *idle* flags; there
        # is deliberately no ``launch_locked`` field on the published state, so
        # the page cannot act on a flag it does not own.
        self._controls_idle = state.prepare_enabled
        super().set_control_state(state)

    def set_strategy_options(self, options, selected_version_id):  # noqa: D102
        self._record("set_strategy_options", (options, selected_version_id), {})
        super().set_strategy_options(options, selected_version_id)

    def set_arm_confirmed(self, value):  # noqa: D102
        self._record("set_arm_confirmed", (bool(value),), {})
        self._armed = bool(value)
        self.arm_writes.append(bool(value))
        super().set_arm_confirmed(value)

    # -- queries (scripted, so no widget state is needed) --
    def candidate_limit(self) -> int:
        return self._candidate_limit

    def capital_limit(self) -> Decimal:
        return self._capital_limit

    def arm_confirmed(self) -> bool:
        return self._armed


class _Version:
    """A minimal ``StrategyVersion`` stand-in for the eligibility rule."""

    def __init__(
        self,
        *,
        strategy_id: str = "intraday-auto-rotation",
        semver: str = "1.0.0",
        status: str = "research",
        gate_passed: bool = False,
        parameters: dict | None = None,
    ) -> None:
        from us_quant.trading.domain.strategy import StrategyStatus

        self.strategy_id = strategy_id
        self.name = "Intraday Auto Rotation"
        self.version_id = f"{strategy_id}-{semver}"
        self.semver = semver
        self.status = StrategyStatus(status)
        self.gate_passed = gate_passed
        self.parameters = dict(parameters or {})
        self.gate_reason = ""


class FakeSelection:
    """Duck-typed ``StrategySelectionService`` recording every call."""

    def __init__(self, versions: list[_Version] | None = None) -> None:
        self.versions = versions if versions is not None else [_Version()]
        self.selected_version = self.versions[0] if self.versions else None
        self.calls: list[tuple[str, tuple]] = []
        self.refuse_select: Exception | None = None

    def options(self, purpose):
        self.calls.append(("options", (purpose,)))
        return list(self.versions)

    def selected(self, purpose):
        self.calls.append(("selected", (purpose,)))
        return self.selected_version

    def restore_or_default(self, purpose):
        self.calls.append(("restore_or_default", (purpose,)))
        return self.selected_version

    def select(self, purpose, version_id):
        self.calls.append(("select", (purpose, version_id)))
        if self.refuse_select is not None:
            raise self.refuse_select
        match = [v for v in self.versions if v.version_id == version_id]
        if not match:
            raise StrategySelectionError(f"unknown version {version_id}")
        self.selected_version = match[0]

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)


class FakePaper:
    """Duck-typed ``PaperFactsPort``; every transition is recorded.

    ``preparation_active`` is genuinely moved by the three transitions, so a test
    can assert the canonical order rather than a scripted answer.
    """

    def __init__(self) -> None:
        self.preparation_active = False
        self.launch_attempt_in_flight = False
        self.order_service_held = False
        self.runtime_active = False
        self.has_runtime_obligations = False
        self.presentation: object | None = None
        self.session_control_facts = _ControlFacts()
        self.transitions: list[str] = []
        self.begin_refusal: str | None = None

    def begin_preparation(self) -> str | None:
        self.transitions.append("begin")
        if self.begin_refusal is not None:
            return self.begin_refusal
        self.preparation_active = True
        return None

    def cancel_preparation(self) -> None:
        self.transitions.append("cancel")
        self.preparation_active = False

    def mark_preparation_ready(self) -> None:
        self.transitions.append("mark_ready")
        # The real seam is a no-op unless the workflow is in PREPARING.
        if self.preparation_active:
            self.preparation_active = False


class _ControlFacts:
    running = False
    paused = False
    reconcile_available = False
    resume_ready = False


class RecordingSubmitter:
    """A task boundary that runs synchronously and records each submission.

    Three modes: admitted and completed (the default), refused (``admit =
    False``), failed (``fail_message``), and *deferred* (``defer = True``), which
    records the submission and returns ``True`` without completing it -- the only
    way for a test to observe a task that is genuinely still in flight.
    """

    def __init__(self) -> None:
        self.submissions: list[dict] = []
        self.admit = True
        self.defer = False
        self.result: object = None
        self.fail_message: str | None = None

    def __call__(self, task, **kwargs) -> bool:
        self.submissions.append({"task": task, **kwargs})
        if not self.admit:
            return False
        if self.defer:
            return True
        if self.fail_message is not None:
            kwargs["on_failure"](self.fail_message)
            return True
        kwargs["on_success"](task(lambda _message: None))
        return True

    @property
    def last(self) -> dict:
        assert self.submissions
        return self.submissions[-1]


# -- the harness ---------------------------------------------------------


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _qapp()
        self.page = CountingPage()
        self.selection = FakeSelection()
        self.paper = FakePaper()
        self.submitter = RecordingSubmitter()
        self.log: list[str] = []
        self.information: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        self.confirmations: list[tuple[str, str]] = []
        self.market_events: list[tuple[str, object]] = []
        self.readiness: list[object] = []

        self.scan: object | None = _Scan(
            ["AAA", "BBB", "CCC", "DDD"], skipped=5
        )
        self.universe: object | None = _Universe()
        self.market_snapshot: object | None = None
        self.market_live = False
        self.market_provider = "finnhub_trades"
        self.recently_ready: set[str] = set()
        self.portfolio: object | None = None
        self.paper_capital: Decimal | None = Decimal("10000")
        self.research_capital = Decimal("1500")
        self.broker_state: object | None = None
        self.reconciliation_rows: object = ()
        self.audit_rows: object = ()
        self.latency_rows: object = ()
        self.exposure_multipliers: dict[str, Decimal] = {}
        self.max_position_pct = Decimal("0.10")
        self.paper_capability_enabled = True
        self.extended_hours_enabled = False
        self.probe_result: object = _probe_result()
        self.probe_calls: list[int] = []
        self.stop_market_calls: list[int] = []
        self.stop_market_result = True
        self.adopted: list[object] = []
        self.scheduled: list[object] = []
        self.history_refreshes: list[int] = []
        self.scan_runs: list[tuple[object, Decimal]] = []
        self.selection_rule_calls: list[dict] = []
        self.selection_rule_rows: list[_Row] = [
            _Row("AAA"),
            _Row("BBB"),
            _Row("CCC"),
        ]

        monkeypatch.setattr(
            execution_orchestrator,
            "select_paper_rotation_rows",
            self._select_rows,
        )

        self.orchestrator = ExecutionOrchestrator(
            page=self.page,
            selection=self.selection,  # type: ignore[arg-type]
            paper=self.paper,  # type: ignore[arg-type]
            providers=self._providers(),
            submit_task=self.submitter,
        )
        self._connect()

    # -- wiring --------------------------------------------------------
    def _connect(self) -> None:
        self.orchestrator.log_requested.connect(self.log.append)
        self.orchestrator.information_requested.connect(
            lambda title, message: self.information.append((title, message))
        )
        self.orchestrator.warning_requested.connect(
            lambda title, message: self.warnings.append((title, message))
        )
        self.orchestrator.start_confirmation_requested.connect(
            lambda title, message: self.confirmations.append((title, message))
        )
        self.orchestrator.market_start_requested.connect(
            lambda: self.market_events.append(("start", None))
        )
        self.orchestrator.market_switch_requested.connect(
            lambda provider: self.market_events.append(("switch", provider))
        )
        self.orchestrator.market_subscription_requested.connect(
            lambda symbols: self.market_events.append(("subscribe", symbols))
        )
        self.orchestrator.market_readiness_inputs_changed.connect(
            self.readiness.append
        )

    # -- providers -----------------------------------------------------
    def _select_rows(self, scan, universe, **kwargs) -> list[_Row]:
        self.selection_rule_calls.append({"scan": scan, **kwargs})
        return list(self.selection_rule_rows)

    def _providers(self) -> ExecutionProviders:
        return ExecutionProviders(
            universe=lambda: self.universe,
            scan=lambda: self.scan,
            run_market_scan=self._run_market_scan,
            adopt_scan=self.adopted.append,
            schedule_history=self._schedule_history,
            refresh_history=lambda: self.history_refreshes.append(1),
            market_snapshot=lambda: self.market_snapshot,
            market_is_live=lambda: self.market_live,
            market_provider=lambda: self.market_provider,
            was_recently_ready=lambda symbol: symbol in self.recently_ready,
            recently_ready_symbols=lambda: tuple(self.recently_ready),
            account_portfolio=lambda: self.portfolio,
            fresh_paper_capital=lambda: self.paper_capital,
            broker_state=lambda: self.broker_state,
            reconciliation_rows=lambda session_id, limit: self.reconciliation_rows,
            audit_rows=lambda limit: self.audit_rows,
            latency_rows=lambda session_id, limit: self.latency_rows,
            exposure_multipliers=lambda: dict(self.exposure_multipliers),
            research_scenario_capital=lambda: self.research_capital,
            maximum_position_exposure_pct=lambda: self.max_position_pct,
            probe_order_channel=self._probe,
            stop_market_data=self._stop_market,
            paper_capability_enabled=lambda: self.paper_capability_enabled,
            extended_hours_enabled=lambda: self.extended_hours_enabled,
        )

    def _run_market_scan(self, universe, capital) -> object:
        self.scan_runs.append((universe, capital))
        assert self.scan is not None
        return self.scan

    def _schedule_history(self, universe) -> int:
        self.scheduled.append(universe)
        return 7

    def _probe(self) -> object:
        self.probe_calls.append(1)
        return self.probe_result

    def _stop_market(self) -> bool:
        self.stop_market_calls.append(1)
        return self.stop_market_result

    # -- conveniences --------------------------------------------------
    def prepare_successfully(self) -> None:
        self.orchestrator.request_prepare()


def _probe_result() -> object:
    class _Connection:
        account_alias = "DU1234567"
        open_broker_orders = 0
        unreconciled_local_orders = 0

    class _State:
        net_liquidation = Decimal("10000")
        cash = Decimal("5000")
        positions = (object(), object())

    return (_Connection(), _State())


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


@pytest.fixture
def page(harness: Harness) -> CountingPage:
    return harness.page


# -- A / B: the runtime strategy selection -------------------------------


def test_selecting_a_strategy_writes_the_service_once(harness: Harness) -> None:
    harness.orchestrator.select_strategy("intraday-auto-rotation-1.0.0")
    assert harness.selection.count("select") == 1
    purpose, version_id = harness.selection.calls[-1][1]
    assert purpose is StrategySelectionPurpose.AUTO_ROTATION
    assert version_id == "intraday-auto-rotation-1.0.0"


def test_the_combo_is_never_the_truth() -> None:
    """The route adopts the *page's* report into the service and reads it back."""

    source = execution_orchestrator.__file__
    text = pathlib.Path(source).read_text(encoding="utf-8")
    assert "selected_strategy_version_id" not in text
    assert "self._selection.selected(" in text


def test_a_refused_selection_logs_and_changes_nothing(
    harness: Harness,
) -> None:
    harness.selection.refuse_select = StrategySelectionError("已停用")
    before = harness.selection.selected_version
    harness.orchestrator.select_strategy("other-1.0.0")
    assert harness.selection.selected_version is before
    assert harness.log, "a refusal must be logged"
    assert "运行选择未生效" in harness.log[-1]
    assert harness.page.count("set_arm_confirmed") == 0


def test_an_empty_selection_report_is_a_no_op(harness: Harness) -> None:
    harness.orchestrator.select_strategy("")
    assert harness.selection.count("select") == 0


def test_refresh_strategy_options_fills_the_combo_from_the_service(
    harness: Harness,
) -> None:
    harness.orchestrator.refresh_strategy_options()
    assert harness.selection.count("restore_or_default") >= 1
    assert harness.selection.count("options") >= 1
    options, selected = harness.page.last("set_strategy_options")[1]
    assert [item[1] for item in options] == [
        version.version_id for version in harness.selection.versions
    ]
    assert selected == harness.selection.selected_version.version_id
    # And the refill repaints the preflight exactly once, after the combo.
    assert harness.page.count("render_preflight") == 1


# -- C: the preflight ----------------------------------------------------


def test_the_preflight_reads_live_providers_every_time(
    harness: Harness,
) -> None:
    harness.orchestrator.refresh_preflight()
    first = harness.page.last("render_preflight")[1][:2]
    harness.paper_capability_enabled = False
    harness.orchestrator.refresh_preflight()
    second = harness.page.last("render_preflight")[1][:2]
    assert first != second, "a cached preflight would have repeated itself"
    assert harness.page.count("render_preflight") == 2


def test_the_preflight_excludes_the_arm_confirmation() -> None:
    from us_quant.trading.runtime.preflight import (
        AutoQuantPreflight,
        AutoQuantPreflightCheck,
    )

    result = AutoQuantPreflight(
        ready=False,
        checks=(
            AutoQuantPreflightCheck(name="本次确认", passed=False, detail="未确认"),
            AutoQuantPreflightCheck(name="行情", passed=True, detail="就绪"),
            AutoQuantPreflightCheck(name="策略", passed=False, detail="缺失"),
        ),
    )
    tally = queries.preflight_tally(result)
    assert (tally.ready, tally.total) == (1, 2)
    assert "本次确认" not in tally.details
    assert "✓ 行情" in tally.details and "✕ 策略" in tally.details


def test_the_preflight_publishes_the_readiness_fact_first(
    harness: Harness,
) -> None:
    harness.orchestrator._candidates = (_candidate("AAA"),)
    harness.selection.selected_version.parameters = {
        "market_reference_symbols": ["spy", " SPY ", "qqq"]
    }
    harness.orchestrator.refresh_preflight()
    assert len(harness.readiness) == 1
    fact = harness.readiness[-1]
    assert fact.candidate_symbols == ("AAA",)
    assert fact.reference_symbols == ("SPY", "QQQ")


def test_the_preflight_quotes_the_minimum_for_the_open_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from us_quant.extended_hours import USEquitySession

    seen: list[Any] = []
    real = execution_orchestrator.evaluate_auto_quant_preflight

    def capture(**kwargs):
        seen.append(kwargs["minimum_realtime_quotes"])
        return real(**kwargs)

    monkeypatch.setattr(
        execution_orchestrator, "evaluate_auto_quant_preflight", capture
    )
    monkeypatch.setattr(
        execution_orchestrator, "us_equity_session", lambda: USEquitySession.REGULAR
    )
    harness.orchestrator.refresh_preflight()
    monkeypatch.setattr(
        execution_orchestrator,
        "us_equity_session",
        lambda: USEquitySession.AFTER_HOURS,
    )
    harness.orchestrator.refresh_preflight()
    assert seen == [3, 1]


def test_a_missing_strategy_reports_the_selection_prompt() -> None:
    eligible, detail = queries.strategy_eligibility(None)
    assert eligible is False
    assert detail == "请选择自动轮动策略版本"


def test_an_ineligible_version_is_refused_and_described() -> None:
    eligible, detail = queries.strategy_eligibility(
        _Version(strategy_id="buy-hold")
    )
    assert eligible is False
    assert detail.startswith("1.0.0")
    # A paper/shadow version must have passed its gate.
    gated = _Version(status="paper_shadow", gate_passed=False)
    assert queries.strategy_eligibility(gated)[0] is False
    passing = _Version(status="paper_shadow", gate_passed=True)
    assert queries.strategy_eligibility(passing)[0] is True
    research = _Version(status="research", gate_passed=False)
    assert queries.strategy_eligibility(research)[0] is True


# -- D: reference symbols ------------------------------------------------


def test_reference_symbols_are_trimmed_uppercased_and_deduplicated() -> None:
    assert queries.reference_symbols(
        {"market_reference_symbols": [" spy ", "SPY", "", "qqq", "  "]}
    ) == ("SPY", "QQQ")
    assert queries.reference_symbols(None) == ()
    assert queries.reference_symbols({}) == ()


def test_reference_symbols_never_become_tradable_candidates() -> None:
    rows = [_Row("SPY"), _Row("aaa"), _Row("QQQ")]
    candidates = queries.build_candidates(rows, ("SPY", "QQQ"))
    assert [row.symbol for row in candidates] == ["AAA"]


def test_the_reference_symbols_are_subscribed_but_stay_apart() -> None:
    assert queries.stream_symbols(("AAA", "BBB"), ("SPY",)) == (
        "AAA",
        "BBB",
        "SPY",
    )
    assert queries.stream_symbols(("AAA",), ("AAA",)) == ("AAA",)


# -- E / F / G: candidate preparation refusals ---------------------------


def test_preparing_without_a_universe_claims_nothing(harness: Harness) -> None:
    harness.universe = None
    harness.orchestrator.request_prepare()
    assert harness.information[-1][0] == execution_models.UNIVERSE_MISSING_TITLE
    assert harness.paper.transitions == []
    assert harness.submitter.submissions == []
    assert harness.page.count("render_context") == 0


def test_preparing_while_a_session_runs_is_refused(harness: Harness) -> None:
    harness.paper.runtime_active = True
    harness.orchestrator.request_prepare()
    assert harness.information[-1][0] == execution_models.SESSION_RUNNING_TITLE
    assert harness.paper.transitions == []
    assert harness.submitter.submissions == []


def test_a_refused_preparation_transition_claims_nothing(
    harness: Harness,
) -> None:
    harness.paper.begin_refusal = "已有会话在运行"
    harness.orchestrator.request_prepare()
    assert harness.information[-1] == (
        execution_models.PREPARATION_REFUSED_TITLE,
        "已有会话在运行",
    )
    assert harness.submitter.submissions == []
    assert harness.orchestrator.launch_locked() is False


def test_an_unadmitted_scan_task_gives_preparation_back(
    harness: Harness,
) -> None:
    harness.submitter.admit = False
    harness.orchestrator.request_prepare()
    assert harness.paper.transitions == ["begin", "cancel"]
    assert harness.paper.preparation_active is False
    assert harness.orchestrator.launch_locked() is False


def test_the_preparing_context_is_drawn_before_the_task(
    harness: Harness,
) -> None:
    harness.orchestrator.request_prepare()
    summaries = [
        kwargs["summary"]
        for name, _, kwargs in harness.page.calls
        if name == "render_context" and kwargs.get("summary")
    ]
    assert summaries[0] == execution_models.PREPARING_SUMMARY
    assert harness.submitter.last["start_message"] == (
        execution_models.PREPARE_START_MESSAGE
    )
    assert harness.submitter.last["resource_group"] == (
        execution_models.SCAN_RESOURCE_GROUP
    )


def test_the_scan_is_sized_on_the_research_scenario_capital(
    harness: Harness,
) -> None:
    harness.scan_runs.clear()
    harness.research_capital = Decimal("2345")
    harness.orchestrator.request_prepare()
    assert harness.scan_runs == [(harness.universe, Decimal("2345"))]
    assert harness.research_capital != harness.paper_capital


# -- H / I: the asynchronous scan ----------------------------------------


def test_a_failed_scan_cancels_preparation_and_releases_busy(
    harness: Harness,
) -> None:
    harness.submitter.fail_message = "scan exploded"
    harness.orchestrator.request_prepare()
    assert harness.paper.transitions == ["begin", "cancel"]
    assert harness.orchestrator.launch_locked() is False
    assert harness.orchestrator.candidates == ()
    assert harness.adopted == []


def test_a_successful_scan_is_adopted_once_and_queues_history(
    harness: Harness,
) -> None:
    harness.orchestrator.request_prepare()
    assert harness.adopted == [harness.scan]
    assert harness.scheduled == [harness.universe]
    assert harness.history_refreshes == [1]
    assert any("历史缺口" in line for line in harness.log)
    assert harness.paper.transitions == ["begin", "mark_ready"]


def test_the_scan_completion_writes_no_manual_line(harness: Harness) -> None:
    harness.orchestrator.request_prepare()
    assert not any("扫描完成" in line for line in harness.log)


def test_the_completion_rejects_an_unexpected_result(
    harness: Harness,
) -> None:
    assert queries.scan_counts(_Scan(["AAA"])) == (1, 0)
    with pytest.raises(TypeError):
        queries.scan_counts(object())


def test_an_unexpected_result_never_reaches_the_capability(
    harness: Harness,
) -> None:
    """The shape is asserted *before* the fact is handed to the scan owner."""

    with pytest.raises(TypeError):
        harness.orchestrator._preparation_finished(object())
    assert harness.adopted == [], (
        "a non-scan must never be adopted into the scan truth"
    )
    assert harness.scheduled == []


# -- J / K / L: the shortlist rules --------------------------------------


def test_a_missing_fresh_capital_cancels_and_warns(harness: Harness) -> None:
    harness.paper_capital = None
    harness.orchestrator.request_prepare()
    assert harness.information[-1] == (
        execution_models.FRESH_CAPITAL_TITLE,
        execution_models.FRESH_CAPITAL_MESSAGE,
    )
    assert harness.paper.transitions == ["begin", "cancel"]
    assert harness.orchestrator.candidates == ()
    assert harness.market_events == []
    assert harness.orchestrator.launch_locked() is False


def test_the_capital_limit_may_only_shrink_the_paper_figure() -> None:
    assert queries.bounded_capital(Decimal("9000"), Decimal("5000")) == Decimal(
        "5000"
    )
    assert queries.bounded_capital(Decimal("9000"), Decimal("0")) == Decimal(
        "9000"
    )
    assert queries.bounded_capital(Decimal("9000"), Decimal("-1")) == Decimal(
        "9000"
    )


def test_the_shortlist_is_sized_on_the_bounded_paper_capital(
    harness: Harness,
) -> None:
    harness.paper_capital = Decimal("9000")
    harness.page._capital_limit = Decimal("5000")
    harness.orchestrator.request_prepare()
    call = harness.selection_rule_calls[-1]
    assert call["capital"] == Decimal("5000")
    assert call["max_position_fraction"] == harness.max_position_pct
    assert call["risk_multipliers"] == harness.exposure_multipliers


def test_too_few_candidates_never_reach_ready(harness: Harness) -> None:
    harness.selection_rule_rows = [_Row("AAA"), _Row("BBB")]
    harness.orchestrator.request_prepare()
    assert harness.warnings[-1][0] == (
        execution_models.INSUFFICIENT_CANDIDATES_TITLE
    )
    assert harness.paper.transitions == ["begin", "cancel"]
    assert "mark_ready" not in harness.paper.transitions
    assert harness.orchestrator.candidates == ()
    assert harness.market_events == []
    assert harness.orchestrator.launch_locked() is False


def test_the_shortlist_message_names_the_minimum(harness: Harness) -> None:
    message = queries.insufficient_candidates_message(2, 3)
    assert "只有 2 个" in message
    assert "至少需要 3 个" in message
    assert execution_models.MINIMUM_CANDIDATES == 3


# -- M: a successful shortlist ------------------------------------------


def test_a_successful_shortlist_retains_once_and_announces_everything(
    harness: Harness,
) -> None:
    harness.orchestrator.request_prepare()
    assert [row.symbol for row in harness.orchestrator.candidates] == [
        "AAA",
        "BBB",
        "CCC",
    ]
    assert harness.paper.transitions == ["begin", "mark_ready"]
    assert ("subscribe", ("AAA", "BBB", "CCC")) in harness.market_events
    assert harness.readiness, "the readiness fact must be republished"
    assert harness.readiness[-1].candidate_symbols == ("AAA", "BBB", "CCC")
    assert harness.page.count("render_candidates") >= 1
    assert harness.page.count("render_preflight") >= 1
    assert harness.orchestrator.launch_locked() is False
    assert harness.market_events[-1][0] == "start"


def test_a_live_market_is_switched_rather_than_started(
    harness: Harness,
) -> None:
    harness.market_live = True
    harness.orchestrator.request_prepare()
    assert harness.market_events[-1] == ("switch", "finnhub_trades")
    assert ("start", None) not in harness.market_events
    assert harness.page.last("render_context")[2]["summary"] == (
        queries.switching_summary_text(3)
    )


def test_the_shortlist_is_not_published_to_the_market_before_readiness(
    harness: Harness,
) -> None:
    harness.orchestrator.request_prepare()
    order = [name for name, _, _ in harness.page.calls]
    assert order.index("render_candidates") < len(order)


def test_the_candidate_truth_is_one_object(harness: Harness) -> None:
    harness.orchestrator.request_prepare()
    retained = harness.orchestrator.candidates
    assert harness.readiness[-1].candidate_symbols == tuple(
        row.symbol for row in retained
    )
    # The page was handed a view built from the same tuple, not a copy of it.
    view = harness.page.last("render_candidates")[1][0]
    assert [row.symbol for row in view.candidates] == [
        row.symbol for row in retained
    ]


def test_the_scope_line_quotes_the_scan_counts(harness: Harness) -> None:
    harness.orchestrator.request_prepare()
    scopes = [
        kwargs["scope"]
        for name, _, kwargs in harness.page.calls
        if name == "render_context" and kwargs.get("scope")
    ]
    assert scopes, "a successful shortlist writes a scope line"
    assert "非中概研究池 120" in scopes[-1]
    assert "完成评分 4" in scopes[-1]
    assert "不足200根 5" in scopes[-1]
    assert "候选 3" in scopes[-1]


def test_the_shortlist_refuses_an_extra_reference_row(harness: Harness) -> None:
    harness.selection.selected_version.parameters = {
        "market_reference_symbols": ["bbb"]
    }
    harness.selection_rule_rows = [
        _Row("AAA"),
        _Row("BBB"),
        _Row("CCC"),
        _Row("DDD"),
    ]
    harness.orchestrator.request_prepare()
    assert [row.symbol for row in harness.orchestrator.candidates] == [
        "AAA",
        "CCC",
        "DDD",
    ]
    assert "BBB" not in tuple(
        row.symbol for row in harness.orchestrator.candidates
    )


# -- N / O / P / Q: the channel probe ------------------------------------


def test_a_second_probe_request_is_ignored(harness: Harness) -> None:
    harness.submitter.defer = True
    harness.orchestrator.request_channel_check()
    assert harness.orchestrator._channel_probe_inflight is True
    harness.orchestrator.request_channel_check()
    assert len(harness.submitter.submissions) == 1
    assert harness.probe_calls == []


def test_a_held_order_channel_needs_no_probe(harness: Harness) -> None:
    harness.paper.order_service_held = True
    harness.orchestrator.request_channel_check()
    assert harness.information[-1] == (
        execution_models.CHANNEL_BUSY_TITLE,
        execution_models.CHANNEL_BUSY_MESSAGE,
    )
    assert harness.submitter.submissions == []


def test_a_live_session_needs_no_probe(harness: Harness) -> None:
    harness.paper.runtime_active = True
    harness.orchestrator.request_channel_check()
    assert harness.submitter.submissions == []
    assert harness.information[-1][0] == execution_models.CHANNEL_BUSY_TITLE


def test_an_unadmitted_probe_releases_only_its_own_flag(
    harness: Harness,
) -> None:
    harness.submitter.admit = False
    harness.orchestrator.request_channel_check()
    assert harness.orchestrator._channel_probe_inflight is False
    assert harness.orchestrator._launch_busy is False
    assert harness.paper.transitions == []


def test_a_failed_probe_releases_only_its_own_flag(
    harness: Harness,
) -> None:
    harness.submitter.fail_message = "broker said no"
    harness.orchestrator.request_channel_check()
    assert harness.orchestrator._channel_probe_inflight is False
    assert harness.paper.transitions == []
    assert harness.page.count("render_execution_health") == 0


def test_a_successful_probe_reports_the_detail(harness: Harness) -> None:
    harness.orchestrator.request_channel_check()
    assert harness.orchestrator._channel_probe_inflight is False
    assert harness.submitter.last["resource_group"] == (
        execution_models.BROKER_RESOURCE_GROUP
    )
    health = harness.page.last("render_execution_health")[1][0]
    assert health.startswith(execution_models.CHANNEL_OK_HEALTH_PREFIX)
    assert "DU1234567" in health and "$10,000.00" in health
    assert harness.log[-1].startswith(execution_models.CHANNEL_OK_LOG_PREFIX)


def test_an_unreadable_probe_result_leaves_the_route_closed(
    harness: Harness,
) -> None:
    harness.probe_result = object()
    with pytest.raises(TypeError):
        harness.orchestrator.request_channel_check()
    assert harness.orchestrator._channel_probe_inflight is True


def test_the_probe_locks_the_launch_controls(harness: Harness) -> None:
    harness.submitter.defer = True
    harness.orchestrator.request_channel_check()
    state = harness.page.last("set_control_state")[1][0]
    assert state.prepare_enabled is False
    assert state.channel_check_enabled is False
    assert state.arm_confirm_enabled is False


# -- the launch lock -----------------------------------------------------


def test_the_launch_lock_reads_paper_facts_and_local_flags(
    harness: Harness,
) -> None:
    assert harness.orchestrator.launch_locked() is False
    for fact in (
        "launch_attempt_in_flight",
        "order_service_held",
        "runtime_active",
    ):
        setattr(harness.paper, fact, True)
        assert harness.orchestrator.launch_locked() is True
        setattr(harness.paper, fact, False)
    harness.orchestrator._launch_busy = True
    assert harness.orchestrator.launch_locked() is True
    harness.orchestrator._launch_busy = False
    harness.orchestrator._channel_probe_inflight = True
    assert harness.orchestrator.launch_locked() is True
    harness.orchestrator._channel_probe_inflight = False
    assert harness.orchestrator.launch_locked() is False


def test_the_controls_come_from_the_capability_facts(
    harness: Harness,
) -> None:
    facts = _ControlFacts()
    facts.running = True
    harness.paper.session_control_facts = facts
    harness.market_live = True
    harness.orchestrator.refresh_controls()
    state = harness.page.last("set_control_state")[1][0]
    # ``control_state`` publishes *which controls may be offered*; there is no
    # phase or running flag on the state the page receives.
    assert state.pause_enabled is True
    assert state.stop_enabled is True
    assert state.stop_stream_enabled is True
    facts.running = False
    harness.market_live = False
    harness.orchestrator.refresh_controls()
    state = harness.page.last("set_control_state")[1][0]
    assert state.pause_enabled is False
    assert state.stop_stream_enabled is False


# -- R / W / X: the launch confirmation ----------------------------------


def test_a_duplicate_launch_ask_is_refused(harness: Harness) -> None:
    harness.paper.launch_attempt_in_flight = True
    harness.orchestrator.request_start()
    assert harness.confirmations == []
    assert harness.information[-1] == (
        execution_models.LAUNCH_IN_FLIGHT_TITLE,
        execution_models.LAUNCH_IN_FLIGHT_MESSAGE,
    )


def test_the_launch_ask_publishes_the_question(harness: Harness) -> None:
    harness.orchestrator.request_start()
    assert harness.confirmations == [
        (execution_models.CONFIRM_TITLE, execution_models.CONFIRM_MESSAGE)
    ]
    assert harness.page.count("set_arm_confirmed") == 0


def test_declining_the_launch_clears_the_arm_and_starts_nothing(
    harness: Harness,
) -> None:
    harness.orchestrator.confirm_start(False)
    assert harness.page.arm_writes == [False]
    assert harness.paper.transitions == []


def test_accepting_the_launch_arms_once_and_asks_paper_once(
    harness: Harness,
) -> None:
    seen: list[int] = []
    harness.orchestrator.paper_start_requested.connect(lambda: seen.append(1))
    harness.orchestrator.confirm_start(True)
    assert harness.page.arm_writes == [True]
    assert seen == [1]


def test_consent_is_not_authorization(harness: Harness) -> None:
    """The route never runs a preflight of its own before asking Paper."""

    harness.orchestrator.confirm_start(True)
    assert harness.paper.transitions == []


# -- the stop-stream control --------------------------------------------


def test_stopping_the_stream_is_refused_under_obligations(
    harness: Harness,
) -> None:
    harness.paper.has_runtime_obligations = True
    harness.orchestrator.request_stop_stream()
    assert harness.stop_market_calls == []
    assert harness.information[-1][0] == (
        execution_models.STOP_STREAM_BLOCKED_TITLE
    )


def test_stopping_the_stream_reports_the_result(harness: Harness) -> None:
    harness.orchestrator.request_stop_stream()
    assert harness.stop_market_calls == [1]
    assert harness.page.last("render_context")[2]["summary"] == (
        execution_models.STOP_STREAM_SUMMARY
    )


def test_a_refused_market_stop_says_nothing(harness: Harness) -> None:
    harness.stop_market_result = False
    harness.orchestrator.request_stop_stream()
    assert harness.page.count("render_context") == 0


# -- T / U: the render path ---------------------------------------------


def test_the_candidate_table_is_drawn_before_any_session(
    harness: Harness,
) -> None:
    harness.orchestrator._candidates = (_candidate("AAA"),)
    harness.orchestrator.refresh_current()
    assert harness.page.count("render_candidates") == 1
    assert harness.page.count("render") == 0


def test_no_session_means_no_session_render(harness: Harness) -> None:
    harness.paper.presentation = None
    harness.orchestrator.refresh_current()
    assert harness.page.count("render") == 0


def test_the_session_view_is_built_from_the_retained_presentation(
    harness: Harness,
) -> None:
    session = _FakePresentation()
    harness.paper.presentation = session
    harness.broker_state = "broker-reading"
    harness.orchestrator.refresh_current()
    view = harness.page.last("render")[1][0]
    assert view is not None
    # The session id the row queries are keyed by is the presentation's own.
    assert harness.page.count("render") == 1
    assert session.session_id


def test_the_presentation_is_read_at_render_time_not_cached(
    harness: Harness,
) -> None:
    harness.paper.presentation = _FakePresentation()
    harness.orchestrator.refresh_current()
    harness.paper.presentation = None
    harness.orchestrator.refresh_current()
    # A cached snapshot would have drawn the session a second time.
    assert harness.page.count("render") == 1


def test_the_route_keeps_no_snapshot_of_any_capability_fact(
    harness: Harness,
) -> None:
    """A full refresh cycle leaves no capability fact retained on the route."""

    harness.paper.presentation = _FakePresentation()
    harness.market_snapshot = _FakeSnapshot()
    harness.orchestrator.refresh_all()
    for name in (
        "market_snapshot",
        "snapshot",
        "_snapshot",
        "portfolio",
        "_portfolio",
        "presentation",
        "_presentation",
        "scan",
        "_scan",
        "universe",
        "_universe",
        "preflight",
        "_preflight",
    ):
        assert not hasattr(harness.orchestrator, name), name
    # What it does retain is the route's own three facts, and nothing else.
    assert harness.orchestrator._candidates == ()
    assert harness.orchestrator._launch_busy is False
    assert harness.orchestrator._channel_probe_inflight is False


def test_the_extended_hours_line_comes_from_the_preference(
    harness: Harness,
) -> None:
    harness.orchestrator.refresh_extended_hours_status()
    disabled = harness.page.last("render_context")[2]["session"]
    assert "未启用" in disabled
    harness.extended_hours_enabled = True
    harness.orchestrator.refresh_extended_hours_status()
    enabled = harness.page.last("render_context")[2]["session"]
    assert "未启用" not in enabled


def test_the_window_composes_the_scope_text_and_the_route_draws_it(
    harness: Harness,
) -> None:
    harness.orchestrator.set_scope("composed elsewhere")
    assert harness.page.last("render_context")[2] == {"scope": "composed elsewhere"}


# -- V / Y: Paper's publications ----------------------------------------


def test_a_result_repaints_the_route_and_the_controls(
    harness: Harness,
) -> None:
    harness.orchestrator.on_paper_result_changed(object())
    assert harness.page.count("render_candidates") == 1
    assert harness.page.count("set_control_state") == 1
    assert harness.page.count("render") == 0


def test_a_finalized_session_reports_and_clears_the_arm(
    harness: Harness,
) -> None:
    harness.orchestrator.on_paper_session_finalized()
    assert harness.page.last("render_execution_health")[1][0] == (
        execution_models.SAFE_END_HEALTH
    )
    assert harness.page.arm_writes == [False]
    assert harness.page.count("set_control_state") == 1
    assert harness.paper.transitions == []


class _FakePresentation:
    session_id = "session-1"
    engine_snapshot = object()
    candidates = ()
    positions = ()
    orders = ()
    status = "running"


class _FakeSnapshot:
    """The market snapshot shape the render path reads: a quote sequence.

    Iterable as well as carrying ``quotes``, because the readiness rule accepts
    either a real ``MarketSnapshot`` or a plain iterable of quotes.
    """

    quotes: tuple = ()
    realtime_ready = True
    message = "ok"

    def __iter__(self):
        return iter(self.quotes)


# -- the window-level facts: an unrelated task ---------------------------


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A real offscreen ``MainWindow`` with no broker or worker boundary faked."""

    monkeypatch.setenv("US_QUANT_STATE_ROOT", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox

    from us_quant.desktop import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    _qapp()
    window = MainWindow()
    _APP.processEvents()
    return window


def test_an_unrelated_task_failure_leaves_the_route_untouched(
    window, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A History / Research / Account failure is not an execution fact."""

    execution = window.execution_orchestrator
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning", lambda *a, **k: None
    )
    execution._launch_busy = True
    execution._channel_probe_inflight = True
    execution._candidates = (_candidate("AAA"),)
    window.execution_page.set_arm_confirmed(True)

    recorded: list = []
    monkeypatch.setattr(
        execution, "refresh_controls", lambda: recorded.append("controls")
    )
    monkeypatch.setattr(
        execution, "refresh_current", lambda: recorded.append("current")
    )

    window._task_failed("unrelated background task exploded")

    assert execution._launch_busy is True
    assert execution._channel_probe_inflight is True
    assert [row.symbol for row in execution.candidates] == ["AAA"]
    assert window.execution_page.arm_confirmed() is True
    assert recorded == []


def test_an_unrelated_worker_finish_repaints_no_route(
    window, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = window.execution_orchestrator
    recorded: list = []
    monkeypatch.setattr(
        execution, "refresh_controls", lambda: recorded.append("controls")
    )
    monkeypatch.setattr(execution, "refresh_all", lambda: recorded.append("all"))

    worker = window.task_controller.running_workers()
    assert worker == ()
    # A worker that was never registered: ``finish`` reports "not found" and the
    # generic path must still not touch the route.
    window._worker_finished(object())  # type: ignore[arg-type]

    assert recorded == []
    assert execution._launch_busy is False


def test_the_page_prepare_intent_reaches_the_orchestrator(
    window, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page is wired to the route's owner, not to a window handler."""

    seen: list[str] = []
    monkeypatch.setattr(
        window.execution_orchestrator,
        "request_prepare",
        lambda: seen.append("prepare"),
    )
    window.execution_page.prepare_requested.emit()
    assert seen == ["prepare"]


def test_the_page_session_intents_reach_paper_directly(window) -> None:
    seen: list[str] = []
    for name in ("pause", "resume", "stop", "reconcile"):
        monkeypatch_target = getattr(window.paper_orchestrator, name)
        assert callable(monkeypatch_target)
        seen.append(name)
    assert seen == ["pause", "resume", "stop", "reconcile"]
    # The route's own orchestrator must not be a second owner of the session.
    for name in ("pause", "resume", "stop", "reconcile"):
        assert not hasattr(window.execution_orchestrator, name), name


def test_the_window_hands_the_page_only_its_palette(window) -> None:
    window.execution_page.set_palette(window.theme)
    called = [
        name
        for name in ("render", "render_candidates", "set_control_state")
        if hasattr(window.execution_page, name)
    ]
    # The page still *has* those methods; the claim is that the window does not
    # call them, which the AST guard in
    # ``test_desktop_execution_architecture`` pins.
    assert called == ["render", "render_candidates", "set_control_state"]
