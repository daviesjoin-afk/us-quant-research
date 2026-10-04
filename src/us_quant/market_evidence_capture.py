"""Long-running, read-only durable market evidence recorder.

Run with ``python -m us_quant.market_evidence_capture --source ibkr``.
This module intentionally wires only MarketDataApplication and the evidence
capture application; it has no order, risk, portfolio, or strategy imports.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
from threading import Event, Thread
from time import monotonic

from us_quant.config import load_config
from us_quant.credential_store import WindowsCredentialStore
from us_quant.desktop_credentials import DesktopCredentialService
from us_quant.minute_data import MinuteQuoteStore
from us_quant.paths import ApplicationPaths
from us_quant.trading.application.market_data import (
    SOURCE_IBKR_EXTENDED,
    SOURCE_LABELS,
    SUPPORTED_SOURCES,
    MarketDataCredentials,
    MarketDataStartRequest,
)
from us_quant.trading.application.market_evidence_capture import (
    MarketEvidenceCaptureApplication,
)
from us_quant.trading.composition.market_data import (
    build_market_data_application,
)


DEFAULT_SYMBOLS = ("SPY", "QQQ", "AAPL", "NVDA")
DEFAULT_HEALTH_INTERVAL_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    paths = ApplicationPaths.discover()
    parser = argparse.ArgumentParser(
        description="Read-only durable market evidence capture"
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=SUPPORTED_SOURCES,
        help="固定行情源；不会自动切换 provider",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="逗号分隔的固定 symbol 清单",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=paths.runtime_root / "minute_quotes.sqlite3",
        help="minute quote SQLite 数据库路径",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=paths.config_path,
        help="应用配置路径",
    )
    parser.add_argument(
        "--health-interval",
        type=float,
        default=DEFAULT_HEALTH_INTERVAL_SECONDS,
        help="health 输出周期（秒）",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="snapshot 与 durable evidence 采样周期（秒）",
    )
    return parser.parse_args(argv)


def _normalized_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(
        sorted({item.strip().upper() for item in value.split(",") if item.strip()})
    )
    if not symbols:
        raise ValueError("至少需要一个 symbol")
    return symbols


def _health_payload(health: object, *, source_id: str) -> dict[str, object]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source_id,
        "provider": health.provider,
        "broker_api_connected": health.connected,
        "market_stream_ready": health.ready,
        "market_stream_realtime": health.market_stream_realtime,
        "symbols_expected": health.expected_symbols,
        "symbols_observed": health.observed_symbols,
        "symbols_realtime": health.realtime_symbols,
        "last_snapshot_at": (
            health.last_snapshot_at.isoformat()
            if health.last_snapshot_at is not None
            else None
        ),
        "last_durable_minute": health.last_durable_minute,
        "rows_written": health.rows_written,
        "duplicate_rows_ignored": health.duplicate_rows_ignored,
        "status": _health_status(health),
    }


def _health_status(health: object) -> str:
    if not health.connected:
        return "DISCONNECTED"
    if health.capture_stalled:
        return "CAPTURE_STALLED"
    if not health.market_stream_realtime:
        return "NON_REALTIME"
    if not health.ready:
        return "NOT_READY"
    return "RUNNING"


def _extended_route_target(source_id: str, market_data: object) -> str | None:
    if source_id != SOURCE_IBKR_EXTENDED:
        return None
    desired_exchange = market_data.desired_market_exchange(source_id)
    if desired_exchange == market_data.prepared_market_exchange:
        return None
    return desired_exchange


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        symbols = _normalized_symbols(args.symbols)
        if args.health_interval <= 0 or args.poll_interval <= 0:
            raise ValueError("health and poll intervals must be positive")
        config = load_config(args.config)
        if args.source in {"ibkr", "ibkr_extended"}:
            if not config.ibkr.api_read_only:
                raise ValueError("IBKR evidence capture requires api_read_only=true")
            if config.ibkr.paper_order_submission_enabled:
                raise ValueError(
                    "IBKR evidence capture requires paper_order_submission_enabled=false"
                )

        paths = ApplicationPaths.discover()
        credential_store = WindowsCredentialStore(paths.state_root / "credentials")
        credentials_service = DesktopCredentialService(credential_store)
        resolved = credentials_service.resolve_stream_credentials()
        credentials = MarketDataCredentials(
            alpaca_api_key=resolved.alpaca_api_key,
            alpaca_api_secret=resolved.alpaca_api_secret,
            finnhub_api_key=resolved.finnhub_api_key,
        )
        store = MinuteQuoteStore(args.db)
        provider = SOURCE_LABELS[args.source]
        capture = MarketEvidenceCaptureApplication(
            store=store,
            source_id=args.source,
            provider=provider,
            symbols=symbols,
        )
        market_data = build_market_data_application(lambda: config.ibkr)
        request = MarketDataStartRequest(
            source_id=args.source,
            symbols=symbols,
            credentials=credentials,
        )
        # Capture on the bounded polling loop below, not on the provider's
        # WebSocket receive thread. This keeps SQLite work off the push path
        # while still sampling current and stale states at each interval.
        market_data.prepare(request)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"market evidence capture cannot start: {error}", file=sys.stderr)
        return 2

    stopping = Event()
    runner_errors: list[BaseException] = []

    def run_market_data() -> None:
        try:
            market_data.run()
        except BaseException as error:  # surfaced on the control thread
            runner_errors.append(error)
            stopping.set()

    def start_market_data_runner() -> Thread:
        thread = Thread(
            target=run_market_data, name="market-data", daemon=True
        )
        thread.start()
        return thread

    runner = start_market_data_runner()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        last_health = 0.0
        while not stopping.is_set():
            snapshot = market_data.snapshot()
            capture.capture(snapshot)
            now = monotonic()
            if now - last_health >= args.health_interval:
                health = capture.health(snapshot, now_monotonic=now)
                print(
                    json.dumps(
                        _health_payload(health, source_id=args.source),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                last_health = now
            if runner_errors:
                break
            desired_exchange = _extended_route_target(
                args.source, market_data
            )
            if desired_exchange is not None:
                print(
                    "IBKR extended session changed; rotating route "
                    f"from {market_data.prepared_market_exchange} "
                    f"to {desired_exchange}",
                    file=sys.stderr,
                    flush=True,
                )
                market_data.stop()
                runner.join(timeout=10)
                if runner.is_alive():
                    runner_errors.append(
                        RuntimeError(
                            "market data stream did not stop for session rotation"
                        )
                    )
                    break
                if stopping.is_set() or runner_errors:
                    break
                try:
                    market_data.prepare(
                        replace(request, market_exchange=desired_exchange)
                    )
                except (OSError, ValueError, RuntimeError) as error:
                    runner_errors.append(error)
                    break
                runner = start_market_data_runner()
            stopping.wait(args.poll_interval)
    except KeyboardInterrupt:
        stopping.set()
    finally:
        market_data.stop()
        runner.join(timeout=10)
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
    if runner_errors:
        print(
            f"market data stream stopped: {type(runner_errors[0]).__name__}: {runner_errors[0]}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
