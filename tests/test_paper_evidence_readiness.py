"""R93: read-only Paper evidence acquisition readiness diagnostics."""

from __future__ import annotations

import ast
import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadiness,
    assess_market_evidence_readiness,
)
from us_quant.trading.application.paper_evidence_readiness import (
    DISPLAY_STATUS,
    EVIDENCE_READY_STATUS,
    IbkrConnectionProjection,
    MarketStreamProjection,
    PaperEvidenceReadinessApplication,
    PaperEvidenceReadinessSpec,
    PaperEvidenceReadinessStatus,
)
from us_quant.trading.composition.paper_evidence_readiness import (
    EvidenceStoreReadPort,
    EvidenceStoreUnavailable,
    build_evidence_readiness,
    build_ibkr_projection,
    load_stream_projection,
)

PARAMETERS = {"warmup_minutes": 5, "momentum_lookback_minutes": 5}
PROVIDER = "IBKR"
SOURCE_ID = "ibkr"
NOW = datetime(2026, 10, 7, 1, 30, tzinfo=UTC)
NY = ZoneInfo("America/New_York")
TARGETS = ("SPY", "QQQ", "AAPL", "NVDA")
FIVE_TARGETS = ("SPY", "QQQ", "AAPL", "NVDA", "TSLA")


def _spec(symbols: tuple[str, ...] = TARGETS, *, provider: str = PROVIDER):
    return PaperEvidenceReadinessSpec(provider=provider, symbols=symbols)


def _ibkr(
    *,
    reachable: bool = True,
    port: int = 4002,
    read_only: bool = True,
    submission: bool = False,
    checked: bool = True,
) -> IbkrConnectionProjection:
    return IbkrConnectionProjection(
        checked=checked,
        socket_reachable=reachable,
        host="127.0.0.1",
        port=port,
        api_read_only=read_only,
        paper_order_submission_enabled=submission,
    )


def _stream(
    *,
    observed: bool = True,
    connected: bool = True,
    realtime: bool = True,
    stalled: bool = False,
    provider: str = PROVIDER,
    observed_at: datetime = NOW,
    symbols: tuple[str, ...] = TARGETS,
) -> MarketStreamProjection:
    return MarketStreamProjection(
        observed=observed,
        source_id=SOURCE_ID,
        provider=provider,
        connected=connected,
        realtime=realtime,
        stalled=stalled,
        expected_symbols=symbols,
        realtime_symbols=symbols,
        last_snapshot_at=observed_at,
    )


def _row(
    symbol: str,
    *,
    status: str = EVIDENCE_READY_STATUS,
    provider: str = PROVIDER,
    captured: int = 25,
    review_ready: int = 25,
    required: int = 25,
    blockers: tuple[str, ...] = (),
) -> MarketEvidenceReadiness:
    return MarketEvidenceReadiness(
        symbol=symbol,
        provider=provider,
        status=status,
        captured_session_count=captured,
        robustness_usable_sessions=review_ready,
        high_quality_sessions=review_ready,
        review_ready_sessions=review_ready,
        required_sessions=required,
        remaining_sessions=max(0, required - review_ready),
        first_session="2026-01-05",
        latest_session="2026-02-06",
        latest_session_quality=None,
        ready_for_targeted_review=status == EVIDENCE_READY_STATUS,
        blockers=blockers,
    )


def _rows(symbols=TARGETS, **kwargs):
    return tuple(_row(symbol, **kwargs) for symbol in symbols)


def _inspect(
    *,
    ibkr=None,
    stream=None,
    evidence=None,
    spec=None,
    unavailable=None,
    clock=lambda: NOW,
):
    application = PaperEvidenceReadinessApplication(
        spec=spec or _spec(), clock=clock
    )
    return application.inspect(
        ibkr=ibkr or _ibkr(),
        stream=stream or _stream(),
        evidence=_rows() if evidence is None else evidence,
        evidence_unavailable_reason=unavailable,
    )


