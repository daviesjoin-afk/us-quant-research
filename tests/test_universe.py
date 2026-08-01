from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.universe import (
    UniverseRefreshCancelled,
    UniverseRecord,
    UniverseSnapshot,
    _apply_sec_profile,
    _download,
    _parse_nasdaq_country_screener,
    enrich_us_profiles,
    load_china_concept_denylist,
    load_leader_seeds,
    prioritized_research_symbols,
    sector_from_sic,
)
from datetime import datetime, timezone
from unittest.mock import patch


class UniverseTests(unittest.TestCase):
    def test_profile_refresh_stops_before_writing_a_partial_snapshot(self) -> None:
        snapshot = UniverseSnapshot(
            generated_at=datetime.now(timezone.utc),
            source_timestamps={},
            records=(
                UniverseRecord(
                    symbol="STOP",
                    name="Stop Inc",
                    exchange="NYSE",
                    security_type="STK",
                    cik=1,
                ),
            ),
        )
        with TemporaryDirectory() as directory:
            with self.assertRaises(UniverseRefreshCancelled):
                enrich_us_profiles(
                    snapshot,
                    cache_root=Path(directory) / "sec_profiles",
                    should_stop=lambda: True,
                )
            self.assertFalse(
                (Path(directory) / "universe.json").exists()
            )

    def test_single_request_download_uses_the_requested_timeout(self) -> None:
        with patch("us_quant.universe.urlopen", side_effect=OSError("offline")) as mocked:
            with self.assertRaises(OSError):
                _download(
                    "https://data.sec.gov/submissions/CIK0000000001.json",
                    user_agent="test",
                    timeout_seconds=8,
                    max_attempts=1,
                )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 8)

    def test_us_profile_can_enter_broad_research_but_not_trade(self) -> None:
        row = UniverseRecord(
            symbol="TEST",
            name="Test Inc",
            exchange="NASDAQ",
            security_type="STK",
            cik=1,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "DE",
                "addresses": {
                    "business": {"stateOrCountry": "CA"}
                },
                "filings": {"recent": {"form": ["10-K"]}},
                "sic": "3674",
                "sicDescription": "Semiconductors",
            },
        )
        self.assertTrue(enriched.eligible_for_research)
        self.assertFalse(enriched.eligible_for_trading)
        self.assertEqual(enriched.leader_tier, 3)
        self.assertEqual(enriched.sector, "信息技术")

    def test_non_us_profile_is_fail_closed(self) -> None:
        row = UniverseRecord(
            symbol="FOREIGN",
            name="Foreign Inc",
            exchange="NASDAQ",
            security_type="STK",
            cik=2,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "E9",
                "addresses": {
                    "business": {"stateOrCountry": "E9"}
                },
                "filings": {"recent": {"form": ["20-F"]}},
                "sic": "7372",
                "sicDescription": "Software",
            },
        )
        self.assertFalse(enriched.eligible_for_research)
        self.assertFalse(enriched.eligible_for_trading)
        self.assertIn("中国大陆", enriched.exclusion_reason)

    def test_us_shell_with_foreign_business_is_fail_closed(self) -> None:
        row = UniverseRecord(
            symbol="SHELL",
            name="Shell Inc",
            exchange="NASDAQ",
            security_type="STK",
            cik=3,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "NV",
                "addresses": {
                    "business": {"stateOrCountry": "E9"}
                },
                "filings": {"recent": {"form": ["10-K"]}},
                "sic": "7372",
            },
        )
        self.assertFalse(enriched.eligible_for_research)

    def test_us_leader_seed_cannot_bypass_foreign_sec_evidence(self) -> None:
        row = UniverseRecord(
            symbol="SEEDED",
            name="Seeded Shell Inc",
            exchange="NASDAQ",
            security_type="STK",
            leader_tier=1,
            country_status="美国注册",
            eligible_for_research=True,
            eligible_for_trading=True,
            cik=4,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "NV",
                "addresses": {
                    "business": {"stateOrCountry": "E9"}
                },
                "filings": {"recent": {"form": ["20-F"]}},
                "sic": "7372",
            },
        )
        self.assertFalse(enriched.eligible_for_research)
        self.assertFalse(enriched.eligible_for_trading)

    def test_new_domestic_leader_with_us_addresses_can_pass(self) -> None:
        row = UniverseRecord(
            symbol="NEWUS",
            name="New US Holding Inc",
            exchange="NYSE",
            security_type="STK",
            leader_tier=1,
            country_status="美国注册",
            eligible_for_research=True,
            eligible_for_trading=True,
            cik=5,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "TX",
                "addresses": {
                    "business": {"stateOrCountry": "TX"}
                },
                "filings": {"recent": {"form": ["8-K"]}},
                "sic": "2911",
            },
        )
        self.assertTrue(enriched.eligible_for_trading)

    def test_sector_mapping(self) -> None:
        self.assertEqual(sector_from_sic("6022"), "金融")
        self.assertEqual(sector_from_sic("2834"), "医疗保健")
        self.assertEqual(sector_from_sic("7372"), "信息技术")

    def test_non_china_foreign_profile_enters_research_only(self) -> None:
        row = UniverseRecord(
            symbol="JAPAN",
            name="Japan Leader",
            exchange="NYSE",
            security_type="STK",
            cik=6,
        )
        enriched = _apply_sec_profile(
            row,
            {
                "stateOfIncorporation": "M0",
                "stateOfIncorporationDescription": "Japan",
                "addresses": {
                    "business": {
                        "stateOrCountry": "M0",
                        "stateOrCountryDescription": "Japan",
                    }
                },
                "filings": {"recent": {"form": ["20-F"]}},
                "sic": "3711",
            },
        )
        self.assertTrue(enriched.eligible_for_research)
        self.assertFalse(enriched.eligible_for_trading)
        self.assertIn("Japan", enriched.country_status)

    def test_nasdaq_china_screener_symbols_are_parsed(self) -> None:
        payload = (
            b'{"data":{"table":{"rows":['
            b'{"symbol":"BABA"},{"symbol":"BILI"}]}}}'
        )
        self.assertEqual(
            _parse_nasdaq_country_screener(payload),
            {"BABA", "BILI"},
        )

    def test_local_china_denylist_contains_user_examples(self) -> None:
        path = (
            Path(__file__).parents[1]
            / "configs"
            / "china_concept_denylist.csv"
        )
        denied = load_china_concept_denylist(path)
        self.assertIn("BABA", denied)
        self.assertIn("BILI", denied)

    def test_leader_seed_is_utf8_without_special_execution_symbol(self) -> None:
        path = (
            Path(__file__).parents[1]
            / "configs"
            / "sector_leaders.csv"
        )
        seeds = load_leader_seeds(path)
        self.assertNotIn("MUU", seeds)

    def test_unbounded_history_scope_keeps_all_research_eligible(self) -> None:
        snapshot = UniverseSnapshot(
            generated_at=datetime.now(timezone.utc),
            source_timestamps={},
            records=(
                UniverseRecord(
                    symbol="TAIL",
                    name="Broad Research",
                    exchange="NYSE",
                    security_type="STK",
                    eligible_for_research=True,
                    leader_tier=3,
                    sec_source_rank=30,
                ),
                UniverseRecord(
                    symbol="LEAD",
                    name="Leader",
                    exchange="NYSE",
                    security_type="STK",
                    eligible_for_research=True,
                    eligible_for_trading=True,
                    leader_tier=1,
                    sec_source_rank=20,
                ),
                UniverseRecord(
                    symbol="SECOND",
                    name="Second Tier",
                    exchange="NASDAQ",
                    security_type="STK",
                    eligible_for_research=True,
                    eligible_for_trading=True,
                    leader_tier=2,
                    sec_source_rank=10,
                ),
                UniverseRecord(
                    symbol="BLOCKED",
                    name="Excluded",
                    exchange="NASDAQ",
                    security_type="STK",
                    eligible_for_research=False,
                ),
            ),
        )
        self.assertEqual(
            prioritized_research_symbols(snapshot, limit=None),
            ("LEAD", "SECOND", "TAIL"),
        )


if __name__ == "__main__":
    unittest.main()
