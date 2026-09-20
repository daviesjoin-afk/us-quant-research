"""Pure-Python tests for the market route's projection.

``rows.py`` and ``presenter.py`` are Qt-free by construction, and these tests
are why that matters: they pin the operator-visible rules -- the price and age
formatting, the feed label that has to name what a provider actually is, the
readiness counting, the gate explanation -- without constructing a widget, a
stream or a service.  If any of them needed Qt, the split would have failed.

Nothing here is a rendering test; the page's own tests cover what reaches the
screen.  These cover the values that reach it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.desktop_v2.pages.market import presenter
from us_quant.desktop_v2.pages.market.models import (
    MarketReadinessFacts,
    MarketRowTone,
)
from us_quant.desktop_v2.pages.market.rows import (
    age_text,
    connection_label,
    display_values,
    feed_label,
    joined_symbols,
    mode_label,
    price,
    quote_rows,
    row_tone,
    symbol_text,
    updated_text,
)
from us_quant.trading.domain.market import MarketDataMode


NOW = datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Quote:
    """The quote surface the projection reads, and nothing else."""

    symbol: str = "AAPL"
    bid: Decimal | None = Decimal("100.10")
    ask: Decimal | None = Decimal("100.20")
    last: Decimal | None = Decimal("100.15")
    close: Decimal | None = Decimal("99.50")
    spread: Decimal | None = Decimal("0.10")
    mode: MarketDataMode = MarketDataMode.REALTIME
    updated_at: datetime | None = NOW
    age_seconds: float | None = 0.4
    generation: int = 3
    source_id: str = "ibkr"
    source_label: str = "IBKR"
    coverage: str = "Type 1 实时"
    realtime_ready: bool = True
    stale: bool = False
    stale_reason: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """The snapshot surface the projection reads, and nothing else."""

    connected: bool = True
    ready: bool = True
    generation: int = 3
    reconnect_attempt: int = 0
    quotes: tuple[Quote, ...] = (Quote(),)
    error_code: str | None = None
    message: str = "运行中"
    source_id: str = "ibkr"
    source_label: str = "IBKR"
    coverage: str = "Type 1 实时"
    realtime_ready: bool = True


# -- rows: formatting ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        (Decimal("1"), "1"),
        (Decimal("1.2"), "1.2"),
        (Decimal("1234.5"), "1,234.5"),
        (Decimal("1.23456"), "1.2346"),
        (Decimal("100.1000"), "100.1"),
    ],
)
def test_price_formats_the_way_the_table_shows_it(value, expected) -> None:
    assert price(value) == expected


def test_age_text_formats_to_one_decimal() -> None:
    assert age_text(1.25) == "1.2"
    assert age_text(0.0) == "0.0"
    assert age_text(None) == "—"


def test_updated_text_falls_back_to_a_word_not_a_blank() -> None:
    assert updated_text(None) == "未收到"
    assert updated_text(NOW) == NOW.isoformat()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (MarketDataMode.REALTIME, "实时"),
        (MarketDataMode.FROZEN, "冻结"),
        (MarketDataMode.DELAYED, "延迟"),
        (MarketDataMode.DELAYED_FROZEN, "延迟冻结"),
        (MarketDataMode.UNKNOWN, "未知"),
    ],
)
def test_mode_label_spells_every_domain_mode(mode, expected) -> None:
    assert mode_label(mode) == expected


def test_an_unknown_mode_reads_as_unknown_rather_than_raising() -> None:
    assert mode_label("something-else") == "未知"


# -- rows: the symbol field ----------------------------------------------


def test_symbol_text_splits_strips_uppercases_and_dedupes() -> None:
    assert symbol_text(" aapl , msft ,, aapl ") == ("AAPL", "MSFT")


def test_symbol_text_keeps_the_operator_order() -> None:
    """De-duplication must not sort: the operator's order is what they typed."""

    assert symbol_text("MSFT,AAPL") == ("MSFT", "AAPL")