def _complete_rows(
    symbol: str,
    *,
    provider: str = PROVIDER,
    start: datetime = datetime(2026, 1, 5),
    sessions: int = 25,
) -> list[MinuteQuoteRecord]:
    """Build ``sessions`` complete regular sessions of captured evidence."""

    rows: list[MinuteQuoteRecord] = []
    day = start
    while len({row.minute[:10] for row in rows}) < sessions:
        if day.weekday() < 5:
            local_open = day.replace(hour=9, minute=30, tzinfo=NY)
            for index in range(390):
                minute = local_open + timedelta(minutes=index)
                rows.append(
                    MinuteQuoteRecord(
                        symbol=symbol,
                        minute=minute.astimezone(UTC).isoformat(),
                        provider=provider,
                        coverage="unit test",
                        bid=Decimal("100"),
                        ask=Decimal("100.01"),
                        last=Decimal("100"),
                        mode=_REALTIME,
                        realtime_ready=True,
                        stale=False,
                        stale_reason=None,
                        generation=1,
                        evidence_origin="captured_stream",
                        source_age_seconds=1.0,
                        bid_size=Decimal("10"),
                        ask_size=Decimal("11"),
                    )
                )
        day += timedelta(days=1)
    return rows


from us_quant.trading.domain.market import MarketDataMode  # noqa: E402

_REALTIME = MarketDataMode.REALTIME


def _seed(path: Path, rows) -> None:
    """Create a real minute-quote database, then leave it in DELETE journal mode."""

    store = MinuteQuoteStore(path)
    connection = store._connect()
    try:
        with connection:
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
                        row.generation, datetime.now(UTC).isoformat(),
                        row.evidence_origin, row.source_age_seconds,
                        str(row.bid_size), str(row.ask_size),
                    )
                    for row in rows
                ],
            )
    finally:
        connection.close()
    # Fold the WAL back into the main file so the fixture is a single file with
    # a stable byte hash; a live ``-wal`` sidecar would make the hash
    # non-deterministic for reasons unrelated to this module's behaviour.
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")


# --------------------------------------------------------------------------
# R93-01 .. R93-09: the diagnostic verdicts
# --------------------------------------------------------------------------


def test_r93_01_ibkr_disconnected_is_blocked() -> None:
    report = _inspect(ibkr=_ibkr(reachable=False))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "IBKR_SOCKET_UNREACHABLE" in report.blockers
    assert report.ready is False
    assert report.display_status == "BLOCKED"


def test_r93_01b_unchecked_ibkr_fails_closed() -> None:
    report = _inspect(ibkr=_ibkr(checked=False))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "IBKR_NOT_CHECKED" in report.blockers


def test_r93_02_connected_but_delayed_data_is_blocked() -> None:
    report = _inspect(stream=_stream(realtime=False))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_NOT_REALTIME" in report.blockers


def test_r93_02b_disconnected_or_stalled_stream_is_blocked() -> None:
    disconnected = _inspect(stream=_stream(connected=False))
    assert disconnected.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_DISCONNECTED" in disconnected.blockers

    stalled = _inspect(stream=_stream(stalled=True))
    assert stalled.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_STALLED" in stalled.blockers


def test_r93_02c_stale_health_snapshot_is_blocked() -> None:
    stale = _inspect(
        stream=_stream(observed_at=NOW - timedelta(seconds=121))
    )
    assert stale.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_STALLED" in stale.blockers

    fresh = _inspect(
        stream=_stream(observed_at=NOW - timedelta(seconds=119))
    )
    assert fresh.status is PaperEvidenceReadinessStatus.READY


def test_r93_02e_expected_symbol_not_realtime_is_blocked() -> None:
    partial = MarketStreamProjection(
        observed=True,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        connected=True,
        realtime=True,
        stalled=False,
        expected_symbols=TARGETS,
        realtime_symbols=("SPY", "QQQ", "AAPL"),
        last_snapshot_at=NOW,
    )
    report = _inspect(stream=partial)
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_SYMBOL_NOT_REALTIME" in report.blockers
    # The symbol list is a diagnostic, not a per-symbol verdict: the evidence
    # rows still decide what is captured.
    assert report.total_captured_sessions == 4 * 25


def test_r93_02f_expected_symbols_absent_from_targets_do_not_block() -> None:
    projection = MarketStreamProjection(
        observed=True,
        source_id=SOURCE_ID,
        provider=PROVIDER,
        connected=True,
        realtime=True,
        stalled=False,
        expected_symbols=TARGETS + ("TSLA",),
        realtime_symbols=TARGETS,
        last_snapshot_at=NOW,
    )
    report = _inspect(stream=projection)
    assert report.status is PaperEvidenceReadinessStatus.READY
    assert "MARKET_STREAM_SYMBOL_NOT_REALTIME" not in report.blockers


