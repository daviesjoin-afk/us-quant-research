"""Behaviour of the v2O-C5B targeted session capability, without a window.

The capability is constructed with plain callables and a stubbed service, so every
rule it owns can be asserted here -- the three refusals and their severities, the
draft signal's narrowness, the apply ordering, the Shadow and live-feed guards, the
strategy selection using the emitted id, the minute/preflight independence, the
last-good preflight and the Shadow snapshot being read rather than stored --
without starting a Qt event loop.

What these tests cannot prove (that the page's buttons reach the capability, that
the window holds no second truth) belongs to the wiring and architecture files.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from us_quant.desktop_targeted_session_service import (
    DesktopTargetedSessionService,
)
from us_quant.desktop_v2.orchestration.research.targeted.session import (
    REFUSAL_INFORMATION,
    REFUSAL_WARNING,
    TargetedSessionOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.targeted.session.models import (
    DEFAULT_MINUTE_STATUS,
    DEFAULT_TARGET_STATUS,
)
from us_quant.minute_data import MinuteDataSummary
from us_quant.shadow.models import ShadowSnapshot
from us_quant.trading.domain.account import (
    BrokerAccountSnapshot,
    Environment,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot

_NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)


class _Page:
    """Records the target it was told to display, in order."""

    def __init__(self) -> None:
        self.shown: list[str] = []
        self.rendered: list[object] = []
        self.options: list[tuple[tuple, str | None]] = []

    def set_target_symbol(self, symbol: str) -> None:
        self.shown.append(symbol)

    def render_session(self, view) -> None:
        self.rendered.append(view)

    def set_strategy_options(self, options, selected) -> None:
        self.options.append((tuple(options), selected))


class _Store:
    """A minute store returning a summary per symbol, with its read count."""

    def __init__(self, summaries: dict | None = None) -> None:
        self.summaries = summaries or {}
        self.reads: list[str] = []
        self.raises: Exception | None = None

    def summary(self, symbol: str) -> MinuteDataSummary:
        self.reads.append(symbol)
        if self.raises is not None:
            raise self.raises
        return self.summaries.get(
            symbol,
            MinuteDataSummary(
                symbol=symbol,
                total_rows=0,
                usable_rows=0,
                first_minute=None,
                last_minute=None,
                providers=(),
                evidence_origins=(),
            ),
        )


class _Evaluator:
    """Captures the preflight call's arguments and returns a scripted result."""

    def __init__(self, result: object = None, raises: Exception | None = None):
        self.calls: list[dict] = []
        self.result = result if result is not None else _preflight()
        self.raises = raises


def _service(store: _Store, result: object = None, raises=None):
    """A real service over a stub store, with the preflight call intercepted.

    The service is *not* stubbed away: its ``minute_summary`` delegation and its
    hard-disabled ``broker_orders_available=False`` are exactly what one of these
    tests asserts.  Only the evaluator -- a domain function -- is intercepted, so
    "which facts were passed" can be read without building a real preflight.
    """

    service = DesktopTargetedSessionService(minute_quote_store=store)
    evaluator = _Evaluator(result=result, raises=raises)

    def evaluate(symbol, **kwargs):
        evaluator.calls.append({"symbol": symbol, **kwargs})
        if evaluator.raises is not None:
            raise evaluator.raises
        # The real function always returns a result; the stub returns a sentinel
        # object, which is what makes "the snapshot holds exactly this" assertable.
        return evaluator.result

    service.evaluate_preflight = evaluate  # type: ignore[method-assign]
    return service, evaluator