def test_symbol_text_passes_no_judgement_on_the_count() -> None:
    """Thirty-one symbols is for the orchestration layer to refuse, not here."""

    typed = ",".join(f"S{index:02d}" for index in range(31))
    assert len(symbol_text(typed)) == 31
    assert symbol_text("") == ()


def test_joined_symbols_is_the_inverse_of_the_parser() -> None:
    assert joined_symbols(("AAPL", "MSFT")) == "AAPL,MSFT"
    assert symbol_text(joined_symbols(symbol_text("a, b"))) == ("A", "B")


# -- rows: the feed label ------------------------------------------------


def test_alpaca_is_named_as_the_single_exchange_book_it_is() -> None:
    assert feed_label(source_id="alpaca_iex", modes=()) == "IEX 实时"


def test_finnhub_is_named_as_a_trade_stream_not_a_book() -> None:
    assert feed_label(source_id="finnhub_trades", modes=()) == "实时成交"


def test_other_sources_are_described_by_the_modes_they_reported() -> None:
    label = feed_label(
        source_id="ibkr",
        modes=(MarketDataMode.REALTIME, MarketDataMode.DELAYED_FROZEN),
    )
    assert label == "延迟冻结 / 实时"


def test_a_source_with_no_modes_yet_reads_as_waiting() -> None:
    assert feed_label(source_id="ibkr", modes=()) == "等待回调"


# -- rows: connection and tone -------------------------------------------


def test_connection_label_distinguishes_three_states() -> None:
    assert connection_label(Snapshot(ready=True)) == "已握手"
    assert connection_label(Snapshot(ready=False, connected=True)) == "端口已连"
    assert connection_label(Snapshot(ready=False, connected=False)) == "已断开"


def test_row_tone_lets_stale_outrank_ready() -> None:
    """A quote cannot be both; if it is, the operator must see the warning."""

    assert row_tone(realtime_ready=True, stale=False) is MarketRowTone.SUCCESS
    assert row_tone(realtime_ready=False, stale=False) is MarketRowTone.NEUTRAL
    assert row_tone(realtime_ready=True, stale=True) is MarketRowTone.ERROR
    assert row_tone(realtime_ready=False, stale=True) is MarketRowTone.ERROR


def test_quote_rows_projects_every_field_of_every_quote() -> None:
    rows = quote_rows(Snapshot(quotes=(Quote(), Quote(symbol="MSFT"))))

    assert [row.symbol for row in rows] == ["AAPL", "MSFT"]
    first = rows[0]
    assert first.bid == "100.1"
    assert first.ask == "100.2"
    assert first.last == "100.15"
    assert first.close == "99.5"
    assert first.spread == "0.1"
    assert first.mode == "实时"
    assert first.updated_at == NOW.isoformat()
    assert first.age == "0.4"
    assert first.generation == "3"
    assert first.source == "IBKR"
    assert first.coverage == "Type 1 实时"
    assert first.status == "READY"
    assert first.reason == "可用于日内观察"


def test_a_stale_quote_carries_its_reason_and_the_error_tone() -> None:
    stale = Quote(stale=True, stale_reason="订阅失效", realtime_ready=False)
    row = quote_rows(Snapshot(quotes=(stale,)))[0]

    assert row.status == "STALE"
    assert row.reason == "订阅失效"
    assert row.tone is MarketRowTone.ERROR


def test_display_values_omits_the_tone_column() -> None:
    """``tone`` is the row's colour, not one of the fourteen cells."""

    rows = quote_rows(Snapshot())
    (values,) = display_values(rows)

    assert len(values) == 14
    assert values[0] == "AAPL"
    assert values[12] == "READY"
    assert "error" not in values
    assert "success" not in values


# -- presenter: the cards ------------------------------------------------


def test_every_card_has_an_idle_form() -> None:
    """Before any snapshot the route still reads as started-or-not, not blank."""

    assert presenter.connection_metric(None).value == "未启动"
    assert presenter.feed_metric(None).value == "未知"
    assert presenter.readiness_metric(None).value == "否"


