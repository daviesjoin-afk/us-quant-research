from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_quant.credential_store import WindowsCredentialStore  # noqa: E402
from us_quant.extended_hours import us_equity_session  # noqa: E402
from us_quant.finnhub_stream import (  # noqa: E402
    FinnhubTradeStream,
)
from us_quant.paths import ApplicationPaths  # noqa: E402


def _load_key() -> tuple[str, str]:
    """Load the Finnhub key exactly like the desktop does (env wins, then
    the DPAPI store at the application state root)."""
    environment_value = os.environ.get("FINNHUB_API_KEY", "").strip()
    if environment_value:
        return environment_value, "environment"
    paths = ApplicationPaths.discover()
    store = WindowsCredentialStore(paths.state_root / "credentials")
    saved = store.load_secret("finnhub_api_key")
    if saved:
        return saved, f"dpapi:{paths.state_root}"
    # Packaged build fallback: LOCALAPPDATA\\USQuantResearch.
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        packaged_store = WindowsCredentialStore(
            Path(local_app_data) / "USQuantResearch" / "credentials"
        )
        packaged = packaged_store.load_secret("finnhub_api_key")
        if packaged:
            return packaged, "dpapi:packaged"
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument(
        "--symbols",
        default="SPY,QQQ,DIA,IWM,AAPL,MSFT",
    )
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds > 120:
        raise ValueError("seconds must be in 1..120")
    symbols = tuple(
        item.strip().upper()
        for item in args.symbols.split(",")
        if item.strip()
    )

    key, key_source = _load_key()
    if not key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "没有找到 Finnhub Key：请先在 系统设置 → API 数据源 保存，"
                    "或设置 FINNHUB_API_KEY 环境变量",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3

    snapshots = []
    stream = FinnhubTradeStream(
        symbols=symbols,
        api_key=key,
        listener=snapshots.append,
    )
    worker = Thread(target=stream.run, daemon=True)
    worker.start()
    deadline = monotonic() + args.seconds
    while monotonic() < deadline:
        sleep(0.2)
    stream.stop()
    worker.join(timeout=5)

    latest = snapshots[-1] if snapshots else stream.snapshot()
    best_by_symbol = {}
    trade_count = 0
    for snapshot in snapshots:
        for quote in snapshot.quotes:
            if quote.last is not None:
                best_by_symbol[quote.symbol] = quote
                trade_count += 1
    session = us_equity_session()
    handshake = any(
        snapshot.handshake_complete for snapshot in snapshots
    )
    fatal_error = any(
        snapshot.last_error_code in {9103} for snapshot in snapshots
    )
    payload = {
        "ok": handshake and not fatal_error,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "us_equity_session": session.value,
        "key_source": key_source,
        "key_length": len(key),
        "proxy": stream._active_proxy,
        "provider": latest.provider,
        "requested_symbols": list(symbols),
        "snapshots_received": len(snapshots),
        "protocol_handshake_seen": handshake,
        "socket_connected": latest.socket_connected,
        "trade_symbols_seen": sorted(best_by_symbol),
        "trade_prints_received": trade_count,
        "quotes": [
            {
                "symbol": quote.symbol,
                "last": str(quote.last),
                "synthetic_bid": str(quote.bid),
                "synthetic_ask": str(quote.ask),
                "age_seconds": quote.age_seconds,
                "ready": quote.realtime_ready,
            }
            for quote in best_by_symbol.values()
        ],
        "last_error_code": latest.last_error_code,
        "last_message": latest.last_message,
        "worker_stopped": not worker.is_alive(),
        "orders_submitted": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if fatal_error:
        return 1
    if handshake:
        # Connection + authentication + subscription succeeded.  During
        # closed/overnight sessions no trade prints are expected; pre-market
        # prints are sparse and a zero count is normal early in the window.
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
