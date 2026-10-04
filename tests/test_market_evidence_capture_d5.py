from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pytest
import ast

from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.minute_evidence_quality import (
    evaluate_minute_evidence_session,
    minute_is_in_evaluation_window,
)
from us_quant.trading.application.market_data import SOURCE_IBKR
from us_quant.trading.application.market_evidence_capture import (
    MarketEvidenceCaptureApplication,
    MarketEvidenceCaptureError,
)
from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadinessApplication,
    assess_market_evidence_readiness,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


PARAMETERS = {"warmup_minutes": 5, "momentum_lookback_minutes": 5}
NY = ZoneInfo("America/New_York")


def quote(
    minute: datetime,
    *,
    symbol: str = "SPY",
    source_id: str = SOURCE_IBKR,
    provider: str = "IBKR",
    mode: MarketDataMode = MarketDataMode.REALTIME,
    stale: bool = False,
    bid: Decimal | None = Decimal("100"),
    ask: Decimal | None = Decimal("100.01"),
    age: float | None = 1.0,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=Decimal("100"),
        close=None,
        bid_size=Decimal("10"),
        ask_size=Decimal("11"),
        mode=mode,
        updated_at=minute,
        age_seconds=age,
        stale=stale,
        stale_reason="stale test row" if stale else None,
        generation=1,
        source_id=source_id,
        source_label=provider,
        coverage="unit test",
    )


def snapshot(
    minute: datetime,
    *,
    bid: str = "100",
    symbol: str = "SPY",
    stale: bool = False,
    mode: MarketDataMode = MarketDataMode.REALTIME,
) -> MarketSnapshot:
    item = quote(
        minute,
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(bid) + Decimal("0.01"),
        stale=stale,
        mode=mode,
    )
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=(item,),
        error_code=None,
        message="test",
        observed_at=minute,
        source_id=SOURCE_IBKR,
        source_label="IBKR",
        coverage="unit test",
    )


def make_record(
    minute: datetime,
    *,
    symbol: str = "SPY",
    provider: str = "IBKR",
    origin: str = "captured_stream",
    stale: bool = False,
    mode: MarketDataMode = MarketDataMode.REALTIME,
    bid: Decimal | None = Decimal("100"),
    ask: Decimal | None = Decimal("100.01"),
    age: float | None = 1.0,
) -> MinuteQuoteRecord:
    realtime_ready = (
        mode is MarketDataMode.REALTIME
        and not stale
        and bid is not None
        and ask is not None
        and bid > 0
        and ask >= bid
    )
    return MinuteQuoteRecord(
        symbol=symbol,
        minute=minute.astimezone(timezone.utc).isoformat(),
        provider=provider,
        coverage="unit test",
        bid=bid,
        ask=ask,
        last=Decimal("100"),
        mode=mode,
        realtime_ready=realtime_ready,
        stale=stale,
        stale_reason="stale test row" if stale else None,
        generation=1,
        evidence_origin=origin,
        source_age_seconds=age,
        bid_size=Decimal("10"),
        ask_size=Decimal("11"),
    )


def session_records(
    date: datetime,
    *,
    provider: str = "IBKR",
    origin: str = "captured_stream",
    ages: list[float] | None = None,
    missing_minutes: set[int] | None = None,
) -> list[MinuteQuoteRecord]:
    local_open = date.replace(hour=9, minute=30, tzinfo=NY)
    rows: list[MinuteQuoteRecord] = []
    for index in range(390):
        if index in (missing_minutes or set()):
            continue
        minute = local_open + timedelta(minutes=index)
        age = ages[index - 60] if ages and 60 <= index <= 405 else 1.0
        rows.append(make_record(minute, provider=provider, origin=origin, age=age))
    return rows