def _build(
    *,
    store: _Store | None = None,
    result: object = None,
    raises=None,
    universe: UniverseSnapshot | None = None,
    market: MarketSnapshot | None = None,
    live: bool = False,
    account: BrokerAccountSnapshot | None = None,
    strategy: StrategyVersion | None = None,
    displayed: StrategyVersion | None = None,
    options: tuple = (),
    multipliers: dict | None = None,
    shadow: ShadowSnapshot | None = None,
):
    store = store or _Store()
    service, evaluator = _service(store, result=result, raises=raises)
    page = _Page()
    seen: dict[str, list] = {
        "subscription": [],
        "start": [],
        "selected": [],
        "log": [],
        "refused": [],
    }
    orchestrator = TargetedSessionOrchestrator(
        page=page,
        service=service,
        universe_provider=lambda: universe,
        market_snapshot_provider=lambda: market,
        market_is_live=lambda: live,
        account_provider=lambda: account,
        selected_strategy_provider=lambda: strategy,
        displayed_strategy_provider=lambda: displayed,
        strategy_options_provider=lambda: options,
        strategy_selector=lambda version_id: seen["selected"].append(version_id),
        exposure_multipliers_provider=lambda: dict(multipliers or {}),
        shadow_snapshot_provider=lambda: shadow,
        market_set_subscription=lambda symbols, **kwargs: seen[
            "subscription"
        ].append((symbols, kwargs.get("note"))),
        market_start=lambda: seen["start"].append(True),
    )
    orchestrator.refused.connect(
        lambda level, title, message: seen["refused"].append(
            (level, title, message)
        )
    )
    orchestrator.log_requested.connect(seen["log"].append)
    return orchestrator, page, store, evaluator, seen


def _universe(*records: UniverseRecord) -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=records,
    )


def _record(symbol: str = "AAPL", eligible: bool = True) -> UniverseRecord:
    return UniverseRecord(
        symbol=symbol,
        name="Apple Inc.",
        exchange="NASDAQ",
        security_type="STK",
        sector="信息技术",
        leader_tier=1,
        country_status="美国注册",
        country_evidence_level="verified_non_china",
        eligible_for_research=eligible,
        eligible_for_trading=eligible,
        exclusion_reason="",
    )


def _quote(symbol: str = "AAPL", ready: bool = True) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        bid=Decimal("99"),
        ask=Decimal("100"),
        last=Decimal("100"),
        close=None,
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=_NOW,
        age_seconds=1,
        stale=not ready,
        stale_reason=None if ready else "stale",
        generation=1,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit test",
    )


def _stream(*quotes: MarketQuote) -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=quotes,
        error_code=None,
        message="ok",
        observed_at=_NOW,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit test",
    )


def _account() -> BrokerAccountSnapshot:
    """Domain account truth: an ``Environment`` and an aware ``datetime``."""

    return BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias="DU***67",
        net_liquidation=Decimal("10000"),
        cash=Decimal("10000"),
        available_funds=Decimal("10000"),
        buying_power=Decimal("10000"),
        gross_position_value=Decimal("0"),
        excess_liquidity=Decimal("10000"),
        maintenance_margin=Decimal("0"),
        cushion=Decimal("1"),
        daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        observed_at=datetime.now(timezone.utc),
        pnl_source="IBKR reqPnL",
    )


def _strategy(version_id: str = "version-1") -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id="intraday-targeted-t",
            name="指定标的日内 T",
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id="intraday-targeted-t",
            version_id=version_id,
            parameter_hash="hash-1",
        ),
        semver=f"1.0.0-{version_id}",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters={},
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.10"),
        gate_passed=False,
        gate_reason="research",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _preflight(symbol: str = "AAPL", *, shadow_ready: bool = True):
    """A real preflight result: the presenter reads every gate of it.

    A sentinel object would let these tests pass while the panel rendered
    nothing -- the presenter is the one projection this round must not change, so
    the tests hand it the shape it actually consumes.
    """

    from us_quant.targeted_preflight import (
        TargetPreflightGate,
        TargetPreflightResult,
    )

    gates = (
        TargetPreflightGate(
            code="symbol_format",
            name="代码格式",
            passed=True,
            observed=symbol,
            required="有效美股/ETF代码",
            category="标的",
            blocking=True,
        ),
        TargetPreflightGate(
            code="minute_evidence",
            name="本地分钟证据",
            passed=shadow_ready,
            observed="0/0 可用",
            required="至少 10 个可用分钟",
            category="证据",
            blocking=False,
        ),
    )
    return TargetPreflightResult(
        generated_at="2026-09-22T14:00:00+00:00",
        symbol=symbol,
        company_name="Apple",
        security_type="STK",
        sector="科技",
        leader_tier=None,
        country_status="US",
        strategy_version_id="targeted-v1",
        strategy_semver="1.0.0",
        strategy_status="research",
        quote_provider="IBKR",
        bid=Decimal("99"),
        ask=Decimal("100"),
        quote_age_seconds=Decimal("1"),
        account_net_liquidation=Decimal("10000"),
        account_age_seconds=Decimal("1"),
        position_budget=Decimal("1000"),
        estimated_whole_shares=10,
        estimated_entry_notional=Decimal("1000"),
        minute_usable_rows=0,
        minute_total_rows=0,
        gates=gates,
        hard_gates_passed=1,
        hard_gate_count=1,
        shadow_ready=shadow_ready,
        broker_orders_available=False,
        decision="EXPLORATORY_SHADOW_READY" if shadow_ready else "BLOCKED",
    )


