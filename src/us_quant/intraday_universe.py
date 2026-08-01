from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Mapping

from us_quant.scanner import MarketScan, ScanResult
from us_quant.universe import UniverseSnapshot


def select_paper_rotation_rows(
    scan: MarketScan,
    universe: UniverseSnapshot,
    *,
    capital: Decimal,
    max_position_fraction: Decimal,
    limit: int = 20,
    maximum_per_sector: int = 3,
    risk_multipliers: Mapping[str, Decimal] | None = None,
) -> tuple[ScanResult, ...]:
    """Select a diversified Paper shortlist from the full scan.

    Scan-level ``trade_eligible`` may reflect a small live-research budget.
    Paper selection intentionally recomputes whole-share affordability from
    the current simulated account while retaining the official country and
    leader-tier gates from the universe snapshot.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if not Decimal("0") < max_position_fraction <= Decimal("1"):
        raise ValueError("max_position_fraction must be in (0, 1]")
    if not 3 <= limit <= 30:
        raise ValueError("limit must be between 3 and 30")
    if maximum_per_sector <= 0:
        raise ValueError("maximum_per_sector must be positive")
    records = {row.symbol: row for row in universe.records}
    multipliers = risk_multipliers or {}
    ranked = sorted(
        scan.results,
        key=lambda row: (
            row.signal != "趋势候选",
            -row.score,
            -row.average_dollar_volume_20d,
            row.execution_symbol,
        ),
    )
    selected: list[ScanResult] = []
    selected_symbols: set[str] = set()
    sector_counts: Counter[str] = Counter()
    for row in ranked:
        record = records.get(row.symbol)
        if (
            record is None
            or not record.eligible_for_trading
            or record.leader_tier not in {1, 2}
        ):
            continue
        execution_symbol = row.execution_symbol.strip().upper()
        multiplier = multipliers.get(execution_symbol, Decimal("1"))
        executable_cap = (
            capital * max_position_fraction / multiplier
        )
        if Decimal(str(row.execution_price)) > executable_cap:
            continue
        if execution_symbol in selected_symbols:
            continue
        if sector_counts[row.sector] >= maximum_per_sector:
            continue
        selected.append(row)
        selected_symbols.add(execution_symbol)
        sector_counts[row.sector] += 1
        if len(selected) >= limit:
            break
    return tuple(selected)


def select_intraday_watchlist(
    scan: MarketScan,
    *,
    capital: Decimal = Decimal("1500"),
    max_position_fraction: Decimal = Decimal("0.10"),
    limit: int = 30,
    maximum_per_sector: int = 3,
    reference_symbols: tuple[str, ...] = ("SPY", "QQQ"),
    risk_multipliers: Mapping[str, Decimal] | None = None,
) -> tuple[str, ...]:
    """Build a diverse, whole-share executable Level-I watchlist.

    The scan is already fail-closed for country and leader tier. This
    selector adds the small-account position cap and sector concentration
    cap. Reference ETFs may be observed even when they are unaffordable,
    but the shadow engine independently refuses unaffordable orders.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if maximum_per_sector <= 0:
        raise ValueError("maximum_per_sector must be positive")
    position_cap = capital * max_position_fraction
    ranked = sorted(
        (
            row
            for row in scan.results
            if row.trade_eligible and row.leader_tier in {1, 2}
        ),
        key=lambda row: (
            row.signal != "趋势候选",
            -row.score,
            -row.average_dollar_volume_20d,
            row.symbol,
        ),
    )
    selected: list[str] = []
    sector_counts: Counter[str] = Counter()
    multipliers = risk_multipliers or {}
    for row in ranked:
        execution_symbol = row.execution_symbol
        risk_multiplier = multipliers.get(
            execution_symbol, Decimal("1")
        )
        executable_cap = position_cap / risk_multiplier
        if Decimal(str(row.execution_price)) > executable_cap:
            continue
        if execution_symbol in selected:
            continue
        if sector_counts[row.sector] >= maximum_per_sector:
            continue
        selected.append(execution_symbol)
        sector_counts[row.sector] += 1
        if len(selected) >= limit:
            return tuple(selected)

    known = {row.symbol: row for row in scan.results}
    for symbol in reference_symbols:
        row = known.get(symbol)
        if (
            row is not None
            and row.trade_eligible
            and symbol not in selected
            and len(selected) < limit
        ):
            selected.append(symbol)
    return tuple(selected)