def weekdays(start: datetime, count: int) -> list[datetime]:
    dates: list[datetime] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def test_capture_append_once_restart_and_next_minute(tmp_path: Path) -> None:
    db = tmp_path / "minute.sqlite3"
    first = datetime(2026, 7, 6, 14, 0, 10, tzinfo=timezone.utc)
    store = MinuteQuoteStore(db)
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    result = capture.capture(snapshot(first))
    capture.capture(snapshot(first.replace(second=50), bid="123"))
    assert result.rows_written == 1
    assert store.load("SPY", usable_only=False)[0].bid == Decimal("100")

    restarted_store = MinuteQuoteStore(db)
    restarted = MarketEvidenceCaptureApplication(
        store=restarted_store,
        source_id=SOURCE_IBKR,
        provider="IBKR",
        symbols=("SPY",),
    )
    duplicate = restarted.capture(snapshot(first.replace(second=50), bid="123"))
    added = restarted.capture(
        snapshot(first + timedelta(minutes=1), bid="100")
    )
    assert duplicate.duplicate_rows_ignored == 1
    assert added.rows_written == 1
    rows = restarted_store.load("SPY", usable_only=False)
    assert len(rows) == 2
    assert rows[0].bid == Decimal("100")


@pytest.mark.parametrize("origin", ["synthetic_preview", "live_stream_cache"])
def test_real_capture_replaces_non_durable_row_then_is_immutable(
    tmp_path: Path,
    origin: str,
) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    store.record_snapshot(
        snapshot(minute, bid="80"), evidence_origin=origin
    )
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    captured = capture.capture(snapshot(minute, bid="100"))
    duplicate = capture.capture(snapshot(minute, bid="120"))
    rows = store.load("SPY", usable_only=False)
    assert captured.rows_written == 1
    assert duplicate.duplicate_rows_ignored == 1
    assert len(rows) == 1
    assert rows[0].evidence_origin == "captured_stream"
    assert rows[0].bid == Decimal("100")


@pytest.mark.parametrize("origin", ["synthetic_preview", "imported_research"])
def test_generic_snapshot_cannot_overwrite_captured_minute(
    tmp_path: Path,
    origin: str,
) -> None:
    store = MinuteQuoteStore(tmp_path / f"{origin}.sqlite3")
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    capture.capture(snapshot(minute, bid="100"))

    store.record_snapshot(snapshot(minute, bid="80"), evidence_origin=origin)

    rows = store.load("SPY", provider="IBKR", usable_only=False)
    assert len(rows) == 1
    assert rows[0].evidence_origin == "captured_stream"
    assert rows[0].bid == Decimal("100")


def test_duplicate_snapshot_key_is_rejected_before_durable_write(
    tmp_path: Path,
) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    duplicated = replace(
        snapshot(minute),
        quotes=(
            quote(minute, stale=True, bid=None, ask=None),
            quote(minute, bid=Decimal("100"), ask=Decimal("100.01")),
        ),
    )
    with pytest.raises(MarketEvidenceCaptureError, match="invalid capture snapshot"):
        capture.capture(duplicated)
    assert store.load("SPY", usable_only=False) == ()


def test_stale_quote_is_bucketed_by_observation_minute(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    sampled_at = datetime(2026, 7, 6, 14, 2, tzinfo=timezone.utc)
    old_quote = quote(
        sampled_at - timedelta(minutes=2), stale=True, age=None
    )
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )

    capture.capture(replace(snapshot(sampled_at), quotes=(old_quote,)))

    rows = store.load("SPY", provider="IBKR", usable_only=False)
    assert len(rows) == 1
    assert rows[0].minute == sampled_at.isoformat()
    assert rows[0].stale
    assert rows[0].source_age_seconds == 120