def _shadow(*, active: bool, target: str = "AAPL") -> ShadowSnapshot:
    return ShadowSnapshot(
        session_id="session-1",
        strategy_version_id="targeted-v1",
        parameter_hash="hash-1",
        target_symbol=target,
        active=active,
        initial_cash=Decimal("10000"),
        capital_source="IBKR Paper DU***67 NetLiquidation",
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        realized_pnl=Decimal("0"),
        daily_realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        positions=(),
        fills=(),
        trades_today=0,
        trading_day="2026-09-22",
        status="运行中" if active else "已停止",
        observed_at="2026-09-22T14:00:00+00:00",
    )


# -- initial state ---------------------------------------------------------


def test_the_initial_snapshot_has_the_documented_defaults() -> None:
    orchestrator, page, _store, _evaluator, _seen = _build()

    snapshot = orchestrator.snapshot
    assert snapshot.target_draft == ""
    assert snapshot.target_status == DEFAULT_TARGET_STATUS
    assert snapshot.minute_status == DEFAULT_MINUTE_STATUS
    assert snapshot.preflight is None
    # Construction runs nothing: no store read, no evaluation, no paint.
    assert _store.reads == []
    assert page.rendered == []


# -- the draft signal ------------------------------------------------------


def test_adopting_a_draft_normalizes_and_does_nothing_else() -> None:
    """One keystroke is one assignment, not a fan-out.

    The signal is wired to this method and nothing else, so if adopting a draft
    ran a preflight, read the minute store, touched the Market subscription,
    started a feed, logged or repainted, the operator's typing would trigger the
    whole session once per character.
    """

    orchestrator, page, store, evaluator, seen = _build()
    orchestrator.adopt_target_draft(" aapl ")

    assert orchestrator.snapshot.target_draft == "AAPL"
    assert store.reads == [], "typing must not read the minute store"
    assert evaluator.calls == [], "typing must not run a preflight"
    assert seen["subscription"] == [], "typing must not touch the subscription"
    assert seen["start"] == [], "typing must not start a feed"
    assert seen["log"] == [], "typing must not log"
    assert page.rendered == [], "typing must not repaint"
    assert page.shown == [], "typing must not rewrite the editor"


def test_adopting_an_empty_draft_clears_it() -> None:
    orchestrator, _page, _store, _evaluator, _seen = _build()
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.adopt_target_draft("")
    assert orchestrator.snapshot.target_draft == ""


# -- apply -----------------------------------------------------------------


def test_an_invalid_symbol_is_refused_as_a_warning_and_changes_nothing() -> None:
    orchestrator, page, store, _evaluator, seen = _build(
        universe=_universe(_record())
    )
    orchestrator.request_target_apply("AA PL")

    assert seen["refused"][0][0] == REFUSAL_WARNING
    assert orchestrator.snapshot.target_draft == ""
    assert orchestrator.snapshot.target_status == DEFAULT_TARGET_STATUS
    assert page.shown == []
    assert seen["subscription"] == []
    assert seen["log"] == []


def test_apply_commits_the_draft_status_and_preflight() -> None:
    result = _preflight()
    orchestrator, page, _store, evaluator, seen = _build(
        result=result, universe=_universe(_record("AAPL"))
    )
    orchestrator.request_target_apply(" aapl ")

    assert orchestrator.snapshot.target_draft == "AAPL"
    assert orchestrator.snapshot.target_status == (
        "AAPL · 订阅、回放、评估和影子做 T 共用"
    )
    assert page.shown == ["AAPL"], "the page reflects the normalized symbol"
    assert evaluator.calls[0]["symbol"] == "AAPL"
    assert orchestrator.snapshot.preflight is result
    assert seen["log"] == [
        "当前指定做 T 标的已切换为 AAPL；没有默认代码或单一股票专用逻辑。"
    ]


