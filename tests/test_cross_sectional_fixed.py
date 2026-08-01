from datetime import date, timedelta
from decimal import Decimal

import pytest

from us_quant.cross_sectional import (
    DEFAULT_CANDIDATES,
    FIXED_DAILY_CANDIDATE,
    CrossSectionalCandidate,
    ResearchMode,
    _rank_signals,
    resolve_research_candidates,
)
from us_quant.market_data import DailyBar
from us_quant.universe import UniverseRecord


def _record(symbol: str, sector: str) -> UniverseRecord:
    return UniverseRecord(
        symbol=symbol,
        name=symbol,
        exchange="NASDAQ",
        security_type="STK",
        sector=sector,
        leader_tier=1,
        country_status="verified_us",
        eligible_for_research=True,
        eligible_for_trading=True,
        exclusion_reason="",
    )


def _bars(symbol: str, *, final_close: Decimal) -> tuple[DailyBar, ...]:
    first = date(2024, 1, 2)
    result = []
    for index in range(202):
        close = (
            final_close if index == 201 else Decimal("100") + Decimal(index)
        )
        result.append(
            DailyBar(
                symbol=symbol,
                trading_date=first + timedelta(days=index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1000000"),
                average=close,
                bar_count=1,
            )
        )
    return tuple(result)


def test_fixed_mode_resolves_only_the_declared_daily_hypothesis() -> None:
    assert FIXED_DAILY_CANDIDATE == CrossSectionalCandidate(126, 21, 3)
    assert resolve_research_candidates(ResearchMode.FIXED_DAILY) == (
        FIXED_DAILY_CANDIDATE,
    )
    assert resolve_research_candidates(
        ResearchMode.EXPLORATORY_GRID
    ) == DEFAULT_CANDIDATES

    with pytest.raises(ValueError, match="must not be empty"):
        resolve_research_candidates(
            ResearchMode.EXPLORATORY_GRID,
            exploratory_candidates=(),
        )


def test_date_t_close_does_not_change_date_t_rank() -> None:
    candidate = CrossSectionalCandidate(126, 21, 3)
    records = {
        "AAA": _record("AAA", "Technology"),
        "BBB": _record("BBB", "Healthcare"),
    }
    baseline = {
        "AAA": _bars("AAA", final_close=Decimal("301")),
        "BBB": _bars("BBB", final_close=Decimal("1")),
    }
    modified = {
        "AAA": _bars("AAA", final_close=Decimal("1")),
        "BBB": _bars("BBB", final_close=Decimal("10000")),
    }
    trading_date = baseline["AAA"][-1].trading_date

    def rank(data: dict[str, tuple[DailyBar, ...]]) -> list[tuple[str, float]]:
        indexed = {
            symbol: {
                bar.trading_date: (index, bar)
                for index, bar in enumerate(bars)
            }
            for symbol, bars in data.items()
        }
        return _rank_signals(
            trading_date,
            candidate,
            data=data,
            records=records,
            indexed=indexed,
        )

    assert rank(modified) == rank(baseline)
