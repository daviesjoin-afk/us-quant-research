from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from math import floor, log10, sqrt
from pathlib import Path
import tempfile

from us_quant.market_data import (
    DailyBar,
    load_latest_normalized_series,
)
from us_quant.portfolio import SubstitutionRule
from us_quant.portfolio import IntegerPositionSizer
from us_quant.universe import UniverseRecord, UniverseSnapshot


@dataclass(frozen=True, slots=True)
class ScanResult:
    symbol: str
    execution_symbol: str
    name: str
    sector: str
    leader_tier: int
    security_type: str
    trading_date: date
    close: float
    execution_price: float
    whole_share_capacity: int
    average_dollar_volume_20d: float
    return_20d: float
    return_63d: float
    volatility_20d: float
    drawdown_252d: float
    rsi_14d: float
    atr_pct_14d: float
    above_sma_50: bool
    above_sma_200: bool
    score: float
    signal: str
    research_eligible: bool
    trade_eligible: bool
    reason: str


@dataclass(frozen=True, slots=True)
class MarketScan:
    generated_at: datetime
    capital: float
    data_date: date | None
    results: tuple[ScanResult, ...]
    skipped: dict[str, str]
    max_position_risk_pct: float = 0.10

    def summary(self) -> dict[str, int | float]:
        return {
            "capital": self.capital,
            "scanned": len(self.results),
            "trade_eligible": sum(
                row.trade_eligible for row in self.results
            ),
            "positive_signal": sum(
                row.signal == "趋势候选" for row in self.results
            ),
            "skipped": len(self.skipped),
        }


def scan_market(
    universe: UniverseSnapshot,
    *,
    data_root: str | Path = "data",
    fallback_data_root: str | Path | None = None,
    capital: Decimal = Decimal("1500"),
    max_position_risk_pct: Decimal = Decimal("0.10"),
    substitutions: dict[str, SubstitutionRule] | None = None,
    allow_quality_second_tier: bool = True,
) -> MarketScan:
    if capital <= 0:
        raise ValueError("capital must be positive")
    if not Decimal("0") < max_position_risk_pct <= Decimal("1"):
        raise ValueError("max position risk must be in (0, 1]")
    substitution_rules = substitutions or {}
    all_loaded: dict[str, tuple[DailyBar, ...]] = {}
    skipped: dict[str, str] = {}
    candidates = [
        row for row in universe.records if row.eligible_for_research
    ]
    execution_symbols = {
        substitution_rules.get(row.symbol).execution_symbol
        if row.symbol in substitution_rules
        else row.symbol
        for row in candidates
    }
    for symbol in sorted(
        {row.symbol for row in candidates} | execution_symbols
    ):
        try:
            all_loaded[symbol] = load_latest_normalized_series(
                symbol,
                data_root=data_root,
                fallback_data_root=fallback_data_root,
            ).bars
        except (FileNotFoundError, ValueError) as error:
            skipped[symbol] = str(error)

    raw: list[dict] = []
    for row in candidates:
        bars = all_loaded.get(row.symbol)
        if bars is None:
            continue
        if len(bars) < 200:
            skipped[row.symbol] = "少于 200 根日 K，暂不评分"
            continue
        latest_prices = {
            symbol: bars[-1].close
            for symbol, bars in all_loaded.items()
            if bars
        }
        resolved = IntegerPositionSizer(substitution_rules).resolve(
            signal_symbol=row.symbol,
            target_risk_exposure=(
                capital * max_position_risk_pct
            ),
            prices=latest_prices,
        )
        execution_symbol = (
            resolved.execution_symbol
            if resolved is not None
            else row.symbol
        )
        execution_bars = all_loaded.get(execution_symbol)
        if execution_bars is None:
            skipped[row.symbol] = (
                f"缺少执行标的 {execution_symbol} 的日 K"
            )
            continue
        metrics = _metrics(bars)
        execution_price = execution_bars[-1].close
        raw.append(
            {
                "record": row,
                "execution_symbol": execution_symbol,
                "execution_price": execution_price,
                "whole_share_capacity": (
                    resolved.quantity if resolved is not None else 0
                ),
                **metrics,
            }
        )

    liquidity_ranks = _sector_percentiles(
        raw,
        "average_dollar_volume_20d",
    )
    momentum_ranks = _sector_percentiles(raw, "return_63d")
    results: list[ScanResult] = []
    for item in raw:
        row: UniverseRecord = item["record"]
        liquidity_score = liquidity_ranks[row.symbol] * 18
        momentum_score = momentum_ranks[row.symbol] * 22
        trend_score = (
            (18 if item["above_sma_50"] else 0)
            + (18 if item["above_sma_200"] else 0)
        )
        risk_penalty = min(
            16.0,
            item["volatility_20d"] * 100 * 0.45,
        )
        tier_bonus = {1: 14.0, 2: 7.0, 3: 0.0}.get(
            row.leader_tier, 0.0
        )
        score = max(
            0.0,
            min(
                100.0,
                20
                + liquidity_score
                + momentum_score
                + trend_score
                + tier_bonus
                - risk_penalty,
            ),
        )
        capacity = int(item["whole_share_capacity"])
        tier_ok = row.leader_tier == 1 or (
            allow_quality_second_tier and row.leader_tier == 2
        )
        trade_ok = (
            row.eligible_for_trading
            and tier_ok
            and capacity >= 1
        )
        if not tier_ok:
            reason = "广域研究样本，不属于龙头或优质二线交易池"
        elif capacity < 1:
            reason = "整股资金不足"
        elif not row.eligible_for_trading:
            reason = row.exclusion_reason or "交易资格未通过"
        elif score >= 62 and item["above_sma_50"]:
            reason = "通过中概排除证据、层级、整股与趋势过滤"
        else:
            reason = "基础资格通过，当前信号强度不足"
        signal = (
            "趋势候选"
            if trade_ok and score >= 62 and item["above_sma_50"]
            else "观察"
        )
        results.append(
            ScanResult(
                symbol=row.symbol,
                execution_symbol=item["execution_symbol"],
                name=row.name,
                sector=row.sector,
                leader_tier=row.leader_tier,
                security_type=row.security_type,
                trading_date=item["trading_date"],
                close=float(item["close"]),
                execution_price=float(item["execution_price"]),
                whole_share_capacity=capacity,
                average_dollar_volume_20d=float(
                    item["average_dollar_volume_20d"]
                ),
                return_20d=float(item["return_20d"]),
                return_63d=float(item["return_63d"]),
                volatility_20d=float(item["volatility_20d"]),
                drawdown_252d=float(item["drawdown_252d"]),
                rsi_14d=float(item["rsi_14d"]),
                atr_pct_14d=float(item["atr_pct_14d"]),
                above_sma_50=item["above_sma_50"],
                above_sma_200=item["above_sma_200"],
                score=round(score, 2),
                signal=signal,
                research_eligible=True,
                trade_eligible=trade_ok,
                reason=reason,
            )
        )
    results.sort(
        key=lambda result: (
            result.signal != "趋势候选",
            not result.trade_eligible,
            -result.score,
            result.leader_tier,
            result.symbol,
        )
    )
    data_date = max(
        (row.trading_date for row in results),
        default=None,
    )
    return MarketScan(
        generated_at=datetime.now(timezone.utc),
        capital=float(capital),
        data_date=data_date,
        results=tuple(results),
        skipped=skipped,
        max_position_risk_pct=float(max_position_risk_pct),
    )