def test_apply_calls_out_a_symbol_the_directory_excludes_from_non_china() -> None:
    orchestrator, _page, _store, _evaluator, _seen = _build(
        universe=_universe(_record("AAPL", eligible=False))
    )
    orchestrator.request_target_apply("AAPL")
    assert orchestrator.snapshot.target_status == (
        "AAPL · 已设置，但尚未通过当前非中概研究资格门"
    )


def test_apply_without_a_universe_is_not_reported_as_ineligible() -> None:
    """No directory is not the same as an excluded symbol.

    The preflight's identity gate is what reports a missing directory; escalating
    the status line instead would tell the operator their symbol failed a gate
    that was never evaluated.
    """

    orchestrator, _page, _store, _evaluator, _seen = _build()
    orchestrator.request_target_apply("AAPL")
    assert orchestrator.snapshot.target_status == (
        "AAPL · 订阅、回放、评估和影子做 T 共用"
    )


def test_apply_preconfigures_the_subscription_only_when_not_live() -> None:
    orchestrator, _page, _store, _evaluator, seen = _build()
    orchestrator.request_target_apply("AAPL")
    assert seen["subscription"] == [(("AAPL",), None)]
    assert seen["start"] == [], "applying a target is not asking for a feed"

    live, _page2, _store2, _evaluator2, seen2 = _build(live=True)
    live.request_target_apply("AAPL")
    assert seen2["subscription"] == [], "a live feed keeps its subscription"


