from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from us_quant.backtest import BacktestEngine, CostModel
from us_quant.config import load_config
from us_quant.domain import Bar, MarketSlice
from us_quant.ibkr import probe_ibkr_socket
from us_quant.ibkr_history import collect_daily_history
from us_quant.ibkr_readonly import (
    IBKRAPIUnavailable,
    IBKRReadOnlyError,
    collect_readonly_snapshot,
    snapshot_to_redacted_dict,
)
from us_quant.portfolio import IntegerPositionSizer
from us_quant.optimization import (
    MovingAverageCandidate,
    walk_forward_moving_average,
)
from us_quant.strategy import MovingAverageTrendStrategy
from us_quant.market_data import (
    build_aligned_market_slices,
    load_latest_normalized_series,
    save_historical_series,
    validate_daily_series,
)
from us_quant.history_queue import HistoryJobStore, run_history_queue
from us_quant.scanner import scan_market, save_market_scan
from us_quant.public_history import run_public_history_queue
from us_quant.cross_sectional import (
    run_cross_sectional_research,
    save_cross_sectional_research,
)
from us_quant.universe import (
    enrich_us_profiles,
    load_universe_snapshot,
    prioritized_research_symbols,
    refresh_official_universe,
)


def _bar(
    symbol: str,
    timestamp: datetime,
    open_price: str,
    close_price: str,
) -> Bar:
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return Bar(
        symbol=symbol,
        timestamp=timestamp,
        open=open_value,
        high=max(open_value, close_value) * Decimal("1.01"),
        low=min(open_value, close_value) * Decimal("0.99"),
        close=close_value,
        volume=1_000_000,
    )


def demo_slices() -> list[MarketSlice]:
    start = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
    prices = [
        ("960", "970"),
        ("972", "980"),
        ("981", "990"),
        ("992", "1000"),
        ("998", "985"),
        ("986", "975"),
    ]
    slices: list[MarketSlice] = []
    for index, daily_prices in enumerate(prices):
        timestamp = start + timedelta(days=index)
        bars = {"DEMO": _bar("DEMO", timestamp, *daily_prices)}
        slices.append(MarketSlice(timestamp=timestamp, bars=bars))
    return slices