def save_market_scan(
    scan: MarketScan,
    path: str | Path = "research/results/market_scan.json",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": scan.generated_at.isoformat(),
        "capital": scan.capital,
        "max_position_risk_pct": scan.max_position_risk_pct,
        "data_date": (
            scan.data_date.isoformat() if scan.data_date else None
        ),
        "summary": scan.summary(),
        "results": [
            {
                **asdict(row),
                "trading_date": row.trading_date.isoformat(),
            }
            for row in scan.results
        ],
        "skipped": scan.skipped,
        "policy": {
            "china_concept": (
                "known China concepts hard-excluded by Nasdaq country, "
                "SEC evidence and local denylist; unmatched broad tail "
                "is labeled denylist_only"
            ),
            "fractional_shares": False,
            "whole_share_capacity_basis": (
                "capital x max_position_risk_pct; leveraged ETF "
                "multiplier included"
            ),
            "historical_price_basis": (
                "adjusted research proxy; not point-in-time executable"
            ),
            "core_universe": "sector leaders",
            "quality_second_tier": "allowed when enabled",
            "broad_tail": "research only, never directly tradable",
            "orders": "disabled",
        },
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)
    return target


def load_close_series(
    symbol: str,
    *,
    data_root: str | Path = "data",
    fallback_data_root: str | Path | None = None,
) -> tuple[tuple[date, float], ...]:
    series = load_latest_normalized_series(
        symbol,
        data_root=data_root,
        fallback_data_root=fallback_data_root,
    )
    return tuple(
        (bar.trading_date, float(bar.close)) for bar in series.bars
    )


def _metrics(bars: tuple[DailyBar, ...]) -> dict:
    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    volumes = [float(bar.volume) for bar in bars]
    returns = [
        current / previous - 1
        for previous, current in zip(closes, closes[1:])
        if previous > 0
    ]
    last = closes[-1]
    sma_50 = sum(closes[-50:]) / 50
    sma_200 = sum(closes[-200:]) / 200
    recent_returns = returns[-20:]
    mean_return = sum(recent_returns) / len(recent_returns)
    variance = sum(
        (value - mean_return) ** 2 for value in recent_returns
    ) / len(recent_returns)
    peak_252 = max(closes[-252:])
    true_ranges = [
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(max(1, len(bars) - 14), len(bars))
    ]
    return {
        "trading_date": bars[-1].trading_date,
        "close": bars[-1].close,
        "average_dollar_volume_20d": sum(
            price * volume
            for price, volume in zip(closes[-20:], volumes[-20:])
        )
        / 20,
        "return_20d": last / closes[-21] - 1,
        "return_63d": last / closes[-64] - 1,
        "volatility_20d": sqrt(variance) * sqrt(252),
        "drawdown_252d": last / peak_252 - 1,
        "rsi_14d": _rsi(closes, 14),
        "atr_pct_14d": sum(true_ranges) / len(true_ranges) / last,
        "above_sma_50": last > sma_50,
        "above_sma_200": last > sma_200,
    }


def _rsi(closes: list[float], period: int) -> float:
    changes = [
        current - previous
        for previous, current in zip(
            closes[-period - 1 :], closes[-period:]
        )
    ]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100 - 100 / (1 + relative_strength)


def _sector_percentiles(
    rows: list[dict],
    field: str,
) -> dict[str, float]:
    by_sector: dict[str, list[dict]] = {}
    for row in rows:
        by_sector.setdefault(row["record"].sector, []).append(row)
    result: dict[str, float] = {}
    for sector_rows in by_sector.values():
        ordered = sorted(
            sector_rows,
            key=lambda item: item[field],
        )
        denominator = max(1, len(ordered) - 1)
        for rank, row in enumerate(ordered):
            result[row["record"].symbol] = rank / denominator
    return result
