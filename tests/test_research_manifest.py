from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

from us_quant.config import load_config
from us_quant.cross_sectional import (
    CrossSectionalCandidate,
    ResearchMode,
    run_cross_sectional_research,
)
from us_quant.executable_research import (
    run_executable_cross_sectional_research,
)
from us_quant.market_data import DailyBar, LoadedDailySeries
from us_quant.research_manifest import (
    build_research_run_manifest,
    canonical_universe_sha256,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot


def _record(symbol: str) -> UniverseRecord:
    return UniverseRecord(
        symbol=symbol,
        name=symbol,
        exchange="NASDAQ",
        security_type="STK",
        sector="Technology",
        leader_tier=1,
        country_status="verified_us",
        eligible_for_research=True,
        eligible_for_trading=True,
        exclusion_reason="",
    )


def _series(symbol: str) -> LoadedDailySeries:
    bar = DailyBar(
        symbol=symbol,
        trading_date=date(2025, 1, 2),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("100"),
        average=Decimal("10"),
        bar_count=1,
    )
    return LoadedDailySeries(
        symbol=symbol,
        source_sha256=f"{symbol.lower()}-sha",
        path=Path("data") / symbol / "daily.json",
        bars=(bar,),
        source="IBKR",
        price_basis="raw_trade_price",
        point_in_time_membership=True,
    )


def test_manifest_is_json_serializable_and_records_exact_inputs() -> None:
    universe = UniverseSnapshot(
        generated_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        source_timestamps={"sec": "2025-02-01T00:00:00+00:00"},
        records=(_record("BBB"), _record("AAA")),
    )
    manifest = build_research_run_manifest(
        universe,
        {"BBB": _record("BBB"), "AAA": _record("AAA")},
        {"SPY": _series("SPY"), "AAA": _series("AAA")},
    )

    payload = manifest.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["schema_version"] == 1
    assert payload["policy_mode"] == "exploratory"
    assert payload["universe"]["generated_at"] == "2025-02-01T00:00:00+00:00"
    assert payload["selected_symbols"] == ["AAA", "BBB"]
    assert [row["symbol"] for row in payload["loaded_artifacts"]] == [
        "AAA",
        "SPY",
    ]
    assert payload["loaded_artifacts"][0] == {
        "symbol": "AAA",
        "source_sha256": "aaa-sha",
        "path": "data\\AAA\\daily.json",
        "source": "IBKR",
        "price_basis": "raw_trade_price",
        "point_in_time_membership": True,
        "rows": 1,
        "first_date": "2025-01-02",
        "last_date": "2025-01-02",
    }


def test_universe_hash_is_canonical_across_record_order() -> None:
    common = {
        "generated_at": datetime(2025, 2, 1, tzinfo=timezone.utc),
        "source_timestamps": {"b": "2", "a": "1"},
    }
    first = UniverseSnapshot(records=(_record("BBB"), _record("AAA")), **common)
    second = UniverseSnapshot(records=(_record("AAA"), _record("BBB")), **common)

    assert canonical_universe_sha256(first) == canonical_universe_sha256(second)


def test_both_research_results_include_the_loaded_input_manifest() -> None:
    universe = UniverseSnapshot(
        generated_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        source_timestamps={},
        records=(_record("AAA"),),
    )
    # Each series needs its own symbol while sharing the same deterministic
    # calendar.  Keeping this fixture local prevents any disk/archive access.
    def make_bars(symbol: str) -> tuple[DailyBar, ...]:
        return tuple(
            DailyBar(
                symbol=symbol,
                trading_date=date.fromordinal(date(2024, 1, 1).toordinal() + index),
                open=Decimal("100") + Decimal(index),
                high=Decimal("101") + Decimal(index),
                low=Decimal("99") + Decimal(index),
                close=Decimal("100.5") + Decimal(index),
                volume=Decimal("1000000"),
                average=Decimal("100") + Decimal(index),
                bar_count=1,
            )
            for index in range(235)
        )

    loaded = {
        symbol: LoadedDailySeries(
            symbol=symbol,
            source_sha256=f"{symbol.lower()}-sha",
            path=Path("data") / f"{symbol}.json",
            bars=make_bars(symbol),
            source="IBKR",
            price_basis="raw_trade_price",
            point_in_time_membership=True,
        )
        for symbol in ("SPY", "AAA")
    }
    payload = (
        {symbol: item.bars for symbol, item in loaded.items()},
        {"AAA": _record("AAA")},
        loaded,
    )
    config = load_config("configs/paper.toml")
    candidate = (CrossSectionalCandidate(63, 5, 1),)

    with patch("us_quant.cross_sectional._load_data", return_value=payload):
        classic = run_cross_sectional_research(
            config,
            universe,
            mode=ResearchMode.EXPLORATORY_GRID,
            candidates=candidate,
            minimum_train_days=210,
            test_days=20,
        )
    with patch("us_quant.executable_research._load_data", return_value=payload):
        executable = run_executable_cross_sectional_research(
            config,
            universe,
            data_root=Path("unused"),
            mode=ResearchMode.EXPLORATORY_GRID,
            candidates=candidate,
            minimum_train_days=210,
            test_days=20,
        )

    for result in (classic, executable):
        assert result["run_manifest"]["schema_version"] == 1
        assert result["run_manifest"]["selected_symbols"] == ["AAA"]
        assert result["run_manifest"]["policy_mode"] == "exploratory"
