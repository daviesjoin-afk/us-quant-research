from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo

from us_quant.ibkr_stream import StreamQuote, StreamSnapshot
from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.shadow_paper import ShadowFill, ShadowPaperEngine, ShadowPaperStore
from us_quant.targeted_intraday import build_targeted_shadow_config


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class TargetedReplayResult:
    run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    parameters: dict[str, object]
    data_hash: str
    first_minute: str
    last_minute: str
    row_count: int
    gap_count: int
    providers: tuple[str, ...]
    coverages: tuple[str, ...]
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    realized_pnl: Decimal
    commission_cost: Decimal
    fills: tuple[ShadowFill, ...]
    status: str


def run_targeted_replay(
    records: tuple[MinuteQuoteRecord, ...],
    *,
    strategy_version_id: str,
    strategy_semver: str,
    parameter_hash: str,
    parameters: dict[str, object],
    initial_equity: Decimal,
) -> TargetedReplayResult:
    if not records:
        raise ValueError("没有可回放的 fresh 分钟 bid/ask")
    symbols = {row.symbol for row in records}
    if len(symbols) != 1:
        raise ValueError("指定标的回放只能包含一个代码")
    providers = {row.provider for row in records}
    if len(providers) != 1:
        raise ValueError("分钟回放禁止跨行情源拼接")
    ordered = tuple(sorted(records, key=lambda row: row.minute))
    session_dates = {
        datetime.fromisoformat(row.minute)
        .astimezone(NEW_YORK)
        .date()
        for row in ordered
    }
    if len(session_dates) != 1:
        raise ValueError("单会话回放禁止跨交易日拼接")
    required = max(
        int(parameters["warmup_minutes"]),
        int(parameters["momentum_lookback_minutes"]) + 1,
    )
    if len(ordered) < required:
        raise ValueError(
            f"可用分钟数据不足：需要至少 {required} 行，当前 {len(ordered)} 行"
        )
    longest_contiguous = _longest_contiguous_run(ordered)
    if longest_contiguous < required:
        raise ValueError(
            "连续分钟数据不足："
            f"需要至少 {required} 分钟，当前最长 {longest_contiguous} 分钟"
        )
    symbol = ordered[0].symbol
    config = build_targeted_shadow_config(
        parameters,
        initial_cash=initial_equity,
        capital_source="本地分钟行情回放研究情景",
        daily_loss_limit=initial_equity * Decimal("0.01"),
    )
    equity_path: list[Decimal] = [initial_equity]
    with tempfile.TemporaryDirectory() as directory:
        engine = ShadowPaperEngine(
            store=ShadowPaperStore(
                Path(directory) / "targeted_replay.sqlite3"
            ),
            allowed_symbols=(symbol,),
            config=config,
            strategy_version_id=strategy_version_id,
            parameter_hash=parameter_hash,
            target_symbol=symbol,
        )
        engine.start()
        for record in ordered:
            observed = datetime.fromisoformat(record.minute)
            quote = StreamQuote(
                symbol=record.symbol,
                request_id=1,
                generation=record.generation,
                requested_market_data_type=(
                    record.market_data_type or 1
                ),
                effective_market_data_type=(
                    record.market_data_type or 1
                ),
                bid=record.bid,
                ask=record.ask,
                last=record.last,
                close=None,
                updated_at=record.minute,
                age_seconds=0,
                stale=False,
                stale_reason=None,
                provider=record.provider,
                coverage=record.coverage,
                bid_size=record.bid_size,
                ask_size=record.ask_size,
            )
            snapshot = StreamSnapshot(
                generation=record.generation,
                socket_connected=True,
                handshake_complete=True,
                reconnect_attempt=0,
                quotes=(quote,),
                last_error_code=None,
                last_message="minute replay",
                observed_at=record.minute,
                provider=record.provider,
                coverage=record.coverage,
            )
            state = engine.on_stream(snapshot, observed_at=observed)
            equity_path.append(state.equity)
        final_state = engine.stop(
            observed_at=(
                datetime.fromisoformat(ordered[-1].minute)
                + timedelta(minutes=1)
            )
        )
        equity_path.append(final_state.equity)
    maximum_drawdown = _maximum_drawdown(equity_path)
    gaps = sum(
        (
            datetime.fromisoformat(current.minute)
            - datetime.fromisoformat(previous.minute)
        ).total_seconds()
        > 60
        for previous, current in zip(ordered, ordered[1:])
    )
    commission_cost = sum(
        (fill.commission for fill in final_state.fills),
        Decimal("0"),
    )
    return TargetedReplayResult(
        run_id=uuid4().hex,
        symbol=symbol,
        strategy_version_id=strategy_version_id,
        strategy_semver=strategy_semver,
        parameter_hash=parameter_hash,
        parameters=dict(parameters),
        data_hash=MinuteQuoteStore.fingerprint(ordered),
        first_minute=ordered[0].minute,
        last_minute=ordered[-1].minute,
        row_count=len(ordered),
        gap_count=gaps,
        providers=tuple(sorted(providers)),
        coverages=tuple(sorted({row.coverage for row in ordered})),
        initial_equity=initial_equity,
        final_equity=final_state.equity,
        total_return=final_state.equity / initial_equity - Decimal("1"),
        maximum_drawdown=maximum_drawdown,
        realized_pnl=final_state.realized_pnl,
        commission_cost=commission_cost,
        fills=final_state.fills,
        status="research_replay",
    )


