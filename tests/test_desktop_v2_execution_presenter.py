"""Pure-Python tests for the execution page's presentation projection.

The presenter is Qt-free by construction, and these tests are the reason that
matters: they check the operator-visible rules -- broker truth before the
account snapshot before the local estimate, the latency thresholds, the
candidate freshness words -- without constructing a widget, a broker or a
runtime.  If any of them needed Qt, the split would have failed.

Nothing here is a rendering test; the page's own tests cover what reaches the
screen.  These cover the values that reach it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.desktop_v2.pages.execution import presenter
from us_quant.desktop_v2.pages.execution.models import Tone
from us_quant.desktop_v2.pages.execution.rows import (
    candidate_realtime,
    candidate_rows,
    fill_rows,
    latency_rows,
    latency_tone,
    money,
    order_rows,
    position_rows,
    price,
    shadow_price,
    shadow_rows,
    tier_label,
)

NOW = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Quote:
    symbol: str = "AAA"
    bid: Decimal | None = Decimal("99.90")
    ask: Decimal | None = Decimal("100.10")
    realtime_ready: bool = True


@dataclass(frozen=True)
class Position:
    symbol: str = "AAA"
    quantity: int = 10
    average_price: Decimal = Decimal("100")
    opened_at: str = "2026-09-20 13:00"
    high_water: Decimal = Decimal("101")
    provider: str = "session"


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str = "AAA"
    quantity: Decimal = Decimal("10")
    average_cost: Decimal = Decimal("95")


@dataclass(frozen=True)
class Fill:
    execution_id: str = "exec-1"
    intent_id: str = "intent-1"
    occurred_at: str = "2026-09-20 13:05"
    symbol: str = "AAA"
    side: str = "BUY"
    quantity: int = 10
    price: Decimal = Decimal("100")
    estimated_commission: Decimal = Decimal("1.00")
    realized_pnl: Decimal | None = None


@dataclass(frozen=True)
class Candidate:
    symbol: str = "AAA"
    name: str = "Alpha"
    sector: str = "Tech"
    leader_tier: int = 1
    scan_score: Decimal = Decimal("88.5")
    signal: str = "buy"


@dataclass(frozen=True)
class Snapshot:
    session_id: str | None = "abcdef123456"
    active: bool = True
    strategy_version_id: str = "strategy@1"
    parameter_hash: str = "hash"
    candidate_count: int = 3
    initial_equity: Decimal = Decimal("1500")
    estimated_cash: Decimal = Decimal("1000")
    estimated_equity: Decimal = Decimal("1500")
    estimated_realized_pnl: Decimal = Decimal("5")
    estimated_unrealized_pnl: Decimal = Decimal("7")
    positions: tuple[object, ...] = ()
    fills: tuple[object, ...] = ()
    intents: tuple[object, ...] = ()
    pending_orders: tuple[object, ...] = ()
    trades_today: int = 2
    trading_day: str | None = "2026-09-20"
    status: str = "运行中"
    observed_at: str = "2026-09-20T14:00:00+00:00"
    entries_paused: bool = False
    stop_requested: bool = False


@dataclass(frozen=True)
class Account:
    net_liquidation: Decimal | None = Decimal("1490")
    realized_pnl: Decimal | None = Decimal("4")
    unrealized_pnl: Decimal | None = Decimal("6")
    pnl_source: str = "IBKR reqPnL"
    account_alias: str = "DU***17"


@dataclass(frozen=True)
class BrokerState:
    net_liquidation: Decimal | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    positions: tuple[object, ...] = ()


@dataclass(frozen=True)
class Reconciliation:
    intent_id: str = "intent-1"
    symbol: str = "AAA"
    side: str = "BUY"
    intended_quantity: Decimal = Decimal("10")
    executed_quantity: Decimal = Decimal("10")
    reconciled: bool = True
    terminal: bool = False
    latest_status: str | None = None
    reason: str = "filled"
    broker_order_id: int = 42


# -- formatting -----------------------------------------------------------


def test_money_and_price_format_the_way_the_other_pages_do() -> None:
    assert money(Decimal("1500")) == "$1,500.00"
    assert money(Decimal("5"), signed=True) == "+$5.00"
    assert money(Decimal("-5"), signed=True) == "$-5.00"
    assert money(None) == "不可用"
    assert price(Decimal("100.5000")) == "100.5"
    assert price(None) == "—"
    # The cards and the rows share one formatter, so a card and a table cell
    # can never disagree about the same amount.
    assert presenter.money is money


# -- the status card ------------------------------------------------------


@pytest.mark.parametrize(
    "flags,expected",
    (
        ({"active": True}, "运行中"),
        ({"active": True, "entries_paused": True}, "仅管理持仓"),
        ({"active": True, "stop_requested": True}, "停止处理中"),
        ({"active": False}, "已停止"),
    ),
)
def test_the_status_card_names_what_the_session_is_doing(
    flags: dict[str, bool], expected: str
) -> None:
    assert presenter.runtime_status(Snapshot(**flags)).value == expected


# -- broker truth before account before local -----------------------------


def test_the_equity_card_prefers_the_broker_then_the_account_then_local() -> None:
    snapshot, account = Snapshot(), Account()

    broker = BrokerState(net_liquidation=Decimal("2000"))
    view = presenter.account_metrics(snapshot, account, broker)[0]
    assert view.value == "$2,000.00"
    assert view.note == "IBKR Paper 订单会话实时账户摘要"

    view = presenter.account_metrics(snapshot, account, BrokerState())[0]
    assert view.value == "$1,490.00"
    assert view.note == "IBKR Paper 只读快照"

    view = presenter.account_metrics(snapshot, None, BrokerState())[0]
    assert view.value == "$1,500.00"
    assert view.note == "等待券商刷新；显示本地估算"


def test_the_realized_card_prefers_the_broker_then_the_account_then_local() -> None:
    snapshot, account = Snapshot(), Account()

    view = presenter.account_metrics(
        snapshot, account, BrokerState(realized_pnl=Decimal("9"))
    )[1]
    assert view.value == "+$9.00"
    assert view.note == "IBKR reqPnL（订单会话）"

    view = presenter.account_metrics(snapshot, account, BrokerState())[1]
    assert view.value == "+$4.00"
    assert view.note == "IBKR reqPnL"

    view = presenter.account_metrics(snapshot, None, BrokerState())[1]
    assert view.value == "+$5.00"
    assert view.note == "本地估算"


def test_the_unrealized_card_uses_the_same_order() -> None:
    snapshot = Snapshot()

    assert (
        presenter.account_metrics(
            snapshot, Account(), BrokerState(unrealized_pnl=Decimal("-3"))
        )[2].value
        == "$-3.00"
    )
    assert (
        presenter.account_metrics(snapshot, Account(), BrokerState())[2].value
        == "+$6.00"
    )
    assert (
        presenter.account_metrics(snapshot, None, BrokerState())[2].value
        == "+$7.00"
    )


def test_the_position_card_counts_broker_holdings_when_there_are_any() -> None:
    snapshot = Snapshot(positions=(Position(), Position(symbol="BBB")))

    assert presenter.position_metric(snapshot, BrokerState()).value == "2"
    assert (
        presenter.position_metric(
            snapshot, BrokerState(positions=(BrokerPosition(),))
        ).value
        == "1"
    )


def test_the_position_card_notes_the_pending_count_and_the_day_trade_count() -> None:
    view = presenter.position_metric(Snapshot(), BrokerState())
    assert view.note == "在途 0 · 完成交易 2"


def test_the_summary_line_names_the_session_and_the_broker_authority() -> None:
    text = presenter.summary_text(Snapshot())
    assert "候选 3" in text
    assert "abcdef12" in text
    assert "券商订单与持仓必须以 IBKR Paper 回报为准" in text


def test_the_summary_line_handles_a_session_with_no_id() -> None:
    assert "会话 无" in presenter.summary_text(Snapshot(session_id=None))


# -- position rows --------------------------------------------------------


def test_a_broker_holding_is_marked_as_a_broker_holding() -> None:
    rows = position_rows(
        Snapshot(positions=(Position(),)),
        (BrokerPosition(),),
        {"AAA": Quote()},
    )
    assert len(rows) == 1
    assert rows[0].source == "IBKR Paper position"
    assert rows[0].held_for == "—"
    assert rows[0].average_price == "95"


def test_the_local_book_is_the_fallback_when_the_broker_reports_nothing() -> None:
    rows = position_rows(Snapshot(positions=(Position(),)), (), {"AAA": Quote()})
    assert rows[0].source == "session"
    assert rows[0].held_for == "2026-09-20 13:00"
    assert rows[0].quantity == "10"


def test_a_position_is_valued_at_the_mid_of_its_quote() -> None:
    rows = position_rows(Snapshot(positions=(Position(),)), (), {"AAA": Quote()})
    # mid of 99.90 / 100.10 is 100.00, so the unrealized is zero.
    assert rows[0].mark == "100"
    assert rows[0].unrealized == "$0.00"


def test_a_position_falls_back_to_its_cost_basis_without_a_two_sided_quote() -> None:
    one_sided = {"AAA": Quote(bid=Decimal("99.9"), ask=None)}
    rows = position_rows(
        Snapshot(positions=(Position(),)), (), one_sided
    )
    assert rows[0].mark == "100"
    assert rows[0].unrealized == "$0.00"


def test_a_fractional_broker_holding_is_skipped_rather_than_rounded() -> None:
    rows = position_rows(
        Snapshot(),
        (BrokerPosition(quantity=Decimal("10.5")),),
        {"AAA": Quote()},
    )
    assert rows == ()


# -- fill rows ------------------------------------------------------------


def test_fills_are_shown_newest_first() -> None:
    snapshot = Snapshot(
        fills=(Fill(execution_id="old"), Fill(execution_id="new"))
    )
    rows = fill_rows(snapshot)
    assert [row.occurred_at for row in rows] == ["2026-09-20 13:05"] * 2


def test_a_fill_with_no_realized_pnl_reads_as_unavailable() -> None:
    rows = fill_rows(Snapshot(fills=(Fill(),)))
    assert rows[0].realized == "不可用"
    assert rows[0].commission == "$1.00"


# -- shadow rows ----------------------------------------------------------


def test_the_shadow_band_moves_against_the_side_a_fill_would_cross() -> None:
    buy = shadow_price(Decimal("100"), side="BUY")
    sell = shadow_price(Decimal("100"), side="SELL")
    assert buy > Decimal("100")
    assert sell < Decimal("100")


def test_a_shadow_row_without_a_quote_waits_and_says_so() -> None:
    rows = shadow_rows((Candidate(),), {}, {})
    assert rows[0].status == "等待行情"
    assert rows[0].tone is Tone.WARNING
    assert rows[0].bid == "—"
    assert rows[0].shadow_buy == "—"


def test_a_shadow_row_with_a_quote_but_no_order_is_neutral() -> None:
    rows = shadow_rows((Candidate(),), {"AAA": Quote()}, {})
    assert rows[0].status == "无待挂单"
    assert rows[0].tone is Tone.NEUTRAL
    assert rows[0].limit_price == "—"


def test_a_resting_limit_is_the_expected_state() -> None:
    @dataclass(frozen=True)
    class Pending:
        limit_price: Decimal = Decimal("100.5")

    rows = shadow_rows((Candidate(),), {"AAA": Quote()}, {"AAA": Pending()})
    assert rows[0].status == "策略限价已挂"
    assert rows[0].tone is Tone.SUCCESS
    assert rows[0].limit_price == "100.5"


# -- latency rows ---------------------------------------------------------


@pytest.mark.parametrize(
    "latency,expected",
    (
        (0, Tone.SUCCESS),
        (120, Tone.SUCCESS),
        (121, Tone.WARNING),
        (500, Tone.WARNING),
        (501, Tone.ERROR),
    ),
)
def test_the_latency_thresholds_are_unchanged(
    latency: int, expected: Tone
) -> None:
    assert latency_tone(latency) is expected


def test_a_latency_row_is_skipped_when_the_submission_never_completed() -> None:
    rows = latency_rows(
        (
            {"intent_id": "a", "submit_latency_ms": None},
            {"intent_id": "b", "submit_latency_ms": 42, "observed_at": "2026-09-20T14:00:00Z"},
        )
    )
    assert len(rows) == 1
    assert rows[0].intent_id == "b"
    assert rows[0].latency == "42 ms"
    assert rows[0].generated_at == "2026-09-20 14:00:00"
    assert rows[0].tone is Tone.SUCCESS


# -- candidate rows -------------------------------------------------------


def test_the_tier_label_names_the_two_tiers_and_numbers_the_rest() -> None:
    assert tier_label(1) == "龙头"
    assert tier_label(2) == "优质二线"
    assert tier_label(3) == "层级3"


def test_the_candidate_scan_columns_are_rendered() -> None:
    row = candidate_rows((Candidate(),))[0]
    assert row.symbol == "AAA"
    assert row.tier == "龙头"
    assert row.scan_score == "88.5"


def test_the_realtime_column_says_fresh_recent_or_waiting() -> None:
    candidates = (Candidate(),)

    fresh = candidate_realtime(candidates, {"AAA": Quote()}, lambda _s: False)
    assert fresh[0].status == "当前 fresh"
    assert fresh[0].tone is Tone.NEUTRAL

    recent = candidate_realtime(
        candidates, {"AAA": Quote(realtime_ready=False)}, lambda _s: True
    )
    assert recent[0].status == "近30秒有实时成交"

    waiting = candidate_realtime(candidates, {}, lambda _s: False)
    assert waiting[0].status == "等待"
    assert waiting[0].tone is Tone.WARNING


# -- order rows -----------------------------------------------------------


def test_a_reconciled_order_is_good_and_explains_itself() -> None:
    rows = order_rows((Reconciliation(),), {"intent-1": {"limit_price": "100.5", "reason": "day"}})
    assert rows[0].status == "已核对"
    assert rows[0].tone is Tone.SUCCESS
    assert rows[0].quantities == "10/10"
    assert rows[0].limit_price == "100.5"
    assert rows[0].explanation == "filled · day"
    assert rows[0].broker_order_id == "42"


def test_a_terminal_order_that_never_reconciled_is_the_failure_case() -> None:
    rows = order_rows(
        (Reconciliation(reconciled=False, terminal=True, latest_status="cancelled"),),
        {},
    )
    assert rows[0].status == "cancelled"
    assert rows[0].tone is Tone.ERROR


def test_a_live_order_without_a_status_yet_is_merely_pending() -> None:
    rows = order_rows(
        (Reconciliation(reconciled=False, terminal=False, latest_status=None),), {}
    )
    assert rows[0].status == "等待首次状态"
    assert rows[0].tone is Tone.WARNING


def test_an_order_row_tolerates_a_missing_audit_record() -> None:
    rows = order_rows((Reconciliation(),), {})
    assert rows[0].limit_price == "0"
    assert rows[0].explanation == "filled"


# -- the control state ----------------------------------------------------


def test_an_idle_route_offers_the_launch_controls_only() -> None:
    state = presenter.control_state(
        launch_locked=False,
        session_running=False,
        session_paused=False,
        reconcile_available=False,
        resume_ready=False,
        stream_running=False,
    )
    assert state.prepare_enabled
    assert state.start_enabled
    assert state.channel_check_enabled
    assert state.strategy_combo_enabled
    assert state.candidate_limit_enabled
    assert state.capital_limit_enabled
    assert state.arm_confirm_enabled
    assert not state.pause_enabled
    assert not state.resume_enabled
    assert not state.stop_enabled
    assert not state.stop_stream_enabled
    assert not state.reconcile_enabled
    assert not state.resume_reconciliation_enabled


def test_a_locked_launch_closes_every_input_but_keeps_the_session_controls() -> None:
    state = presenter.control_state(
        launch_locked=True,
        session_running=True,
        session_paused=False,
        reconcile_available=False,
        resume_ready=False,
        stream_running=True,
    )
    assert not state.prepare_enabled
    assert not state.start_enabled
    assert not state.strategy_combo_enabled
    assert not state.candidate_limit_enabled
    assert not state.capital_limit_enabled
    assert state.pause_enabled
    assert state.stop_enabled
    # Stopping the feed under a live session would starve the exit gates.
    assert not state.stop_stream_enabled


def test_the_recovery_controls_are_independent_of_each_other() -> None:
    halted = presenter.control_state(
        launch_locked=True,
        session_running=False,
        session_paused=False,
        reconcile_available=True,
        resume_ready=False,
        stream_running=False,
    )
    assert halted.reconcile_enabled
    assert not halted.resume_reconciliation_enabled

    ready = presenter.control_state(
        launch_locked=True,
        session_running=False,
        session_paused=False,
        reconcile_available=False,
        resume_ready=True,
        stream_running=False,
    )
    assert not ready.reconcile_enabled
    assert ready.resume_reconciliation_enabled


# -- the assembled view ---------------------------------------------------


def test_the_view_assembles_every_table_from_one_set_of_facts() -> None:
    view = presenter.build_runtime_view(
        snapshot=Snapshot(positions=(Position(),), fills=(Fill(),)),
        account=Account(),
        broker_state=BrokerState(),
        broker_positions=(),
        quotes={"AAA": Quote()},
        pending_by_symbol={},
        latency=({"intent_id": "a", "submit_latency_ms": 10},),
        reconciliations=(Reconciliation(),),
        audit_by_intent={},
        candidates=(Candidate(),),
        recently_ready=lambda _s: False,
    )
    assert view.status.value == "运行中"
    assert len(view.positions) == 1
    assert len(view.fills) == 1
    assert len(view.shadow) == 1
    assert len(view.latency) == 1
    assert len(view.candidates.candidates) == 1
    assert len(view.candidates.realtime) == 1
    assert len(view.orders) == 1
    assert len(view.candidates.static_key) == 1


def test_the_candidates_view_is_projectable_on_its_own() -> None:
    """The route must be able to draw the shortlist with no session behind it."""

    view = presenter.build_candidates_view(
        candidates=(Candidate(),),
        quotes={"AAA": Quote()},
        recently_ready=lambda _s: False,
    )
    assert view.candidates[0].symbol == "AAA"
    assert view.realtime[0].status == "当前 fresh"
    assert len(view.static_key) == 1
