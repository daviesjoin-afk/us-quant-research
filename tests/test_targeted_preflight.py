from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import unittest

from us_quant.ibkr_stream import StreamQuote
from us_quant.minute_data import MinuteDataSummary
from us_quant.portfolio_view import AccountView
from us_quant.strategy_registry import StrategyRecord
from us_quant.targeted_preflight import evaluate_target_preflight
from us_quant.universe import UniverseRecord


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class TargetedPreflightTests(unittest.TestCase):
    def test_complete_inputs_are_exploratory_shadow_ready(self) -> None:
        result = evaluate_target_preflight(
            "aapl",
            universe_record=_universe(),
            quote=_quote(),
            account=_account(),
            minute_summary=_summary(50, 50),
            strategy=_strategy(),
            now=NOW,
        )
        self.assertTrue(result.shadow_ready)
        self.assertEqual(
            result.decision, "EXPLORATORY_SHADOW_READY"
        )
        self.assertEqual(result.estimated_whole_shares, 4)
        self.assertFalse(result.broker_orders_available)
        self.assertFalse(result.orders_submitted)

    def test_stale_account_and_quote_block_shadow(self) -> None:
        stale_quote = replace(
            _quote(),
            stale=True,
            stale_reason="test",
        )
        stale_account = _account(
            observed_at=(
                NOW - timedelta(minutes=6)
            ).isoformat()
        )
        result = evaluate_target_preflight(
            "AAPL",
            universe_record=_universe(),
            quote=stale_quote,
            account=stale_account,
            minute_summary=_summary(50, 50),
            strategy=_strategy(),
            now=NOW,
        )
        self.assertFalse(result.shadow_ready)
        failed = {gate.code for gate in result.gates if not gate.passed}
        self.assertIn("realtime_quote", failed)
        self.assertIn("paper_account_truth", failed)

    def test_unknown_or_china_target_is_blocked(self) -> None:
        unknown = evaluate_target_preflight(
            "TEST",
            universe_record=None,
            quote=None,
            account=_account(),
            minute_summary=_summary(0, 0),
            strategy=_strategy(),
            now=NOW,
        )
        self.assertFalse(unknown.shadow_ready)
        china = _universe(
            country_status="中概排除",
            eligible_for_research=False,
        )
        excluded = evaluate_target_preflight(
            "BABA",
            universe_record=china,
            quote=_quote(symbol="BABA"),
            account=_account(),
            minute_summary=_summary(50, 50, symbol="BABA"),
            strategy=_strategy(),
            now=NOW,
        )
        self.assertFalse(excluded.shadow_ready)
        gate = next(
            row
            for row in excluded.gates
            if row.code == "non_china_eligible"
        )
        self.assertFalse(gate.passed)

    def test_minute_evidence_is_visible_but_not_a_startup_deadlock(
        self,
    ) -> None:
        result = evaluate_target_preflight(
            "AAPL",
            universe_record=_universe(),
            quote=_quote(),
            account=_account(),
            minute_summary=_summary(0, 0),
            strategy=_strategy(),
            now=NOW,
        )
        evidence = next(
            row
            for row in result.gates
            if row.code == "minute_evidence"
        )
        self.assertFalse(evidence.passed)
        self.assertFalse(evidence.blocking)
        self.assertTrue(result.shadow_ready)


def _universe(**changes) -> UniverseRecord:
    values = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "security_type": "STK",
        "sector": "信息技术",
        "leader_tier": 1,
        "country_status": "美国注册",
        "country_evidence_level": "verified_non_china",
        "eligible_for_research": True,
        "eligible_for_trading": True,
        "exclusion_reason": "",
    }
    values.update(changes)
    return UniverseRecord(**values)


def _quote(symbol: str = "AAPL") -> StreamQuote:
    return StreamQuote(
        symbol=symbol,
        request_id=1,
        generation=1,
        requested_market_data_type=1,
        effective_market_data_type=1,
        bid=Decimal("199.90"),
        ask=Decimal("200"),
        last=Decimal("199.95"),
        close=None,
        updated_at=NOW.isoformat(),
        age_seconds=1,
        stale=False,
        stale_reason=None,
        provider="TestFeed",
        coverage="unit test",
    )


def _account(observed_at: str | None = None) -> AccountView:
    return AccountView(
        environment="paper",
        account_alias="DU***123",
        net_liquidation=Decimal("10000"),
        cash=Decimal("10000"),
        available_funds=Decimal("10000"),
        buying_power=Decimal("10000"),
        gross_position_value=Decimal("0"),
        excess_liquidity=Decimal("10000"),
        maintenance_margin=Decimal("0"),
        cushion=Decimal("1"),
        daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        observed_at=observed_at or NOW.isoformat(),
        pnl_source="IBKR reqPnL",
    )


def _strategy() -> StrategyRecord:
    return StrategyRecord(
        strategy_id="intraday-targeted-t",
        name="指定标的日内 T",
        description="test",
        version_id="version-1",
        semver="1.0.0-research",
        status="research",
        mode="research",
        parameters={
            "max_position_fraction": "0.10",
            "commission_per_order": "0.35",
            "slippage_bps": "2",
            "warmup_minutes": 10,
        },
        parameter_hash="p",
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=0.10,
        gate_passed=False,
        gate_reason="research",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
    )


def _summary(
    usable: int,
    total: int,
    *,
    symbol: str = "AAPL",
) -> MinuteDataSummary:
    return MinuteDataSummary(
        symbol=symbol,
        total_rows=total,
        usable_rows=usable,
        first_minute=None,
        last_minute=None,
        providers=(),
        evidence_origins=(),
    )


if __name__ == "__main__":
    unittest.main()