def test_r93_02g_future_health_timestamp_fails_closed() -> None:
    future = _inspect(stream=_stream(observed_at=NOW + timedelta(seconds=5)))
    assert future.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_STALLED" in future.blockers


def test_r93_02d_unobserved_stream_is_blocked() -> None:
    report = _inspect(
        stream=MarketStreamProjection(
            observed=False,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            connected=False,
            realtime=False,
            stalled=False,
        )
    )
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "MARKET_STREAM_NOT_OBSERVED" in report.blockers


def test_r93_03_realtime_stream_with_zero_rows_is_data_collection() -> None:
    report = _inspect(
        evidence=_rows(captured=0, review_ready=0, status="COLLECTING")
    )
    assert report.status is PaperEvidenceReadinessStatus.DATA_COLLECTION
    assert report.ready is False
    assert report.display_status == "BLOCKED_DATA_COLLECTION"
    assert report.total_captured_sessions == 0
    assert "EVIDENCE_NOT_COLLECTED" in report.blockers


def test_r93_04_one_symbol_incomplete_is_blocked() -> None:
    evidence = tuple(
        _row(symbol)
        if symbol != "NVDA"
        else _row(
            symbol,
            status="COLLECTING",
            captured=24,
            review_ready=24,
            blockers=("INSUFFICIENT_QUALIFIED_SESSIONS",),
        )
        for symbol in TARGETS
    )
    report = _inspect(evidence=evidence)
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "EVIDENCE_INCOMPLETE" in report.blockers
    assert "NVDA:INSUFFICIENT_QUALIFIED_SESSIONS" in report.blockers
    assert report.display_status == "BLOCKED"


def test_r93_05_five_targets_complete_is_ready() -> None:
    report = _inspect(
        spec=_spec(FIVE_TARGETS),
        evidence=_rows(FIVE_TARGETS),
        stream=MarketStreamProjection(
            observed=True,
            source_id=SOURCE_ID,
            provider=PROVIDER,
            connected=True,
            realtime=True,
            stalled=False,
            expected_symbols=FIVE_TARGETS,
            realtime_symbols=FIVE_TARGETS,
            last_snapshot_at=NOW,
        ),
    )
    assert report.status is PaperEvidenceReadinessStatus.READY
    assert report.ready is True
    assert report.display_status == "READY"
    assert report.total_captured_sessions == 5 * 25
    assert report.blockers == ()
    assert tuple(item.symbol for item in report.targets) == FIVE_TARGETS


def test_r93_05b_partial_coverage_across_targets_is_not_ready() -> None:
    evidence = _rows() + (
        _row("TSLA", status="COLLECTING", captured=0, review_ready=0),
    )
    report = _inspect(spec=_spec(FIVE_TARGETS), evidence=evidence)
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "EVIDENCE_INCOMPLETE" in report.blockers


def test_r93_06_order_submission_enabled_is_blocked() -> None:
    report = _inspect(
        ibkr=IbkrConnectionProjection(
            checked=True,
            socket_reachable=True,
            host="127.0.0.1",
            port=4002,
            api_read_only=False,
            paper_order_submission_enabled=True,
        )
    )
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "IBKR_ORDER_SUBMISSION_ENABLED" in report.blockers
    assert "IBKR_API_NOT_READ_ONLY" in report.blockers


def test_r93_06b_read_only_api_with_submission_is_rejected() -> None:
    with pytest.raises(ValueError):
        IbkrConnectionProjection(
            checked=True,
            socket_reachable=True,
            host="127.0.0.1",
            port=4002,
            api_read_only=True,
            paper_order_submission_enabled=True,
        )


@pytest.mark.parametrize("port", [4001, 7496])
def test_r93_07_live_port_is_blocked(port: int) -> None:
    report = _inspect(ibkr=_ibkr(port=port))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "IBKR_LIVE_PORT" in report.blockers