def test_the_connection_card_reports_the_generation_and_the_attempts() -> None:
    metric = presenter.connection_metric(
        Snapshot(generation=7, reconnect_attempt=2)
    )

    assert metric.value == "已握手"
    assert "7" in metric.note
    assert "2" in metric.note


def test_a_connected_but_unhandshaken_feed_does_not_read_as_ready() -> None:
    metric = presenter.connection_metric(Snapshot(ready=False))

    assert metric.value == "端口已连"


def test_the_feed_card_carries_the_coverage_verbatim() -> None:
    metric = presenter.feed_metric(
        Snapshot(source_id="finnhub_trades", coverage="实时成交±5bps")
    )

    assert metric.value == "实时成交"
    assert metric.note == "实时成交±5bps"


# -- presenter: readiness ------------------------------------------------


def _facts(**overrides: int) -> MarketReadinessFacts:
    values = {
        "candidate_count": 0,
        "candidate_current_count": 0,
        "candidate_recent_count": 0,
        "reference_count": 0,
        "reference_current_count": 0,
        "reference_recent_count": 0,
        "subscription_count": 0,
        "subscription_current_count": 0,
        "subscription_recent_count": 0,
    }
    values.update(overrides)
    return MarketReadinessFacts(**values)


def test_the_readiness_card_leads_on_candidates_when_there_are_any() -> None:
    metric = presenter.readiness_metric(
        _facts(
            candidate_count=8,
            candidate_current_count=3,
            candidate_recent_count=5,
            reference_count=2,
            reference_current_count=1,
            reference_recent_count=2,
            subscription_count=10,
            subscription_current_count=4,
            subscription_recent_count=6,
        )
    )

    assert "3/8" in metric.value
    assert "1/2" in metric.value
    assert "5/8" in metric.note
    assert "4/10" in metric.note


def test_without_candidates_the_readiness_card_reports_the_subscription() -> None:
    metric = presenter.readiness_metric(
        _facts(
            subscription_count=6,
            subscription_current_count=2,
            subscription_recent_count=5,
        )
    )

    assert "2/6" in metric.value
    assert "5/6" in metric.note


def test_zero_subscriptions_do_not_read_as_a_broken_ratio() -> None:
    metric = presenter.readiness_metric(_facts())

    assert "0/0" in metric.value


# -- presenter: the watchlist card ---------------------------------------


def test_the_watchlist_card_counts_the_symbols_it_is_given() -> None:
    metric = presenter.watchlist_metric(symbol_count=12)

    assert metric.value == "12"
    assert metric.note == "最多 30；不等于全市场研究池"


def test_the_watchlist_card_uses_the_note_when_one_is_supplied() -> None:
    metric = presenter.watchlist_metric(
        symbol_count=1, note="针对性日内 T：AAPL"
    )

    assert metric.value == "1"
    assert metric.note == "针对性日内 T：AAPL"


# -- presenter: the health panel -----------------------------------------


def test_the_idle_health_panel_teaches_the_gates_before_anything_runs() -> None:
    text = presenter.health_text(None)

    assert "marketDataType" in text
    assert "Type 2/3/4" in text
    assert "未启动流服务" in text


def test_the_health_panel_reports_the_handshake_and_the_last_event() -> None:
    text = presenter.health_text(Snapshot())

    assert "连接代次：3" in text
    assert "协议握手：完成" in text
    assert "最近事件：运行中" in text


def test_the_health_panel_says_so_when_there_is_no_error() -> None:
    assert "最近错误：无" in presenter.health_text(Snapshot())


def test_the_health_panel_names_the_last_error_with_its_source() -> None:
    text = presenter.health_text(Snapshot(error_code="10197"))

    assert "IBKR 10197" in text


def test_the_finnhub_gate_explains_the_simulated_band() -> None:
    text = presenter.health_text(Snapshot(source_id="finnhub_trades"))

    assert "±5bps" in text
    assert "不是市场盘口" in text


def test_the_ibkr_gate_explains_that_lower_types_do_not_upgrade() -> None:
    text = presenter.health_text(Snapshot(source_id="ibkr"))

    assert "Type 1" in text
    assert "不会静默升级" in text


