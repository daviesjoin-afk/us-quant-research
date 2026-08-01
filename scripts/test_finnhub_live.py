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
from us_quant.finnhub_stream import FinnhubTradeStream  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=20)
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
    state_root = (
        Path(os.environ["LOCALAPPDATA"]) / "USQuantResearch"
    )
    store = WindowsCredentialStore(state_root / "credentials")
    key = store.load_secret("finnhub_api_key")
    if not key:
        raise RuntimeError("saved Finnhub credential is missing")

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
    for snapshot in snapshots:
        for quote in snapshot.quotes:
            if quote.last is not None:
                best_by_symbol[quote.symbol] = quote
    payload = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "provider": latest.provider,
        "coverage": latest.coverage,
        "requested_symbols": list(symbols),
        "snapshots_received": len(snapshots),
        "protocol_handshake_seen": any(
            snapshot.handshake_complete for snapshot in snapshots
        ),
        "trade_symbols_seen": sorted(best_by_symbol),
        "quotes": [
            {
                "symbol": quote.symbol,
                "last": str(quote.last),
                "synthetic_bid": str(quote.bid),
                "synthetic_ask": str(quote.ask),
                "updated_at": quote.updated_at,
                "age_seconds": quote.age_seconds,
                "ready": quote.realtime_ready,
                "coverage": quote.coverage,
            }
            for quote in best_by_symbol.values()
        ],
        "last_error_code": latest.last_error_code,
        "last_message": latest.last_message,
        "worker_stopped": not worker.is_alive(),
        "orders_submitted": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if best_by_symbol else 2


if __name__ == "__main__":
    raise SystemExit(main())