@pytest.mark.parametrize("port", [4002, 7497])
def test_r93_07b_paper_ports_are_accepted(port: int) -> None:
    report = _inspect(ibkr=_ibkr(port=port))
    assert report.status is PaperEvidenceReadinessStatus.READY
    assert "IBKR_LIVE_PORT" not in report.blockers


def test_r93_07c_non_paper_port_is_blocked() -> None:
    report = _inspect(ibkr=_ibkr(port=7499))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "IBKR_PORT_NOT_PAPER" in report.blockers


def test_r93_08_provider_mismatch_is_blocked() -> None:
    report = _inspect(stream=_stream(provider="Alpaca"))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "PROVIDER_MISMATCH" in report.blockers


def test_r93_08b_evidence_provider_mismatch_is_blocked() -> None:
    report = _inspect(
        evidence=_rows(status="DATA_INVALID", blockers=("PROVIDER_MISMATCH",))
    )
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "PROVIDER_MISMATCH" in report.blockers
    assert "EVIDENCE_DATA_INVALID" in report.blockers


def test_r93_08c_target_set_mismatch_is_blocked() -> None:
    report = _inspect(evidence=_rows(TARGETS[:-1]))
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "EVIDENCE_TARGET_SET_MISMATCH" in report.blockers


def test_r93_09_unavailable_store_is_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(EvidenceStoreUnavailable):
        build_evidence_readiness(
            spec=_spec(), quote_path=missing, parameters=PARAMETERS
        )
    assert not missing.exists()

    report = _inspect(evidence=(), unavailable="EVIDENCE_STORE_UNAVAILABLE")
    assert report.status is PaperEvidenceReadinessStatus.BLOCKED
    assert "EVIDENCE_STORE_UNAVAILABLE" in report.blockers


def test_r93_09b_unreadable_store_is_blocked(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"this is not a sqlite database")
    with pytest.raises(EvidenceStoreUnavailable):
        build_evidence_readiness(
            spec=_spec(), quote_path=corrupt, parameters=PARAMETERS
        )


# --------------------------------------------------------------------------
# R93-10: the diagnostic is read-only
# --------------------------------------------------------------------------


def test_r93_10_readiness_does_not_write_the_database(tmp_path: Path) -> None:
    database = tmp_path / "minute_quotes.sqlite3"
    rows = _complete_rows("SPY") + _complete_rows("QQQ")
    _seed(database, rows)
    before = sha256(database.read_bytes()).hexdigest()

    result = build_evidence_readiness(
        spec=_spec(("SPY", "QQQ")),
        quote_path=database,
        parameters=PARAMETERS,
    )
    report = _inspect(
        spec=_spec(("SPY", "QQQ")),
        evidence=result,
    )

    assert [item.symbol for item in result] == ["SPY", "QQQ"]
    assert all(item.status == EVIDENCE_READY_STATUS for item in result)
    assert report.status is PaperEvidenceReadinessStatus.READY
    assert sha256(database.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "minute_quotes.sqlite3"
    ]