def test_the_failure_text_keeps_the_route_usable_offline() -> None:
    text = presenter.failure_text("连接被拒绝")

    assert "流服务失败：连接被拒绝" in text
    assert "可继续离线研究" in text


# -- presenter: the control state ----------------------------------------


def test_a_running_worker_offers_stop_and_a_switch_label() -> None:
    view = presenter.control_view(
        worker_running=True,
        stop_pending=False,
        symbols_enabled=False,
        provider_enabled=True,
    )

    assert view.stop_enabled
    assert view.start_label == "切换 / 重连行情"
    assert not view.symbols_enabled


def test_a_pending_stop_closes_the_stop_again() -> None:
    """A second stop must not be admitted against a dying worker."""

    view = presenter.control_view(
        worker_running=True,
        stop_pending=True,
        symbols_enabled=False,
        provider_enabled=True,
    )

    assert not view.stop_enabled


def test_an_idle_worker_offers_start_and_the_watchlist_load() -> None:
    view = presenter.control_view(
        worker_running=False,
        stop_pending=False,
        symbols_enabled=True,
        provider_enabled=True,
    )

    assert view.start_enabled
    assert not view.stop_enabled
    assert view.load_watchlist_enabled
    assert view.start_label == "启动只读流行情"


def test_the_load_watchlist_control_closes_once_a_worker_runs() -> None:
    view = presenter.control_view(
        worker_running=True,
        stop_pending=False,
        symbols_enabled=False,
        provider_enabled=True,
    )

    assert not view.load_watchlist_enabled


# -- presenter: the assembled view ---------------------------------------


def test_the_idle_view_shows_the_way_in_rather_than_an_empty_grid() -> None:
    view = presenter.build_market_view(
        snapshot=None,
        readiness=None,
        scope="范围分层 · 0",
        rows=(),
        controls=presenter.control_view(
            worker_running=False,
            stop_pending=False,
            symbols_enabled=True,
            provider_enabled=True,
        ),
    )

    assert view.empty_message == presenter.IDLE_EMPTY
    assert view.rows == ()
    assert view.scope == "范围分层 · 0"


def test_a_connected_view_with_no_quotes_waits_rather_than_repeating_idle() -> None:
    view = presenter.build_market_view(
        snapshot=Snapshot(quotes=()),
        readiness=None,
        scope="",
        rows=(),
        controls=presenter.control_view(
            worker_running=True,
            stop_pending=False,
            symbols_enabled=False,
            provider_enabled=True,
        ),
    )

    assert view.empty_message == presenter.WAITING_EMPTY


def test_a_view_with_quotes_shows_no_empty_message() -> None:
    rows = quote_rows(Snapshot())
    view = presenter.build_market_view(
        snapshot=Snapshot(),
        readiness=_facts(),
        scope="",
        rows=rows,
        controls=presenter.control_view(
            worker_running=True,
            stop_pending=False,
            symbols_enabled=False,
            provider_enabled=True,
        ),
    )

    assert view.empty_message is None
    assert view.rows == rows
    # The watchlist card counts the quotes actually shown.
    assert view.watchlist.value == "1"


def test_the_view_carries_the_supplied_watchlist_note_through() -> None:
    view = presenter.build_market_view(
        snapshot=Snapshot(),
        readiness=None,
        scope="",
        rows=(),
        controls=presenter.control_view(
            worker_running=False,
            stop_pending=False,
            symbols_enabled=True,
            provider_enabled=True,
        ),
        watchlist_note="Level I 持续订阅",
    )

    assert view.watchlist.note == "Level I 持续订阅"


def test_the_view_is_immutable() -> None:
    view = presenter.build_market_view(
        snapshot=None,
        readiness=None,
        scope="",
        rows=(),
        controls=presenter.control_view(
            worker_running=False,
            stop_pending=False,
            symbols_enabled=True,
            provider_enabled=True,
        ),
    )

    with pytest.raises(FrozenInstanceError):
        view.scope = "mutated"  # type: ignore[misc]