def doctor(config_path: Path) -> int:
    config = load_config(config_path)
    checks = {
        "environment": config.environment.value,
        "live_trading_enabled": config.live_trading_enabled,
        "whole_shares_only": config.whole_shares_only,
        "allow_margin_borrowing": config.allow_margin_borrowing,
        "research_scenario_equity": str(config.initial_equity),
        "broker_account_equity_source": "IBKR API at runtime",
        "ibkr": {
            "host": config.ibkr.host,
            "port": config.ibkr.port,
            "client_id": config.ibkr.client_id,
            "api_read_only": config.ibkr.api_read_only,
            "paper_order_submission_enabled": (
                config.ibkr.paper_order_submission_enabled
            ),
        },
        "approved_substitutions": {
            source: rule.execution_symbol
            for source, rule in config.substitutions.items()
        },
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if config.live_trading_enabled:
        print("ERROR: live trading must remain disabled in Phase 1")
        return 2
    if not config.ibkr.api_read_only:
        print("ERROR: IBKR API must remain read-only before paper acceptance")
        return 2
    if config.ibkr.paper_order_submission_enabled:
        print("ERROR: paper order submission has not passed acceptance")
        return 2
    return 0


def ibkr_probe(config_path: Path) -> int:
    config = load_config(config_path)
    result = probe_ibkr_socket(config.ibkr)
    print(
        json.dumps(
            {
                "reachable": result.reachable,
                "host": result.host,
                "port": result.port,
                "elapsed_ms": result.elapsed_ms,
                "detail": result.detail,
                "scope": "TCP socket only; no login or account access",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.reachable else 3


def ibkr_readonly(config_path: Path) -> int:
    config = load_config(config_path)
    if config.environment.value != "paper" or config.live_trading_enabled:
        print("ERROR: read-only integration requires the paper environment")
        return 2
    try:
        snapshot = collect_readonly_snapshot(config.ibkr)
    except IBKRAPIUnavailable as error:
        print(
            json.dumps(
                {
                    "connected": False,
                    "error": str(error),
                    "next_step": (
                        "Install the official TWS API from "
                        "https://interactivebrokers.github.io/"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 4
    except IBKRReadOnlyError as error:
        print(
            json.dumps(
                {"connected": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 5

    print(
        json.dumps(
            snapshot_to_redacted_dict(snapshot),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def ibkr_history(
    config_path: Path,
    *,
    duration: str,
    data_root: Path,
) -> int:
    config = load_config(config_path)
    if config.environment.value != "paper" or config.live_trading_enabled:
        print("ERROR: history ingestion requires the paper environment")
        return 2
    try:
        series_collection = collect_daily_history(
            config.ibkr,
            duration=duration,
        )
    except IBKRAPIUnavailable as error:
        print(
            json.dumps(
                {"downloaded": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 4
    except IBKRReadOnlyError as error:
        print(
            json.dumps(
                {"downloaded": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 5

    output: list[dict] = []
    all_passed = True
    for series in series_collection:
        quality = validate_daily_series(series)
        artifact = save_historical_series(
            series,
            data_root=data_root,
            include_normalized=quality.passed,
        )
        all_passed = all_passed and quality.passed
        output.append(
            {
                "symbol": quality.symbol,
                "quality_passed": quality.passed,
                "row_count": quality.row_count,
                "first_date": (
                    quality.first_date.isoformat()
                    if quality.first_date
                    else None
                ),
                "last_date": (
                    quality.last_date.isoformat()
                    if quality.last_date
                    else None
                ),
                "issues": [
                    {
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in quality.issues
                ],
                "content_sha256": artifact.content_sha256,
                "raw_path": str(artifact.raw_path),
                "normalized_path": (
                    str(artifact.normalized_path)
                    if artifact.normalized_path
                    else None
                ),
            }
        )

    print(
        json.dumps(
            {
                "downloaded": True,
                "duration": duration,
                "data_root": str(data_root),
                "series": output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all_passed else 6


def demo_backtest(config_path: Path) -> int:
    config = load_config(config_path)
    strategy = MovingAverageTrendStrategy(
        signal_symbol="DEMO",
        short_window=2,
        long_window=3,
        target_weight=Decimal("0.10"),
    )
    engine = BacktestEngine(
        initial_equity=config.initial_equity,
        strategy=strategy,
        position_sizer=IntegerPositionSizer(config.substitutions),
        cost_model=CostModel(
            per_share_commission=config.execution.per_share_commission,
            minimum_commission=config.execution.minimum_commission,
            slippage_bps=config.execution.slippage_bps,
        ),
    )
    result = engine.run(demo_slices())
    output = {
        "initial_equity": str(result.initial_equity),
        "final_equity": str(result.final_equity),
        "total_return": str(result.total_return),
        "max_drawdown": str(result.max_drawdown),
        "total_commission": str(result.total_commission),
        "trades": [
            {
                "time": trade.timestamp.isoformat(),
                "signal_symbol": trade.signal_symbol,
                "execution_symbol": trade.execution_symbol,
                "side": trade.side.value,
                "quantity": trade.quantity,
                "fill_price": str(trade.fill_price),
                "commission": str(trade.commission),
                "used_substitution": trade.used_substitution,
            }
            for trade in result.trades
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def latest_data_backtest(
    config_path: Path,
    *,
    data_root: Path,
    symbol: str,
    short_window: int,
    long_window: int,
    target_weight: Decimal,
) -> int:
    config = load_config(config_path)
    if not 0 < target_weight <= config.risk_limits.max_position_exposure_pct:
        print(
            "ERROR: target weight must be positive and cannot exceed "
            "the configured position risk limit"
        )
        return 2
    try:
        loaded = (
            load_latest_normalized_series(
                symbol.upper(), data_root=data_root
            ),
        )
        slices = build_aligned_market_slices(loaded)
        strategy = MovingAverageTrendStrategy(
            signal_symbol=symbol.upper(),
            short_window=short_window,
            long_window=long_window,
            target_weight=target_weight,
        )
    except (FileNotFoundError, ValueError) as error:
        print(
            json.dumps(
                {"backtested": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 7

    engine = BacktestEngine(
        initial_equity=config.initial_equity,
        strategy=strategy,
        position_sizer=IntegerPositionSizer(config.substitutions),
        cost_model=CostModel(
            per_share_commission=config.execution.per_share_commission,
            minimum_commission=config.execution.minimum_commission,
            slippage_bps=config.execution.slippage_bps,
        ),
    )
    result = engine.run(slices)
    substitution_trades = sum(
        1 for trade in result.trades if trade.used_substitution
    )
    output = {
        "backtested": True,
        "warning": (
            "research baseline only; not a profit forecast or live approval"
        ),
        "strategy": {
            "name": "moving_average_trend",
            "signal_symbol": symbol.upper(),
            "short_window": short_window,
            "long_window": long_window,
            "target_risk_weight": str(target_weight),
        },
        "data": {
            series.symbol: {
                "source_sha256": series.source_sha256,
                "path": str(series.path),
            }
            for series in loaded
        },
        "aligned_rows": len(slices),
        "first_date": slices[0].timestamp.date().isoformat(),
        "last_date": slices[-1].timestamp.date().isoformat(),
        "initial_equity": str(result.initial_equity),
        "final_equity": str(result.final_equity),
        "total_return": str(result.total_return),
        "max_drawdown": str(result.max_drawdown),
        "total_commission": str(result.total_commission),
        "trade_count": len(result.trades),
        "substitution_trade_count": substitution_trades,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def walk_forward(
    config_path: Path,
    *,
    data_root: Path,
    symbol: str,
    target_weight: Decimal,
) -> int:
    config = load_config(config_path)
    if not 0 < target_weight <= config.risk_limits.max_position_exposure_pct:
        print(
            "ERROR: target weight must be positive and cannot exceed "
            "the configured position risk limit"
        )
        return 2
    try:
        loaded = (
            load_latest_normalized_series(
                symbol.upper(), data_root=data_root
            ),
        )
        slices = build_aligned_market_slices(loaded)
        result = walk_forward_moving_average(
            slices,
            signal_symbol=symbol.upper(),
            initial_equity=config.initial_equity,
            position_sizer=IntegerPositionSizer(config.substitutions),
            cost_model=CostModel(
                per_share_commission=(
                    config.execution.per_share_commission
                ),
                minimum_commission=config.execution.minimum_commission,
                slippage_bps=config.execution.slippage_bps,
            ),
            candidates=(
                MovingAverageCandidate(5, 20),
                MovingAverageCandidate(10, 50),
                MovingAverageCandidate(20, 100),
                MovingAverageCandidate(50, 200),
            ),
            target_weight=target_weight,
            minimum_train_days=252,
            test_days=63,
        )
    except (FileNotFoundError, ValueError) as error:
        print(
            json.dumps(
                {"evaluated": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 7

    output = {
        "evaluated": True,
        "warning": (
            "small research sample; result is not a live-trading approval"
        ),
        "method": {
            "type": "anchored_walk_forward",
            "minimum_train_days": 252,
            "test_days": 63,
            "selection_metric": "training annualized Sharpe after costs",
            "candidate_count": 4,
            "target_risk_weight": str(target_weight),
            "signal_symbol": symbol.upper(),
            "execution_policy": (
                "whole shares; optional substitutions only when "
                "explicitly configured"
            ),
            "full_test_folds_only": True,
        },
        "data_hashes": {
            series.symbol: series.source_sha256 for series in loaded
        },
        "out_of_sample_days": result.out_of_sample_days,
        "excluded_tail_days": result.excluded_tail_days,
        "compounded_out_of_sample_return": str(
            result.compounded_out_of_sample_return
        ),
        "out_of_sample_max_drawdown": str(
            result.out_of_sample_max_drawdown
        ),
        "out_of_sample_commission": str(
            result.out_of_sample_commission
        ),
        "out_of_sample_trade_count": result.out_of_sample_trade_count,
        "folds": [
            {
                "fold": fold.fold_number,
                "train_start": fold.train_start.date().isoformat(),
                "train_end": fold.train_end.date().isoformat(),
                "test_start": fold.test_start.date().isoformat(),
                "test_end": fold.test_end.date().isoformat(),
                "selected_parameters": fold.selected.candidate.name,
                "training_sharpe": fold.selected.annualized_sharpe,
                "training_return": str(fold.selected.total_return),
                "training_max_drawdown": str(
                    fold.selected.max_drawdown
                ),
                "oos_return": str(fold.out_of_sample_return),
                "oos_commission": str(
                    fold.out_of_sample_commission
                ),
                "oos_trade_count": fold.out_of_sample_trade_count,
            }
            for fold in result.folds
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def universe_refresh(
    *,
    reference_root: Path,
    leader_seed_path: Path,
    sec_profiles: int,
) -> int:
    try:
        snapshot = refresh_official_universe(
            cache_root=reference_root,
            leader_seed_path=leader_seed_path,
        )
        snapshot = enrich_us_profiles(
            snapshot,
            cache_root=reference_root / "sec_profiles",
            max_new_profiles=sec_profiles,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"refreshed": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 8
    print(
        json.dumps(
            {
                "refreshed": True,
                "summary": snapshot.summary(),
                "snapshot": str(reference_root / "universe.json"),
                "policy": (
                    "只排除中概股；其他国家和地区发行人可研究。"
                    "交易资格仍要求一级龙头或二级优质标的"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def history_schedule(
    *,
    universe_path: Path,
    queue_path: Path,
    limit: int,
) -> int:
    try:
        snapshot = load_universe_snapshot(universe_path)
        symbols = prioritized_research_symbols(snapshot, limit=limit)
        store = HistoryJobStore(queue_path)
        inserted = store.schedule(symbols)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"scheduled": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 8
    print(
        json.dumps(
            {
                "scheduled": True,
                "eligible_symbols": len(symbols),
                "new_jobs": inserted,
                "counts": store.counts(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def history_run(
    config_path: Path,
    *,
    queue_path: Path,
    data_root: Path,
    maximum_jobs: int,
) -> int:
    config = load_config(config_path)
    if config.environment.value != "paper" or config.live_trading_enabled:
        print("ERROR: history queue requires the paper environment")
        return 2
    store = HistoryJobStore(queue_path)
    counts = run_history_queue(
        config.ibkr,
        store,
        data_root=data_root,
        maximum_jobs=maximum_jobs,
        progress=lambda done, total, symbol, status: print(
            f"[{done}/{total}] {symbol}: {status}",
            flush=True,
        ),
    )
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if counts["failed"] == 0 else 6


def history_run_public(
    *,
    queue_path: Path,
    data_root: Path,
    maximum_jobs: int,
) -> int:
    store = HistoryJobStore(queue_path)
    store.reset_failed()
    counts = run_public_history_queue(
        store,
        data_root=data_root,
        maximum_jobs=maximum_jobs,
        progress=lambda done, total, symbol, status: print(
            f"[{done}/{total}] {symbol}: {status}",
            flush=True,
        ),
    )
    print(
        json.dumps(
            {
                **counts,
                "source": "secondary public historical source",
                "live_or_intraday_approved": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if counts["failed"] == 0 else 6


def market_scan(
    config_path: Path,
    *,
    universe_path: Path,
    data_root: Path,
    output_path: Path,
) -> int:
    config = load_config(config_path)
    try:
        universe = load_universe_snapshot(universe_path)
        result = scan_market(
            universe,
            data_root=data_root,
            capital=config.initial_equity,
            substitutions=config.substitutions,
        )
        save_market_scan(result, output_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"scanned": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 8
    print(
        json.dumps(
            {
                "scanned": True,
                "summary": result.summary(),
                "data_date": (
                    result.data_date.isoformat()
                    if result.data_date
                    else None
                ),
                "output": str(output_path),
                "orders_submitted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def research_cross_sectional(
    config_path: Path,
    *,
    universe_path: Path,
    data_root: Path,
    output_path: Path,
) -> int:
    config = load_config(config_path)
    try:
        universe = load_universe_snapshot(universe_path)
        result = run_cross_sectional_research(
            config,
            universe,
            data_root=data_root,
        )
        save_cross_sectional_research(result, output_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"researched": False, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 8
    print(
        json.dumps(
            {
                "researched": True,
                "universe_size": result["scope"]["universe_size"],
                "out_of_sample": result["out_of_sample"],
                "output": str(output_path),
                "orders_submitted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="us-quant")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/paper.toml"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("ibkr-probe")
    subparsers.add_parser("ibkr-readonly")
    history_parser = subparsers.add_parser("ibkr-history")
    history_parser.add_argument("--duration", default="5 Y")
    history_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    subparsers.add_parser("demo-backtest")
    latest_backtest_parser = subparsers.add_parser("backtest-latest")
    latest_backtest_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    latest_backtest_parser.add_argument("--symbol", required=True)
    latest_backtest_parser.add_argument(
        "--short-window",
        type=int,
        default=20,
    )
    latest_backtest_parser.add_argument(
        "--long-window",
        type=int,
        default=100,
    )
    latest_backtest_parser.add_argument(
        "--target-weight",
        type=Decimal,
        default=Decimal("0.10"),
    )
    walk_forward_parser = subparsers.add_parser("walk-forward")
    walk_forward_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    walk_forward_parser.add_argument("--symbol", required=True)
    walk_forward_parser.add_argument(
        "--target-weight",
        type=Decimal,
        default=Decimal("0.10"),
    )
    universe_parser = subparsers.add_parser("universe-refresh")
    universe_parser.add_argument(
        "--reference-root",
        type=Path,
        default=Path("data/reference"),
    )
    universe_parser.add_argument(
        "--leader-seeds",
        type=Path,
        default=Path("configs/sector_leaders.csv"),
    )
    universe_parser.add_argument(
        "--sec-profiles",
        type=int,
        default=0,
        help="number of new SEC issuer profiles to cache",
    )
    schedule_parser = subparsers.add_parser("history-schedule")
    schedule_parser.add_argument(
        "--universe",
        type=Path,
        default=Path("data/reference/universe.json"),
    )
    schedule_parser.add_argument(
        "--queue",
        type=Path,
        default=Path("runtime/history_jobs.sqlite3"),
    )
    schedule_parser.add_argument("--limit", type=int, default=250)
    history_run_parser = subparsers.add_parser("history-run")
    history_run_parser.add_argument(
        "--queue",
        type=Path,
        default=Path("runtime/history_jobs.sqlite3"),
    )
    history_run_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    history_run_parser.add_argument(
        "--maximum-jobs",
        type=int,
        default=25,
    )
    public_history_parser = subparsers.add_parser(
        "history-run-public"
    )
    public_history_parser.add_argument(
        "--queue",
        type=Path,
        default=Path("runtime/history_jobs.sqlite3"),
    )
    public_history_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    public_history_parser.add_argument(
        "--maximum-jobs",
        type=int,
        default=25,
    )
    scan_parser = subparsers.add_parser("scan-market")
    scan_parser.add_argument(
        "--universe",
        type=Path,
        default=Path("data/reference/universe.json"),
    )
    scan_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    scan_parser.add_argument(
        "--output",
        type=Path,
        default=Path("research/results/market_scan.json"),
    )
    cross_parser = subparsers.add_parser(
        "research-cross-sectional"
    )
    cross_parser.add_argument(
        "--universe",
        type=Path,
        default=Path("data/reference/universe.json"),
    )
    cross_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
    )
    cross_parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "research/results/cross_sectional_research.json"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "doctor":
        return doctor(args.config)
    if args.command == "ibkr-probe":
        return ibkr_probe(args.config)
    if args.command == "ibkr-readonly":
        return ibkr_readonly(args.config)
    if args.command == "ibkr-history":
        return ibkr_history(
            args.config,
            duration=args.duration,
            data_root=args.data_root,
        )
    if args.command == "demo-backtest":
        return demo_backtest(args.config)
    if args.command == "backtest-latest":
        return latest_data_backtest(
            args.config,
            data_root=args.data_root,
            symbol=args.symbol,
            short_window=args.short_window,
            long_window=args.long_window,
            target_weight=args.target_weight,
        )
    if args.command == "walk-forward":
        return walk_forward(
            args.config,
            data_root=args.data_root,
            symbol=args.symbol,
            target_weight=args.target_weight,
        )
    if args.command == "universe-refresh":
        return universe_refresh(
            reference_root=args.reference_root,
            leader_seed_path=args.leader_seeds,
            sec_profiles=args.sec_profiles,
        )
    if args.command == "history-schedule":
        return history_schedule(
            universe_path=args.universe,
            queue_path=args.queue,
            limit=args.limit,
        )
    if args.command == "history-run":
        return history_run(
            args.config,
            queue_path=args.queue,
            data_root=args.data_root,
            maximum_jobs=args.maximum_jobs,
        )
    if args.command == "history-run-public":
        return history_run_public(
            queue_path=args.queue,
            data_root=args.data_root,
            maximum_jobs=args.maximum_jobs,
        )
    if args.command == "scan-market":
        return market_scan(
            args.config,
            universe_path=args.universe,
            data_root=args.data_root,
            output_path=args.output,
        )
    if args.command == "research-cross-sectional":
        return research_cross_sectional(
            args.config,
            universe_path=args.universe,
            data_root=args.data_root,
            output_path=args.output,
        )
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