def test_r93_10b_read_only_store_refuses_writes(tmp_path: Path) -> None:
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, _complete_rows("SPY", sessions=1))
    store = MinuteQuoteStore(database, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO minute_quote (symbol, minute, provider, coverage,"
                " bid, ask, last, mode, realtime_ready, stale, stale_reason,"
                " generation, recorded_at) VALUES"
                " ('SPY', 'x', 'IBKR', 'c', '1', '1', '1', 'realtime', 1, 0,"
                " NULL, 1, 'now')"
            )


def test_r93_10c_health_log_is_never_created(tmp_path: Path) -> None:
    health = tmp_path / "capture.health.jsonl"
    projection = load_stream_projection(
        health, default_source_id=SOURCE_ID, default_provider=PROVIDER
    )
    assert projection.observed is False
    assert not health.exists()


def test_r93_10d_composition_never_creates_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "nested" / "minute_quotes.sqlite3"
    constructed: list[object] = []

    import us_quant.trading.composition.paper_evidence_readiness as composition

    class _Spy:
        def __init__(self, *args, **kwargs) -> None:
            constructed.append((args, kwargs))

    monkeypatch.setattr(composition, "MinuteQuoteStore", _Spy)
    with pytest.raises(EvidenceStoreUnavailable, match="not found"):
        build_evidence_readiness(
            spec=_spec(), quote_path=missing, parameters=PARAMETERS
        )
    assert not missing.exists()
    assert not missing.parent.exists()
    assert constructed == []


FORBIDDEN_SOURCE_TOKENS = (
    "record_evidence_snapshot_once",
    "record_snapshot(",
    "MarketEvidenceCaptureApplication",
    "placeOrder",
    "place_order",
    "submit_order",
    "requests.",
    "urllib",
    "subprocess",
    "PySide6",
    "shutil",
    "open(.*\"w\"",
    "to_writable",
    "connect_sqlite(",
)


@pytest.mark.parametrize(
    "module_path",
    [
        "src/us_quant/paper_evidence_readiness.py",
        "src/us_quant/trading/application/paper_evidence_readiness.py",
        "src/us_quant/trading/composition/paper_evidence_readiness.py",
    ],
)
def test_r93_10e_readiness_layers_hold_no_write_or_trading_authority(
    module_path: str,
) -> None:
    source = Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_imports = {
        name
        for name in imported
        if name.startswith("us_quant.trading.application.execution")
        or name.startswith("us_quant.trading.application.risk")
        or name.startswith("us_quant.trading.application.market_evidence_capture")
        or name.startswith("us_quant.desktop")
        or name.startswith("PySide6")
        or name == "subprocess"
    }
    assert not forbidden_imports, (module_path, forbidden_imports)
    lowered = source.lower()
    for token in (
        "canarystate",
        "evidenceapproval",
        "operationalapproval",
        "tradingready",
    ):
        assert token not in lowered, (module_path, token)
    assert "\nimport subprocess" not in source


def test_r93_10f_composition_opens_the_store_read_only() -> None:
    source = Path(
        "src/us_quant/trading/composition/paper_evidence_readiness.py"
    ).read_text(encoding="utf-8")
    assert "MinuteQuoteStore(store_path, read_only=True)" in source
    assert "read_only=False" not in source


def test_r93_10g_no_new_authority_names_are_introduced() -> None:
    for module_path in (
        "src/us_quant/paper_evidence_readiness.py",
        "src/us_quant/trading/application/paper_evidence_readiness.py",
        "src/us_quant/trading/composition/paper_evidence_readiness.py",
    ):
        source = Path(module_path).read_text(encoding="utf-8")
        for forbidden in (
            "CanaryState",
            "EvidenceApproval",
            "OperationalApproval",
            "TradingReady",
        ):
            assert forbidden not in source, (module_path, forbidden)


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


def _cli():
    from us_quant import paper_evidence_readiness as module

    return module


def test_r93_cli_renders_the_operator_format() -> None:
    module = _cli()
    report = _inspect(
        spec=_spec(),
        evidence=_rows(captured=0, review_ready=0, status="COLLECTING"),
        ibkr=_ibkr(reachable=False),
        stream=_stream(realtime=False),
    )
    text = module.render(report)
    assert text == (
        "IBKR:\n"
        "  socket: FAIL\n"
        "  port: 4002\n"
        "  readonly: PASS\n"
        "  order_submission: DISABLED\n"
        "\n"
        "Market stream:\n"
        "  realtime: FAIL\n"
        "\n"
        "Evidence:\n"
        "  SPY   0/25\n"
        "  QQQ   0/25\n"
        "  AAPL  0/25\n"
        "  NVDA  0/25\n"
        "\n"
        "Blockers:\n"
        "  IBKR_SOCKET_UNREACHABLE\n"
        "  MARKET_STREAM_NOT_REALTIME\n"
        "\n"
        "Status:\n"
        "BLOCKED"
    )


def test_r93_cli_reports_blocked_data_collection() -> None:
    module = _cli()
    report = _inspect(
        evidence=_rows(captured=0, review_ready=0, status="COLLECTING")
    )
    text = module.render(report)
    assert "  SPY   0/25" in text
    assert text.rstrip().endswith("BLOCKED_DATA_COLLECTION")
    assert "Status:\nBLOCKED_DATA_COLLECTION" in text


def test_r93_cli_reports_ready() -> None:
    module = _cli()
    report = _inspect()
    text = module.render(report)
    assert "  socket: PASS" in text
    assert "  readonly: PASS" in text
    assert "  order_submission: DISABLED" in text
    assert text.rstrip().endswith("READY")


def test_r93_cli_json_payload_is_machine_readable() -> None:
    module = _cli()
    report = _inspect(
        evidence=_rows(captured=0, review_ready=0, status="COLLECTING")
    )
    payload = json.loads(module._json_payload(report))
    assert payload["status"] == "DATA_COLLECTION"
    assert payload["display_status"] == "BLOCKED_DATA_COLLECTION"
    assert payload["provider"] == PROVIDER
    assert [item["symbol"] for item in payload["targets"]] == list(TARGETS)
    assert payload["ibkr"]["port"] == 4002
    assert payload["observed_at"].startswith("2026-10-07")


def test_r93_cli_display_status_mapping_is_exhaustive() -> None:
    assert DISPLAY_STATUS[PaperEvidenceReadinessStatus.READY] == "READY"
    assert (
        DISPLAY_STATUS[PaperEvidenceReadinessStatus.DATA_COLLECTION]
        == "BLOCKED_DATA_COLLECTION"
    )
    assert DISPLAY_STATUS[PaperEvidenceReadinessStatus.BLOCKED] == "BLOCKED"
    assert set(DISPLAY_STATUS) == set(PaperEvidenceReadinessStatus)


def test_r93_cli_parses_symbols_and_rejects_blank_lists() -> None:
    module = _cli()
    assert module._normalized_symbols("spy, qqq ,SPY") == ("SPY", "QQQ")
    with pytest.raises(ValueError):
        module._normalized_symbols(" , , ")


def test_r93_cli_uses_shipped_intraday_parameters() -> None:
    module = _cli()
    parameters = module._readiness_parameters()
    assert parameters["warmup_minutes"] == 10
    assert parameters["momentum_lookback_minutes"] == 5


def test_r93_spec_requires_unique_nonblank_symbols() -> None:
    with pytest.raises(ValueError):
        PaperEvidenceReadinessSpec(provider=PROVIDER, symbols=("SPY", "SPY"))
    with pytest.raises(ValueError):
        PaperEvidenceReadinessSpec(provider=PROVIDER, symbols=())
    with pytest.raises(ValueError):
        PaperEvidenceReadinessSpec(provider="  ", symbols=("SPY",))
    spec = PaperEvidenceReadinessSpec(provider=" ibkr ", symbols=("spy",))
    assert spec.provider == "IBKR"
    assert spec.symbols == ("SPY",)


def test_r93_targets_follow_operator_order() -> None:
    # Declared order (SPY, NVDA) is deliberately the reverse of the
    # alphabetical order, so a sort-by-symbol mutation is visible here.
    report = _inspect(
        spec=_spec(("SPY", "NVDA")),
        evidence=(_row("NVDA"), _row("SPY")),
    )
    assert tuple(item.symbol for item in report.targets) == ("SPY", "NVDA")
    assert tuple(item.symbol for item in report.targets) != tuple(
        sorted(item.symbol for item in report.targets)
    )


# --------------------------------------------------------------------------
# Real composition over a real database
# --------------------------------------------------------------------------


def test_r93_composition_reads_real_captured_sessions(tmp_path: Path) -> None:
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, _complete_rows("SPY"))
    rows = build_evidence_readiness(
        spec=_spec(("SPY",)), quote_path=database, parameters=PARAMETERS
    )
    assert len(rows) == 1
    assert rows[0].symbol == "SPY"
    assert rows[0].status == EVIDENCE_READY_STATUS
    assert rows[0].review_ready_sessions == 25