def save_targeted_replay(
    result: TargetedReplayResult,
    output_root: str | Path,
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.run_id}.json"
    payload = {
        **asdict(result),
        "initial_equity": str(result.initial_equity),
        "final_equity": str(result.final_equity),
        "total_return": str(result.total_return),
        "maximum_drawdown": str(result.maximum_drawdown),
        "realized_pnl": str(result.realized_pnl),
        "commission_cost": str(result.commission_cost),
        "fills": [
            {
                **asdict(fill),
                "price": str(fill.price),
                "commission": str(fill.commission),
                "realized_pnl": (
                    str(fill.realized_pnl)
                    if fill.realized_pnl is not None
                    else None
                ),
            }
            for fill in result.fills
        ],
        "orders_submitted": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{result.run_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def load_targeted_replays(
    output_root: str | Path,
    *,
    limit: int = 100,
) -> tuple[TargetedReplayResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedReplayResult] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fills = tuple(
                ShadowFill(
                    session_id=str(row["session_id"]),
                    occurred_at=str(row["occurred_at"]),
                    symbol=str(row["symbol"]),
                    side=str(row["side"]),
                    quantity=int(row["quantity"]),
                    price=Decimal(str(row["price"])),
                    commission=Decimal(str(row["commission"])),
                    reason=str(row["reason"]),
                    provider=str(row["provider"]),
                    coverage=str(row["coverage"]),
                    realized_pnl=(
                        Decimal(str(row["realized_pnl"]))
                        if row.get("realized_pnl") is not None
                        else None
                    ),
                )
                for row in payload.get("fills", ())
            )
            results.append(
                TargetedReplayResult(
                    run_id=str(payload["run_id"]),
                    symbol=str(payload["symbol"]),
                    strategy_version_id=str(
                        payload["strategy_version_id"]
                    ),
                    strategy_semver=str(
                        payload.get("strategy_semver", "legacy")
                    ),
                    parameter_hash=str(payload["parameter_hash"]),
                    parameters=dict(payload.get("parameters", {})),
                    data_hash=str(payload["data_hash"]),
                    first_minute=str(payload["first_minute"]),
                    last_minute=str(payload["last_minute"]),
                    row_count=int(payload["row_count"]),
                    gap_count=int(payload["gap_count"]),
                    providers=tuple(payload.get("providers", ())),
                    coverages=tuple(payload.get("coverages", ())),
                    initial_equity=Decimal(
                        str(payload["initial_equity"])
                    ),
                    final_equity=Decimal(str(payload["final_equity"])),
                    total_return=Decimal(str(payload["total_return"])),
                    maximum_drawdown=Decimal(
                        str(payload["maximum_drawdown"])
                    ),
                    realized_pnl=Decimal(
                        str(payload["realized_pnl"])
                    ),
                    commission_cost=Decimal(
                        str(payload["commission_cost"])
                    ),
                    fills=fills,
                    status=str(payload["status"]),
                )
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            continue
    return tuple(results)


def _maximum_drawdown(equity: list[Decimal]) -> Decimal:
    peak = equity[0]
    drawdown = Decimal("0")
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _longest_contiguous_run(
    records: tuple[MinuteQuoteRecord, ...],
) -> int:
    longest = 0
    current = 0
    previous: datetime | None = None
    for row in records:
        observed = datetime.fromisoformat(row.minute)
        if previous is None or (observed - previous).total_seconds() == 60:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = observed
    return longest