def test_apply_is_refused_while_a_shadow_session_runs() -> None:
    """The safety regression: the engine's symbol must not diverge from the page.

    The page and the canonical draft both go back to the symbol the running
    engine is actually trading, so there is no state where the UI shows one
    symbol and the simulation trades another.
    """

    orchestrator, page, _store, evaluator, seen = _build(
        shadow=_shadow(active=True, target="AAPL"),
        universe=_universe(_record("AAPL"), _record("MSFT")),
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.request_target_apply("MSFT")

    assert seen["refused"] == [
        (REFUSAL_INFORMATION, "影子会话运行中", "请先停止当前影子会话，再切换指定标的。")
    ]
    assert orchestrator.snapshot.target_draft == "AAPL"
    assert page.shown == ["AAPL"]
    assert seen["subscription"] == [], "the subscription must not move"
    assert evaluator.calls == [], "no preflight for a refused switch"
    assert seen["log"] == []


def test_apply_is_allowed_when_the_shadow_session_is_stopped() -> None:
    orchestrator, page, _store, _evaluator, _seen = _build(
        shadow=_shadow(active=False),
        universe=_universe(_record("MSFT")),
    )
    orchestrator.request_target_apply("MSFT")
    assert orchestrator.snapshot.target_draft == "MSFT"
    assert page.shown == ["MSFT"]


# -- subscribe -------------------------------------------------------------


def test_subscribe_sets_the_subscription_refreshes_then_starts() -> None:
    """The ordering, asserted as an order rather than as three separate facts.

    The preflight must be refreshed **before** the start request: the operator
    should see the gate verdict for the symbol they are about to stream, not for
    whatever the panel held a moment ago.
    """

    events: list[str] = []
    store = _Store()
    service, _evaluator = _service(store, result=_preflight())
    page = _Page()
    orchestrator = TargetedSessionOrchestrator(
        page=page,
        service=service,
        universe_provider=lambda: None,
        market_snapshot_provider=lambda: None,
        market_is_live=lambda: False,
        account_provider=lambda: None,
        selected_strategy_provider=lambda: None,
        displayed_strategy_provider=lambda: None,
        strategy_options_provider=lambda: (),
        strategy_selector=lambda _version_id: None,
        exposure_multipliers_provider=lambda: {},
        shadow_snapshot_provider=lambda: None,
        market_set_subscription=lambda symbols, **kwargs: events.append(
            f"subscription:{symbols[0]}"
        ),
        market_start=lambda: events.append("start"),
    )
    orchestrator.log_requested.connect(lambda _m: events.append("log"))
    page_refresh = orchestrator.render_current

    def render() -> None:
        events.append("render")
        page_refresh()

    orchestrator.render_current = render  # type: ignore[method-assign]

    orchestrator.request_target_subscribe(" nvda ")

    assert events == [
        "subscription:NVDA",
        "log",
        "render",  # minute status refresh
        "render",  # preflight refresh
        "start",
    ], events
    assert page.shown == ["NVDA"]


def test_subscribe_carries_the_note_and_the_log_line() -> None:
    orchestrator, page, _store, _evaluator, seen = _build()
    orchestrator.request_target_subscribe("NVDA")

    assert seen["subscription"] == [(("NVDA",), "针对性日内 T：NVDA")]
    assert seen["log"] == [
        "本次针对性日内 T 标的设为 NVDA；启动行情后仍需通过实时性与中概排除门。"
    ]
    assert page.shown == ["NVDA"]


def test_subscribe_does_not_recompute_the_target_status() -> None:
    """Subscribing has never changed what the Universe says about a symbol."""

    orchestrator, _page, _store, _evaluator, _seen = _build(
        universe=_universe(_record("AAPL", eligible=False))
    )
    orchestrator.request_target_subscribe("AAPL")
    assert orchestrator.snapshot.target_status == DEFAULT_TARGET_STATUS


def test_subscribe_is_refused_while_the_market_is_live() -> None:
    orchestrator, page, _store, _evaluator, seen = _build(live=True)
    orchestrator.request_target_subscribe("AAPL")

    assert seen["refused"] == [
        (REFUSAL_INFORMATION, "请先停止当前行情流", "停止当前行情流后，再切换本次针对性日内 T 标的。")
    ]
    assert seen["subscription"] == [], "a live stream keeps its subscription"
    assert seen["start"] == []
    assert page.shown == []


def test_subscribe_refuses_an_invalid_symbol_as_a_warning() -> None:
    orchestrator, _page, _store, _evaluator, seen = _build()
    orchestrator.request_target_subscribe("!!")

    assert seen["refused"][0][0] == REFUSAL_WARNING
    assert seen["subscription"] == []
    assert seen["start"] == []


# -- strategy selection ----------------------------------------------------


def test_strategy_selection_uses_the_emitted_id_and_refreshes_the_preflight() -> None:
    """The argument is the truth, not a re-read of the combo.

    Reading the combo again would let the widget decide which version the runtime
    adopts, and could pick up a row the operator never clicked.
    """

    orchestrator, _page, _store, evaluator, seen = _build(
        strategy=_strategy(),
    )
    orchestrator.request_strategy_selection("targeted-v9")

    assert seen["selected"] == ["targeted-v9"]
    assert evaluator.calls, "a selection change re-derives the preflight"


def test_refreshing_strategy_options_publishes_both_the_options_and_the_selection() -> None:
    from us_quant.desktop_v2.pages.research.targeted.models import (
        TargetedStrategyOption,
    )

    versions = (_strategy("v1"), _strategy("v2"))
    options = tuple(
        TargetedStrategyOption(version.identity.version_id, version.semver)
        for version in versions
    )
    orchestrator, page, _store, _evaluator, _seen = _build(
        options=options, displayed=versions[1]
    )
    orchestrator.refresh_strategy_options()

    assert page.options == [(options, "v2")]


# -- minute status and preflight independence ------------------------------


def test_refreshing_the_minute_status_does_not_recompute_the_preflight() -> None:
    """The two are independent; "consistent" is not a reason to couple them.

    A minute refresh that recomputed the preflight would make one refresh cause
    another, which is the implicit fan-out this round keeps out.
    """

    orchestrator, _page, store, evaluator, _seen = _build()
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_minute_status()

    assert store.reads == ["AAPL"]
    assert evaluator.calls == [], "a minute refresh must not run a preflight"
    assert orchestrator.snapshot.minute_status.startswith("分钟证据 · AAPL：")
    assert orchestrator.snapshot.preflight is None


def test_refreshing_the_minute_status_for_an_invalid_target_shows_the_default() -> None:
    orchestrator, _page, store, _evaluator, _seen = _build()
    orchestrator.adopt_target_draft("!!")
    orchestrator.refresh_minute_status()

    assert store.reads == [], "no store read for a symbol that cannot be one"
    assert orchestrator.snapshot.minute_status == DEFAULT_MINUTE_STATUS


def test_the_minute_status_reads_the_store_through_the_service() -> None:
    summary = MinuteDataSummary(
        symbol="AAPL",
        total_rows=20,
        usable_rows=18,
        first_minute="2026-09-22T14:00:00+00:00",
        last_minute="2026-09-22T19:59:00+00:00",
        providers=("IBKR",),
        evidence_origins=("captured_stream",),
    )
    orchestrator, _page, _store, _evaluator, _seen = _build(
        store=_Store({"AAPL": summary})
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_minute_status()

    assert orchestrator.snapshot.minute_status == (
        "分钟证据 · AAPL：可用 18 / 总计 20 行 · 来源 IBKR · "
        "证据类型 captured_stream · 区间 "
        "2026-09-22T14:00:00+00:00 → 2026-09-22T19:59:00+00:00"
    )


# -- preflight facts -------------------------------------------------------


def test_the_preflight_receives_the_current_external_facts() -> None:
    """Every fact is read fresh, and the lookups are the frozen rules."""

    record = _record("AAPL")
    quote = _quote("AAPL")
    account = _account()
    strategy = _strategy()
    orchestrator, _page, _store, evaluator, _seen = _build(
        universe=_universe(record),
        market=_stream(quote, _quote("MSFT")),
        account=account,
        strategy=strategy,
        multipliers={"AAPL": Decimal("2")},
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_preflight()

    call = evaluator.calls[0]
    assert call["symbol"] == "AAPL"
    assert call["universe_record"] is record
    assert call["quote"] is quote, "the target's own quote, never a fallback"
    assert call["account"] is account
    assert call["strategy"] is strategy
    assert call["exposure_multiplier"] == Decimal("2")


def test_the_preflight_has_no_quote_when_the_stream_lacks_the_symbol() -> None:
    orchestrator, _page, _store, evaluator, _seen = _build(
        market=_stream(_quote("MSFT"))
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_preflight()

    assert evaluator.calls[0]["quote"] is None


def test_the_preflight_defaults_the_multiplier_to_one() -> None:
    orchestrator, _page, _store, evaluator, _seen = _build()
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_preflight()
    assert evaluator.calls[0]["exposure_multiplier"] == Decimal("1")


def test_the_service_hard_disables_the_broker_route() -> None:
    """Research Targeted cannot acquire broker execution authority.

    Asserted on the *domain* function the service calls, because that is where a
    ``True`` would have to travel: the service offers no parameter for it, so no
    caller can ask, and this pins the literal it passes.
    """

    import inspect

    from us_quant import desktop_targeted_session_service as module

    source = inspect.getsource(module.DesktopTargetedSessionService)
    assert "broker_orders_available=False" in source
    assert "broker_orders_available" not in inspect.signature(
        module.DesktopTargetedSessionService.evaluate_preflight
    ).parameters


def test_a_failing_preflight_propagates_and_changes_nothing() -> None:
    """The failure must be visible, and the stale verdict must not be repainted.

    Three assertions, and all three matter.  The retired
    ``_refresh_target_preflight`` let the exception escape, so swallowing it here
    would be an error-semantics change dressed as an extraction -- and a silent one
    is worse than noisy: the operator would keep reading the previous verdict as
    current, while the gates it carries (Paper account freshness, quote freshness,
    whole-share capacity) are exactly the ones that must not look valid after a
    failed refresh.

    So: it raises, the last good result is untouched, and nothing is repainted.
    """

    store = _Store()
    first = _preflight("AAPL")
    service, evaluator = _service(store, result=first)
    page = _Page()
    orchestrator = TargetedSessionOrchestrator(
        page=page,
        service=service,
        universe_provider=lambda: None,
        market_snapshot_provider=lambda: None,
        market_is_live=lambda: False,
        account_provider=lambda: None,
        selected_strategy_provider=lambda: None,
        displayed_strategy_provider=lambda: None,
        strategy_options_provider=lambda: (),
        strategy_selector=lambda _version_id: None,
        exposure_multipliers_provider=lambda: {},
        shadow_snapshot_provider=lambda: None,
        market_set_subscription=lambda *_a, **_k: None,
        market_start=lambda: None,
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_preflight()
    assert orchestrator.snapshot.preflight is first
    painted = len(page.rendered)

    evaluator.raises = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        orchestrator.refresh_preflight()

    assert orchestrator.snapshot.preflight is first, "the last good result stands"
    assert len(page.rendered) == painted, "a failure must not repaint a verdict"


def test_a_failing_provider_propagates_too() -> None:
    """Not only the evaluator: a provider read is a refresh step like any other.

    A ``Universe``/``Market``/``Account`` provider that raises must surface as
    well, otherwise the swallow would just move one frame up the call.
    """

    def broken() -> None:
        raise RuntimeError("provider down")

    orchestrator, _page, _store, _evaluator, _seen = _build(
        result=_preflight("AAPL")
    )
    orchestrator.adopt_target_draft("AAPL")
    orchestrator.refresh_preflight()

    orchestrator._universe_provider = broken
    with pytest.raises(RuntimeError, match="provider down"):
        orchestrator.refresh_preflight()


# -- render ----------------------------------------------------------------


def test_render_reads_a_fresh_shadow_snapshot_and_never_stores_it() -> None:
    """The snapshot is external truth, read per paint.

    Storing it would make this capability a second owner of mutable Shadow state;
    the assertion is that a *later* snapshot is what the next paint sees.
    """

    holder: dict = {"shadow": _shadow(active=True)}
    store = _Store()
    service, _evaluator = _service(store)
    page = _Page()
    orchestrator = TargetedSessionOrchestrator(
        page=page,
        service=service,
        universe_provider=lambda: None,
        market_snapshot_provider=lambda: None,
        market_is_live=lambda: False,
        account_provider=lambda: None,
        selected_strategy_provider=lambda: None,
        displayed_strategy_provider=lambda: None,
        strategy_options_provider=lambda: (),
        strategy_selector=lambda _version_id: None,
        exposure_multipliers_provider=lambda: {},
        shadow_snapshot_provider=lambda: holder["shadow"],
        market_set_subscription=lambda *_a, **_k: None,
        market_start=lambda: None,
    )
    orchestrator.render_current()
    assert page.rendered[-1].status.value == "运行中"

    holder["shadow"] = _shadow(active=False)
    orchestrator.render_current()
    assert page.rendered[-1].status.value == "已停止"

    # And the session snapshot holds no Shadow field at all.
    assert not hasattr(orchestrator.snapshot, "shadow_snapshot")
    assert not hasattr(orchestrator.snapshot, "shadow_active")


def test_the_controls_follow_whether_the_simulation_runs() -> None:
    for active in (False, True):
        holder = {"shadow": _shadow(active=active)}
        store = _Store()
        service, _evaluator = _service(store)
        page = _Page()
        orchestrator = TargetedSessionOrchestrator(
            page=page,
            service=service,
            universe_provider=lambda: None,
            market_snapshot_provider=lambda: None,
            market_is_live=lambda: False,
            account_provider=lambda: None,
            selected_strategy_provider=lambda: None,
            displayed_strategy_provider=lambda: None,
            strategy_options_provider=lambda: (),
            strategy_selector=lambda _version_id: None,
            exposure_multipliers_provider=lambda: {},
            shadow_snapshot_provider=lambda: holder["shadow"],
            market_set_subscription=lambda *_a, **_k: None,
            market_start=lambda: None,
        )
        orchestrator.render_current()
        controls = page.rendered[-1].controls

        assert controls.strategy_enabled is not active
        assert controls.target_enabled is not active
        assert controls.subscribe_enabled is not active
        assert controls.shadow_start_enabled is not active
        assert controls.shadow_stop_enabled is active
        assert controls.replay_enabled is True
        assert controls.robustness_enabled is True


def test_render_paints_only_the_session_half() -> None:
    """The evidence tables have their own owner; a session paint is not theirs."""

    import pathlib

    source = (
        pathlib.Path(
            "src/us_quant/desktop_v2/orchestration/research/targeted/session/"
            "orchestrator.py"
        ).read_text(encoding="utf-8")
    )
    assert "render_evidence" not in source


def test_the_capability_exposes_no_separate_session_accessors() -> None:
    """One immutable snapshot, not four accessors that can disagree.

    Two real consumers read these facts (Targeted Evidence and the Shadow start
    path), which is why the snapshot is public -- but four methods would let each
    read at a different moment.
    """

    for name in (
        "target_draft",
        "target_status",
        "minute_status",
        "preflight",
    ):
        assert not hasattr(TargetedSessionOrchestrator, name), name


def test_the_capability_does_not_own_the_market_lifecycle() -> None:
    """No polling, no stop, no switch: subscribing and starting are the whole
    cross-workflow surface, and both are injected callables."""

    import inspect

    from us_quant.desktop_v2.orchestration.research.targeted.session import (
        orchestrator as module,
    )

    source = inspect.getsource(module)
    for forbidden in (
        "market_orchestrator",
        "worker_running",
        "request_switch",
        "stop_polling",
        ".stop(",
    ):
        assert forbidden not in source, forbidden
