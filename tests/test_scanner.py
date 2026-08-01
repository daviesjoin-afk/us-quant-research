from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.market_data import (
    DailyBar,
    HistoricalRequest,
    HistoricalSeries,
    save_historical_series,
)
from us_quant.portfolio import SubstitutionRule
from us_quant.scanner import scan_market
from us_quant.universe import UniverseRecord, UniverseSnapshot


def save_flat_series(root: Path, symbol: str, price: str) -> None:
    bars = tuple(
        DailyBar(
            symbol=symbol,
            trading_date=date(2024, 1, 1) + timedelta(days=index),
            open=Decimal(price),
            high=Decimal(price) * Decimal("1.01"),
            low=Decimal(price) * Decimal("0.99"),
            close=Decimal(price),
            volume=Decimal("1000000"),
            average=Decimal("0"),
            bar_count=0,
        )
        for index in range(220)
    )
    save_historical_series(
        HistoricalSeries(
            source="test",
            server_version=0,
            fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            request=HistoricalRequest(
                symbol=symbol,
                end_datetime="2025-01-01",
                duration="1 Y",
            ),
            returned_start=bars[0].trading_date.isoformat(),
            returned_end=bars[-1].trading_date.isoformat(),
            bars=bars,
        ),
        data_root=root,
    )


class ScannerTests(unittest.TestCase):
    def test_capacity_uses_position_risk_and_muu_multiplier(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_flat_series(root, "MU", "900")
            save_flat_series(root, "MUU", "35")
            universe = UniverseSnapshot(
                generated_at=datetime.now(timezone.utc),
                source_timestamps={},
                records=(
                    UniverseRecord(
                        symbol="MU",
                        name="Micron",
                        exchange="NASDAQ",
                        security_type="STK",
                        sector="信息技术",
                        leader_tier=1,
                        country_status="美国注册",
                        country_evidence_level="verified_non_china",
                        eligible_for_research=True,
                        eligible_for_trading=True,
                        exclusion_reason="",
                    ),
                ),
            )
            result = scan_market(
                universe,
                data_root=root,
                capital=Decimal("1500"),
                max_position_risk_pct=Decimal("0.10"),
                substitutions={
                    "MU": SubstitutionRule(
                        source_symbol="MU",
                        execution_symbol="MUU",
                        exposure_multiplier=Decimal("2"),
                        holding_mode="short_term_only",
                    )
                },
            )
            row = result.results[0]
            self.assertEqual(row.execution_symbol, "MUU")
            self.assertEqual(row.whole_share_capacity, 2)
            self.assertTrue(row.trade_eligible)