def test_generic_live_cache_keeps_quote_minute(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    observed_at = datetime(2026, 7, 6, 14, 2, tzinfo=timezone.utc)
    quote_time = observed_at - timedelta(minutes=2)

    store.record_snapshot(
        replace(snapshot(observed_at), quotes=(quote(quote_time, age=120),))
    )

    rows = store.load("SPY", provider="IBKR", usable_only=False)
    assert len(rows) == 1
    assert rows[0].minute == quote_time.isoformat()
    assert rows[0].evidence_origin == "live_stream_cache"


def test_generic_snapshot_cannot_claim_durable_origin(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="record_evidence_snapshot_once"):
        store.record_snapshot(snapshot(minute), evidence_origin="captured_stream")
    assert store.load("SPY", usable_only=False) == ()


def test_capture_persists_bad_rows_but_excludes_them_from_usable_store(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    start = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    capture.capture(snapshot(start, stale=True))
    capture.capture(snapshot(start + timedelta(minutes=1), mode=MarketDataMode.DELAYED))
    capture.capture(
        replace(
            snapshot(start + timedelta(minutes=2)),
            quotes=(quote(start + timedelta(minutes=2), bid=None),),
        )
    )
    rows = store.load("SPY", usable_only=False)
    assert len(rows) == 3
    assert rows[0].stale and rows[0].evidence_origin == "captured_stream"
    assert rows[1].mode is MarketDataMode.DELAYED
    assert rows[2].bid is None
    assert store.load("SPY") == ()


def test_capture_rejects_identity_mismatch_and_preview_cannot_claim_capture(
    tmp_path: Path,
) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    start = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    with pytest.raises(MarketEvidenceCaptureError):
        capture.capture(replace(snapshot(start), source_id="alpaca_iex"))
    with pytest.raises(MarketEvidenceCaptureError):
        capture.capture(snapshot(start, symbol="QQQ"))
    store.record_snapshot(snapshot(start), evidence_origin="synthetic_preview")
    readiness = assess_market_evidence_readiness(
        store.load("SPY", usable_only=False),
        symbol="SPY",
        provider="IBKR",
        parameters=PARAMETERS,
    )
    assert readiness.captured_session_count == 0
    assert "NO_CAPTURED_DATA" in readiness.blockers


def test_capture_rejects_quote_provider_identity_mismatch(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store, source_id=SOURCE_IBKR, provider="IBKR", symbols=("SPY",)
    )
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    bad = replace(
        snapshot(minute),
        quotes=(quote(minute, source_id="alpaca_iex", provider="Alpaca"),),
    )
    with pytest.raises(MarketEvidenceCaptureError):
        capture.capture(bad)
    assert store.load("SPY", usable_only=False) == ()


def test_symbols_and_providers_remain_independent(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    store.record_evidence_snapshot_once(snapshot(minute))
    other_symbol = replace(
        snapshot(minute),
        quotes=(quote(minute, symbol="QQQ"),),
    )
    store.record_evidence_snapshot_once(other_symbol)
    other_provider = replace(
        snapshot(minute),
        source_id="alpaca_iex",
        source_label="Alpaca",
        quotes=(quote(minute, source_id="alpaca_iex", provider="Alpaca"),),
    )
    store.record_evidence_snapshot_once(other_provider)
    assert len(store.load("SPY", provider="IBKR", usable_only=False)) == 1
    assert len(store.load("QQQ", provider="IBKR", usable_only=False)) == 1
    assert len(store.load("SPY", provider="Alpaca", usable_only=False)) == 1


def test_capture_health_marks_stalled_after_five_minutes(tmp_path: Path) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store,
        source_id=SOURCE_IBKR,
        provider="IBKR",
        symbols=("SPY",),
        stall_after_seconds=300,
    )
    start = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    health = capture.health(snapshot(start), now_monotonic=1e9)
    assert health.connected and health.ready
    assert health.market_stream_realtime
    assert health.capture_stalled
    delayed = capture.health(
        snapshot(start, mode=MarketDataMode.DELAYED), now_monotonic=1e9
    )
    assert not delayed.market_stream_realtime


def test_capture_health_requires_every_expected_symbol_realtime(
    tmp_path: Path,
) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    capture = MarketEvidenceCaptureApplication(
        store=store,
        source_id=SOURCE_IBKR,
        provider="IBKR",
        symbols=("SPY", "QQQ"),
    )
    minute = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    partial = capture.health(snapshot(minute), now_monotonic=1.0)
    assert partial.realtime_symbols == ("SPY",)
    assert not partial.market_stream_realtime

    complete_snapshot = replace(
        snapshot(minute),
        quotes=(quote(minute, symbol="SPY"), quote(minute, symbol="QQQ")),
    )
    complete = capture.health(complete_snapshot, now_monotonic=2.0)
    assert complete.realtime_symbols == ("QQQ", "SPY")
    assert complete.market_stream_realtime


def test_desktop_minute_recorder_uses_mutable_live_cache_origin() -> None:
    source = Path("src/us_quant/desktop.py").read_text(encoding="utf-8")
    method = source.split("    def _record_minute_snapshot(", 1)[1].split(
        "    def ", 1
    )[0]
    assert 'evidence_origin="live_stream_cache"' in method


def test_normalized_symbols_are_fixed_and_deterministic() -> None:
    from us_quant.market_evidence_capture import _normalized_symbols

    assert _normalized_symbols("spy, QQQ,SPY") == ("QQQ", "SPY")
    with pytest.raises(ValueError):
        _normalized_symbols(" , ")


def test_exact_25_qualified_sessions_ready_and_24_collecting() -> None:
    dates = weekdays(datetime(2026, 1, 5), 25)
    rows = [row for day in dates for row in session_records(day)]
    ready = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS
    )
    assert ready.status == "READY"
    assert ready.review_ready_sessions == 25
    assert ready.remaining_sessions == 0
    short = assess_market_evidence_readiness(
        rows[:-390], symbol="SPY", provider="IBKR", parameters=PARAMETERS
    )
    assert short.status == "COLLECTING"
    assert short.review_ready_sessions == 24


def test_25_robust_sessions_with_only_24_quality_sessions_collect() -> None:
    dates = weekdays(datetime(2026, 2, 2), 25)
    rows = [row for day in dates for row in session_records(day)]
    last_day = dates[-1]
    rows = [
        row for row in rows
        if not (
            row.minute == (last_day.replace(hour=10, minute=0, tzinfo=NY)).astimezone(timezone.utc).isoformat()
        )
    ]
    # Eight missing evaluation minutes lower completeness below 98% while
    # leaving a long enough contiguous robustness run.
    missing = {
        (last_day.replace(hour=10, minute=0, tzinfo=NY) + timedelta(minutes=i))
        .astimezone(timezone.utc).isoformat()
        for i in range(8)
    }
    rows = [row for row in rows if row.minute not in missing]
    result = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS
    )
    assert result.robustness_usable_sessions == 25
    assert result.high_quality_sessions == 24
    assert result.status == "COLLECTING"