def test_r93_composition_reports_zero_rows_as_collecting(tmp_path: Path) -> None:
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, [])
    rows = build_evidence_readiness(
        spec=_spec(("SPY",)), quote_path=database, parameters=PARAMETERS
    )
    assert rows[0].status == "COLLECTING"
    assert rows[0].captured_session_count == 0
    report = _inspect(spec=_spec(("SPY",)), evidence=rows)
    assert report.status is PaperEvidenceReadinessStatus.DATA_COLLECTION


def test_r93_composition_uses_a_read_only_read_port(tmp_path: Path) -> None:
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, _complete_rows("SPY", sessions=1))
    store = MinuteQuoteStore(database, read_only=True)
    port = EvidenceStoreReadPort(store)
    rows = port.load("SPY", provider=PROVIDER, usable_only=False)
    assert rows
    with pytest.raises(sqlite3.OperationalError):
        with store._connect() as connection:
            connection.execute("DELETE FROM minute_quote")


def _listening_socket() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def test_r93_cli_probes_the_configured_socket(tmp_path: Path) -> None:
    module = _cli()
    listener, port = _listening_socket()
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, [])
    config = tmp_path / "paper.toml"
    config.write_text(
        Path("configs/paper.toml")
        .read_text(encoding="utf-8")
        .replace("port = 4002", f"port = {port}"),
        encoding="utf-8",
    )
    arguments = [
        "--config",
        str(config),
        "--db",
        str(database),
        "--health-log",
        str(tmp_path / "absent.health.jsonl"),
    ]
    try:
        probed = module.build_report(
            module._arguments(arguments),
            config=module.load_config(config),
            symbols=("SPY",),
            parameters=PARAMETERS,
        )
        skipped = module.build_report(
            module._arguments(arguments + ["--skip-socket-probe"]),
            config=module.load_config(config),
            symbols=("SPY",),
            parameters=PARAMETERS,
        )
    finally:
        listener.close()

    assert probed.ibkr.checked is True
    assert probed.ibkr.socket_reachable is True
    assert "IBKR_SOCKET_UNREACHABLE" not in probed.blockers
    assert skipped.ibkr.checked is False
    assert skipped.ibkr.socket_reachable is False
    assert "IBKR_NOT_CHECKED" in skipped.blockers


