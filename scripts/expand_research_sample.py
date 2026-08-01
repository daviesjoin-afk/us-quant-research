from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_quant.history_queue import HistoryJobStore  # noqa: E402
from us_quant.paths import ApplicationPaths  # noqa: E402
from us_quant.config import load_config  # noqa: E402
from us_quant.public_history import run_public_history_queue  # noqa: E402
from us_quant.scanner import save_market_scan, scan_market  # noqa: E402
from us_quant.universe import (  # noqa: E402
    enrich_us_profiles,
    load_universe_snapshot,
    prioritized_research_symbols,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-profiles", type=int, default=350)
    parser.add_argument("--history-limit", type=int, default=500)
    parser.add_argument("--history-jobs", type=int, default=350)
    args = parser.parse_args()

    paths = ApplicationPaths.discover(resource_root=ROOT)
    reference_root = ROOT / "data" / "reference"
    universe_path = reference_root / "universe.json"
    snapshot = load_universe_snapshot(universe_path)
    print(
        json.dumps(
            {
                "phase": "start",
                "before": snapshot.summary(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    def profile_progress(done: int, total: int, symbol: str) -> None:
        if total > 0 and (done == total or done % 25 == 0):
            print(
                json.dumps(
                    {
                        "phase": "sec_profiles",
                        "done": done,
                        "total": total,
                        "symbol": symbol,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    snapshot = enrich_us_profiles(
        snapshot,
        cache_root=reference_root / "sec_profiles",
        max_new_profiles=args.new_profiles,
        progress=profile_progress,
    )
    print(
        json.dumps(
            {
                "phase": "profiles_complete",
                "after": snapshot.summary(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    target_symbols = prioritized_research_symbols(
        snapshot,
        limit=args.history_limit,
    )
    normalized_root = ROOT / "data" / "normalized" / "ibkr" / "daily"
    missing = tuple(
        symbol
        for symbol in target_symbols
        if not (normalized_root / symbol).exists()
    )
    store = HistoryJobStore(
        paths.runtime_root / "history_jobs.sqlite3"
    )
    inserted = store.schedule(missing)
    print(
        json.dumps(
            {
                "phase": "history_scheduled",
                "target": len(target_symbols),
                "already_local": len(target_symbols) - len(missing),
                "missing": len(missing),
                "inserted": inserted,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    def history_progress(
        done: int, total: int, symbol: str, status: str
    ) -> None:
        if done == total or done % 20 == 0:
            print(
                json.dumps(
                    {
                        "phase": "daily_history",
                        "done": done,
                        "total": total,
                        "symbol": symbol,
                        "status": status,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    counts = run_public_history_queue(
        store,
        data_root=ROOT / "data",
        maximum_jobs=min(args.history_jobs, len(missing)),
        workers=4,
        progress=history_progress,
    )
    configuration = load_config(ROOT / "configs" / "paper.toml")
    scan = scan_market(
        snapshot,
        data_root=ROOT / "data",
        substitutions=configuration.substitutions,
    )
    save_market_scan(
        scan,
        ROOT / "research" / "results" / "market_scan.json",
    )
    print(
        json.dumps(
            {
                "phase": "complete",
                "universe": snapshot.summary(),
                "history_queue": counts,
                "scan": scan.summary(),
                "data_date": (
                    scan.data_date.isoformat()
                    if scan.data_date is not None
                    else None
                ),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