@pytest.mark.parametrize(
    "ages,missing",
    [([6.0] * 346, set()), (None, set(range(60, 63)))],
)
def test_bad_completeness_gap_or_age_does_not_qualify(ages, missing) -> None:
    day = datetime(2026, 3, 2)
    rows = session_records(day, ages=ages, missing_minutes=missing)
    result = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS, required_sessions=1
    )
    assert result.status == "COLLECTING"
    assert result.review_ready_sessions == 0


def test_eight_disjoint_missing_minutes_fail_completeness_only() -> None:
    day = datetime(2026, 3, 2)
    rows = session_records(day, missing_minutes={30, 70, 110, 150, 190, 230, 270, 310})
    result = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS, required_sessions=1
    )
    assert result.latest_session_quality is not None
    assert result.latest_session_quality.completeness < Decimal("0.98")
    assert result.latest_session_quality.maximum_consecutive_missing == 1
    assert result.review_ready_sessions == 0


def test_provider_mixing_rejected_and_input_order_is_deterministic() -> None:
    day = datetime(2026, 4, 6)
    rows = session_records(day)
    first = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS,
        required_sessions=1,
    )
    shuffled = assess_market_evidence_readiness(
        list(reversed(rows)), symbol="SPY", provider="IBKR", parameters=PARAMETERS,
        required_sessions=1,
    )
    assert first == shuffled
    mixed = assess_market_evidence_readiness(
        rows + [replace(rows[0], provider="Alpaca")],
        symbol="SPY", provider="IBKR", parameters=PARAMETERS,
    )
    assert mixed.status == "DATA_INVALID"
    assert mixed.blockers == ("MULTIPLE_PROVIDERS",)