def test_r93_build_ibkr_projection_never_handshakes() -> None:
    from us_quant.ibkr import IBKRConnectionConfig

    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=17,
        api_read_only=True,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=0.25,
    )
    projection = build_ibkr_projection(config, checked=False)
    assert projection.checked is False
    assert projection.socket_reachable is False
    assert projection.port == 4002


def test_r93_load_stream_projection_reads_the_newest_health_line(
    tmp_path: Path,
) -> None:
    health = tmp_path / "capture.health.jsonl"
    health.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source": SOURCE_ID,
                        "provider": PROVIDER,
                        "broker_api_connected": True,
                        "market_stream_realtime": False,
                        "symbols_expected": list(TARGETS),
                        "symbols_realtime": [],
                        "last_snapshot_at": NOW.isoformat(),
                        "status": "NON_REALTIME",
                    }
                ),
                "not json at all",
                json.dumps(
                    {
                        "source": SOURCE_ID,
                        "provider": PROVIDER,
                        "broker_api_connected": True,
                        "market_stream_realtime": True,
                        "symbols_expected": list(TARGETS),
                        "symbols_realtime": list(TARGETS),
                        "last_snapshot_at": NOW.isoformat(),
                        "status": "RUNNING",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    projection = load_stream_projection(
        health, default_source_id=SOURCE_ID, default_provider=PROVIDER
    )
    assert projection.observed is True
    assert projection.realtime is True
    assert projection.connected is True
    assert projection.expected_symbols == TARGETS
    assert projection.last_snapshot_at == NOW


def test_r93_cli_end_to_end_over_a_real_capture_environment(
    tmp_path: Path,
) -> None:
    module = _cli()
    database = tmp_path / "minute_quotes.sqlite3"
    _seed(database, [])
    health = tmp_path / "capture.health.jsonl"
    health.write_text(
        json.dumps(
            {
                "source": SOURCE_ID,
                "provider": PROVIDER,
                "broker_api_connected": True,
                "market_stream_realtime": True,
                "symbols_expected": list(TARGETS),
                "symbols_realtime": list(TARGETS),
                "last_snapshot_at": datetime.now(UTC).isoformat(),
                "status": "RUNNING",
            }
        ),
        encoding="utf-8",
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 4002))
        listener.listen(1)
    except OSError:
        listener.close()
        pytest.skip("port 4002 is not bindable in this environment")

    config = tmp_path / "paper.toml"
    config.write_text(
        Path("configs/paper.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    try:
        exit_code = module.main(
            [
                "--config",
                str(config),
                "--db",
                str(database),
                "--health-log",
                str(health),
            ]
        )
    finally:
        listener.close()

    assert exit_code == 1
    assert config.read_text(encoding="utf-8") == Path(
        "configs/paper.toml"
    ).read_text(encoding="utf-8")
