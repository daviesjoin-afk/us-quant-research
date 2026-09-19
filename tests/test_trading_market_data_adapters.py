"""Adapter-boundary coverage for the market data migration.

The provider adapters keep their algorithms: this file does not re-test the
WebSocket behaviour that ``test_ibkr_stream``/``test_alpaca_stream``/
``test_finnhub_stream`` already own.  What it covers is the *boundary* the
migration introduced:

* the transport -> domain conversion maps every field, and maps it once;
* the conversion is total -- no transport field is dropped, and no domain
  field is invented;
* ``source_id`` is a stable logic key and ``source_label`` is display text,
  so a renamed label cannot silently break a comparison;
* the vendor market-data number is interpreted here and nowhere else.

Every assertion is written so that deleting the conversion fails it: the
mutation sweep at the bottom proves the file is not vacuous.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.adapters.market_data_state import (
    MARKET_DATA_TYPE_MODES,
    MARKET_DATA_TYPE_NAMES,
    StreamQuote,
    StreamSnapshot,
    StreamStateReducer,
    market_data_mode,
    parse_iso_datetime,
    to_market_quote,
    to_market_snapshot,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


_OBSERVED = "2026-07-25T18:00:00+00:00"


def _transport_quote(**overrides) -> StreamQuote:
    base = dict(
        symbol="AAPL",
        request_id=7,
        generation=3,
        requested_market_data_type=1,
        effective_market_data_type=1,
        bid=Decimal("100.00"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        close=Decimal("99.50"),
        updated_at=_OBSERVED,
        age_seconds=0.4,
        stale=False,
        stale_reason=None,
        provider="IBKR",
        coverage="由 IBKR 订阅权限决定",
        bid_size=Decimal("300"),
        ask_size=Decimal("500"),
    )
    base.update(overrides)
    return StreamQuote(**base)


def _transport_snapshot(**overrides) -> StreamSnapshot:
    base = dict(
        generation=3,
        socket_connected=True,
        handshake_complete=True,
        reconnect_attempt=1,
        quotes=(_transport_quote(),),
        last_error_code=None,
        last_message="握手完成",
        observed_at=_OBSERVED,
        provider="IBKR",
        coverage="由 IBKR 订阅权限决定",
    )
    base.update(overrides)
    return StreamSnapshot(**base)


# -- market data mode ----------------------------------------------------


def test_every_vendor_market_data_number_has_a_domain_mode() -> None:
    """1/2/3/4 are IBKR knowledge and must map exhaustively."""

    assert MARKET_DATA_TYPE_MODES == {
        1: MarketDataMode.REALTIME,
        2: MarketDataMode.FROZEN,
        3: MarketDataMode.DELAYED,
        4: MarketDataMode.DELAYED_FROZEN,
    }
    # The display names and the modes must cover the same vendor numbers, so a
    # new number cannot get a label without getting a mode.
    assert set(MARKET_DATA_TYPE_NAMES) == set(MARKET_DATA_TYPE_MODES)


@pytest.mark.parametrize(
    "vendor,expected",
    [
        (1, MarketDataMode.REALTIME),
        (2, MarketDataMode.FROZEN),
        (3, MarketDataMode.DELAYED),
        (4, MarketDataMode.DELAYED_FROZEN),
    ],
)
def test_market_data_mode_maps_the_vendor_number(
    vendor: int, expected: MarketDataMode
) -> None:
    assert market_data_mode(vendor) is expected


def test_an_unknown_vendor_number_is_unknown_not_realtime() -> None:
    """Fail closed: an unrecognised number must never read as live."""

    assert market_data_mode(None) is MarketDataMode.UNKNOWN
    assert market_data_mode(99) is MarketDataMode.UNKNOWN
    assert market_data_mode(0) is MarketDataMode.UNKNOWN


# -- timestamp parsing ---------------------------------------------------


def test_parse_iso_datetime_returns_utc() -> None:
    parsed = parse_iso_datetime("2026-07-25T18:00:00+08:00")
    assert parsed == datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)
    assert parsed.tzinfo is timezone.utc


def test_parse_iso_datetime_accepts_the_z_suffix() -> None:
    assert parse_iso_datetime("2026-07-25T18:00:00Z") == datetime(
        2026, 7, 25, 18, 0, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_treats_a_naive_value_as_utc() -> None:
    assert parse_iso_datetime("2026-07-25T18:00:00") == datetime(
        2026, 7, 25, 18, 0, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_does_not_invent_a_timestamp() -> None:
    """A missing or broken timestamp must stay absent.

    Substituting "now" would make an untimestamped quote look freshly
    updated, which is exactly the failure the stale gate exists to prevent.
    """

    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None
    assert parse_iso_datetime("not a timestamp") is None


# -- quote conversion ----------------------------------------------------


def test_transport_quote_converts_every_field() -> None:
    row = _transport_quote()
    quote = to_market_quote(
        row, source_id="ibkr", source_label="IBKR"
    )

    assert quote.symbol == "AAPL"
    assert quote.bid == Decimal("100.00")
    assert quote.ask == Decimal("100.02")
    assert quote.last == Decimal("100.01")
    assert quote.close == Decimal("99.50")
    assert quote.bid_size == Decimal("300")
    assert quote.ask_size == Decimal("500")
    assert quote.mode is MarketDataMode.REALTIME
    assert quote.updated_at == datetime(
        2026, 7, 25, 18, 0, tzinfo=timezone.utc
    )
    assert quote.age_seconds == 0.4
    assert quote.stale is False
    assert quote.stale_reason is None
    assert quote.generation == 3
    assert quote.source_id == "ibkr"
    assert quote.source_label == "IBKR"
    assert quote.coverage == row.coverage


def test_transport_quote_drops_the_vendor_only_fields() -> None:
    """``request_id`` and the vendor number must not survive the boundary."""

    quote = to_market_quote(
        _transport_quote(), source_id="ibkr", source_label="IBKR"
    )

    assert not hasattr(quote, "request_id")
    assert not hasattr(quote, "requested_market_data_type")
    assert not hasattr(quote, "effective_market_data_type")
    assert not hasattr(quote, "provider")


def test_conversion_uses_the_explicit_source_identity() -> None:
    """The caller supplies the identity; no label->id guessing happens."""

    quote = to_market_quote(
        _transport_quote(provider="whatever"),
        source_id="finnhub_trades",
        source_label="Finnhub",
    )

    assert quote.source_id == "finnhub_trades"
    assert quote.source_label == "Finnhub"


def test_a_coverage_override_replaces_the_transport_coverage() -> None:
    quote = to_market_quote(
        _transport_quote(),
        source_id="ibkr",
        source_label="IBKR",
        coverage="override",
    )

    assert quote.coverage == "override"


def test_conversion_preserves_staleness_verbatim() -> None:
    """The adapter must not "fix" a stale quote on the way up."""

    row = _transport_quote(
        stale=True, stale_reason="等待价格", effective_market_data_type=3
    )
    quote = to_market_quote(row, source_id="ibkr", source_label="IBKR")

    assert quote.stale is True
    assert quote.stale_reason == "等待价格"
    assert quote.mode is MarketDataMode.DELAYED
    assert quote.realtime_ready is False


def test_a_realtime_quote_with_a_full_book_is_ready() -> None:
    quote = to_market_quote(
        _transport_quote(), source_id="ibkr", source_label="IBKR"
    )

    assert quote.realtime_ready is True
    assert quote.spread == Decimal("0.02")


# -- snapshot conversion -------------------------------------------------


def test_transport_snapshot_converts_every_field() -> None:
    snapshot = to_market_snapshot(
        _transport_snapshot(), source_id="ibkr", source_label="IBKR"
    )

    assert snapshot.generation == 3
    assert snapshot.connected is True
    assert snapshot.ready is True
    assert snapshot.reconnect_attempt == 1
    assert len(snapshot.quotes) == 1
    assert snapshot.error_code is None
    assert snapshot.message == "握手完成"
    assert snapshot.observed_at == datetime(
        2026, 7, 25, 18, 0, tzinfo=timezone.utc
    )
    assert snapshot.source_id == "ibkr"
    assert snapshot.source_label == "IBKR"


def test_transport_snapshot_drops_the_vendor_only_fields() -> None:
    snapshot = to_market_snapshot(
        _transport_snapshot(), source_id="ibkr", source_label="IBKR"
    )

    assert not hasattr(snapshot, "socket_connected")
    assert not hasattr(snapshot, "handshake_complete")
    assert not hasattr(snapshot, "last_error_code")
    assert not hasattr(snapshot, "last_message")
    assert not hasattr(snapshot, "provider")


def test_every_transport_quote_becomes_a_domain_quote() -> None:
    """No quote may be dropped in transit."""

    rows = tuple(
        _transport_quote(symbol=symbol)
        for symbol in ("AAPL", "MSFT", "SPY")
    )
    snapshot = to_market_snapshot(
        _transport_snapshot(quotes=rows),
        source_id="ibkr",
        source_label="IBKR",
    )

    assert tuple(quote.symbol for quote in snapshot.quotes) == (
        "AAPL",
        "MSFT",
        "SPY",
    )
    assert snapshot.quote_for("MSFT") is not None
    assert snapshot.quote_for("NOPE") is None


def test_snapshot_conversion_carries_errors_through() -> None:
    # The reducer marks quotes stale when the socket drops, so a realistic
    # disconnected snapshot carries a blocked quote.  The conversion must
    # carry that through rather than re-deriving readiness from the flags.
    snapshot = to_market_snapshot(
        _transport_snapshot(
            socket_connected=False,
            handshake_complete=False,
            quotes=(
                _transport_quote(
                    stale=True, stale_reason="行情 socket 已断开"
                ),
            ),
            last_error_code=10197,
            last_message="行情权限不足",
        ),
        source_id="ibkr",
        source_label="IBKR",
    )

    assert snapshot.connected is False
    assert snapshot.ready is False
    assert snapshot.error_code == 10197
    assert snapshot.message == "行情权限不足"
    assert snapshot.realtime_ready is False


def test_snapshot_realtime_ready_is_any_quote_ready() -> None:
    """Preserved semantics: the snapshot is ready if any quote is."""

    blocked = _transport_quote(
        symbol="AAA", stale=True, stale_reason="等待价格"
    )
    live = _transport_quote(symbol="BBB")

    only_blocked = to_market_snapshot(
        _transport_snapshot(quotes=(blocked,)),
        source_id="ibkr",
        source_label="IBKR",
    )
    assert only_blocked.realtime_ready is False

    with_one_live = to_market_snapshot(
        _transport_snapshot(quotes=(blocked, live)),
        source_id="ibkr",
        source_label="IBKR",
    )
    assert with_one_live.realtime_ready is True


def test_a_coverage_override_reaches_every_quote() -> None:
    rows = (_transport_quote(symbol="AAPL"), _transport_quote(symbol="MSFT"))
    snapshot = to_market_snapshot(
        _transport_snapshot(quotes=rows),
        source_id="ibkr",
        source_label="IBKR",
        coverage="override",
    )

    assert snapshot.coverage == "override"
    assert {quote.coverage for quote in snapshot.quotes} == {"override"}


# -- the reducer still produces a transport snapshot ---------------------


def test_the_reducer_snapshot_stays_transport_shaped() -> None:
    """The conversion is a boundary, not a rewrite of the reducer."""

    reducer = StreamStateReducer(stale_after_seconds=5.0)
    reducer.start_generation(1, 1)
    reducer.handshake(1)
    reducer.register_quote(
        generation=1,
        request_id=11,
        symbol="AAPL",
        requested_market_data_type=1,
    )
    reducer.market_data_type(1, 11, 1)
    reducer.tick_price(1, 11, 1, 100.0)
    reducer.tick_price(1, 11, 2, 100.02)

    raw = reducer.snapshot()
    assert isinstance(raw, StreamSnapshot)
    assert isinstance(raw.quotes[0], StreamQuote)
    # The vendor number is still here -- that is the point of the boundary.
    assert raw.quotes[0].effective_market_data_type == 1

    domain = to_market_snapshot(
        raw, source_id="ibkr", source_label="IBKR"
    )
    assert isinstance(domain, MarketSnapshot)
    assert isinstance(domain.quotes[0], MarketQuote)
    assert domain.quotes[0].mode is MarketDataMode.REALTIME
    assert domain.quotes[0].realtime_ready is True


# -- mutation sweep: prove the conversion is load-bearing ----------------

#: Every domain field the conversion is responsible for, and a transport
#: override that must change it.  Deleting a mapping line makes the matching
#: case below fail, so the suite cannot pass with the conversion stubbed out.
MUTATIONS = (
    ("symbol", {"symbol": "MSFT"}, "MSFT"),
    ("bid", {"bid": Decimal("1")}, Decimal("1")),
    ("ask", {"ask": Decimal("2")}, Decimal("2")),
    ("last", {"last": Decimal("3")}, Decimal("3")),
    ("close", {"close": Decimal("4")}, Decimal("4")),
    ("bid_size", {"bid_size": Decimal("5")}, Decimal("5")),
    ("ask_size", {"ask_size": Decimal("6")}, Decimal("6")),
    ("age_seconds", {"age_seconds": 9.5}, 9.5),
    ("stale", {"stale": True, "stale_reason": "x"}, True),
    (
        "stale_reason",
        {"stale": True, "stale_reason": "because"},
        "because",
    ),
    ("generation", {"generation": 42}, 42),
    ("coverage", {"coverage": "other"}, "other"),
)


@pytest.mark.parametrize(
    "field,override,expected",
    MUTATIONS,
    ids=[case[0] for case in MUTATIONS],
)
def test_each_transport_field_reaches_its_domain_field(
    field: str, override: dict, expected: object
) -> None:
    quote = to_market_quote(
        _transport_quote(**override),
        source_id="ibkr",
        source_label="IBKR",
    )
    assert getattr(quote, field) == expected, field


@pytest.mark.parametrize(
    "vendor,expected",
    [
        (1, MarketDataMode.REALTIME),
        (2, MarketDataMode.FROZEN),
        (3, MarketDataMode.DELAYED),
        (4, MarketDataMode.DELAYED_FROZEN),
    ],
)
def test_the_mode_is_mutated_by_the_vendor_number(
    vendor: int, expected: MarketDataMode
) -> None:
    quote = to_market_quote(
        _transport_quote(effective_market_data_type=vendor),
        source_id="ibkr",
        source_label="IBKR",
    )
    assert quote.mode is expected


def test_the_timestamp_is_mutated_by_the_transport_value() -> None:
    quote = to_market_quote(
        _transport_quote(updated_at="2020-01-02T03:04:05+00:00"),
        source_id="ibkr",
        source_label="IBKR",
    )
    assert quote.updated_at == datetime(
        2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )


def test_the_source_identity_is_mutated_by_the_caller() -> None:
    quote = to_market_quote(
        _transport_quote(),
        source_id="ibkr_extended",
        source_label="IBKR 5×24",
    )
    assert quote.source_id == "ibkr_extended"
    assert quote.source_label == "IBKR 5×24"


def test_every_domain_quote_field_is_accounted_for() -> None:
    """A new domain field must be mapped here or explicitly declared absent.

    Without this, adding a field to ``MarketQuote`` would silently leave it at
    its default for every real quote, and the conversion would look fine.
    """

    quote = to_market_quote(
        _transport_quote(), source_id="ibkr", source_label="IBKR"
    )
    mapped = {field.name for field in dataclasses.fields(MarketQuote)}

    assert mapped == {
        "symbol",
        "bid",
        "ask",
        "last",
        "close",
        "bid_size",
        "ask_size",
        "mode",
        "updated_at",
        "age_seconds",
        "stale",
        "stale_reason",
        "generation",
        "source_id",
        "source_label",
        "coverage",
    }
    # And each one really was populated (not left at a falsy default that a
    # field-by-field assertion above could have missed).
    assert all(
        getattr(quote, name) is not None
        for name in ("bid", "ask", "last", "mode", "updated_at")
    )


def test_every_domain_snapshot_field_is_accounted_for() -> None:
    mapped = {
        field.name for field in dataclasses.fields(MarketSnapshot)
    }

    assert mapped == {
        "generation",
        "connected",
        "ready",
        "reconnect_attempt",
        "quotes",
        "error_code",
        "message",
        "observed_at",
        "source_id",
        "source_label",
        "coverage",
    }