def test_readiness_application_scopes_rows_to_requested_provider(
    tmp_path: Path,
) -> None:
    store = MinuteQuoteStore(tmp_path / "minute.sqlite3")
    ibkr_rows = session_records(datetime(2026, 4, 6))
    with store._connect() as connection:
        connection.executemany(
            """INSERT INTO minute_quote (
                symbol, minute, provider, coverage, bid, ask, last,
                mode, realtime_ready, stale, stale_reason, generation,
                recorded_at, evidence_origin, source_age_seconds,
                bid_size, ask_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row.symbol, row.minute, row.provider, row.coverage,
                    str(row.bid), str(row.ask), str(row.last), row.mode.value,
                    int(row.realtime_ready), int(row.stale), row.stale_reason,
                    row.generation, datetime.now(timezone.utc).isoformat(),
                    row.evidence_origin, row.source_age_seconds,
                    str(row.bid_size), str(row.ask_size),
                )
                for row in ibkr_rows
            ] + [
                (
                    row.symbol, row.minute, "Alpaca", row.coverage,
                    str(row.bid), str(row.ask), str(row.last), row.mode.value,
                    int(row.realtime_ready), int(row.stale), row.stale_reason,
                    row.generation, datetime.now(timezone.utc).isoformat(),
                    row.evidence_origin, row.source_age_seconds,
                    str(row.bid_size), str(row.ask_size),
                )
                for row in [replace(ibkr_rows[0], provider="Alpaca")]
            ],
        )
    result = MarketEvidenceReadinessApplication(store).inspect(
        symbol="SPY", provider="IBKR", parameters=PARAMETERS
    )
    alpaca_result = MarketEvidenceReadinessApplication(store).inspect(
        symbol="SPY", provider="Alpaca", parameters=PARAMETERS
    )
    assert result.provider == "IBKR"
    assert result.status != "DATA_INVALID"
    assert "MULTIPLE_PROVIDERS" not in result.blockers
    assert alpaca_result.provider == "Alpaca"
    assert alpaca_result.status != "DATA_INVALID"


def test_capture_cli_has_no_trading_or_qt_authority() -> None:
    source = Path("src/us_quant/market_evidence_capture.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(
        token in module.lower()
        for module in imported
        for token in ("qt", "execution", "risk", "portfolio", "strategy")
    )
    assert "build_market_data_application" in source
    assert "market_data.snapshot()" in source
    assert "market_data.prepare(request)" in source
    assert "market_data.prepare(request, listener=capture.capture)" not in source


def test_capture_cli_polls_push_sources_for_stale_transitions() -> None:
    source = Path("src/us_quant/market_evidence_capture.py").read_text(
        encoding="utf-8"
    )
    loop = source.split("while not stopping.is_set():", 1)[1].split(
        "except KeyboardInterrupt:", 1
    )[0]
    assert "capture.capture(snapshot)" in loop
    assert "args.source not in PUSH_LISTENER_SOURCES" not in loop


def test_capture_cli_keeps_durable_writes_off_push_listener() -> None:
    source = Path("src/us_quant/market_evidence_capture.py").read_text(
        encoding="utf-8"
    )
    preparation = source.split("market_data.prepare(request)", 1)[0]
    assert "listener=capture.capture" not in preparation


def test_new_york_dst_and_weekends_are_not_counted() -> None:
    before_dst = datetime(2026, 3, 6)
    after_dst = datetime(2026, 3, 9)
    assert session_records(before_dst)[0].minute.endswith("14:30:00+00:00")
    assert session_records(after_dst)[0].minute.endswith("13:30:00+00:00")
    for date in (before_dst, after_dst):
        result = assess_market_evidence_readiness(
            session_records(date),
            symbol="SPY",
            provider="IBKR",
            parameters=PARAMETERS,
            required_sessions=1,
        )
        assert result.high_quality_sessions == 1
    local_three_pm = datetime(2026, 3, 6, 15, 0, tzinfo=NY)
    assert minute_is_in_evaluation_window(
        local_three_pm.astimezone(timezone.utc).isoformat()
    )
    saturday = datetime(2026, 3, 7)
    rows = session_records(saturday)
    result = assess_market_evidence_readiness(
        rows, symbol="SPY", provider="IBKR", parameters=PARAMETERS
    )
    assert result.captured_session_count == 0
    assert result.status == "COLLECTING"


@pytest.mark.parametrize("missing_side", ["bid", "ask"])
def test_missing_quote_side_is_not_usable(missing_side: str) -> None:
    date = datetime(2026, 3, 2)
    rows = session_records(date)
    index = next(
        index for index, row in enumerate(rows)
        if row.minute.endswith("15:00:00+00:00")
    )
    rows[index] = replace(
        rows[index],
        **{missing_side: None, "realtime_ready": True},
    )
    quality = evaluate_minute_evidence_session(
        date.date().isoformat(), rows
    )
    assert quality.missing_minutes == 1
