from datetime import datetime, timezone
from decimal import Decimal
from typing import get_type_hints
import unittest

from us_quant.domain import AccountSnapshot, OrderIntent, Position, Side
from us_quant.risk import (
    PreTradeRiskEngine,
    RiskLimits,
    SessionRiskOverrides,
)


class PreTradeRiskEngineTests(unittest.TestCase):
    def test_session_override_type_hints_resolve(self) -> None:
        hints = get_type_hints(SessionRiskOverrides)
        self.assertIn("entry_start", hints)

    def setUp(self) -> None:
        self.engine = PreTradeRiskEngine(
            RiskLimits(
                max_gross_exposure_pct=Decimal("0.10"),
                max_position_exposure_pct=Decimal("0.10"),
                daily_loss_halt_pct=Decimal("0.02"),
                drawdown_halt_pct=Decimal("0.08"),
                allow_margin_borrowing=False,
            ),
            allowed_symbols={"MUU"},
        )
        self.account = AccountSnapshot(
            net_liquidation=Decimal("1500"),
            cash=Decimal("1500"),
            day_start_equity=Decimal("1500"),
            high_watermark=Decimal("1500"),
            timestamp=datetime.now(timezone.utc),
        )

    def _intent(self, quantity: int) -> OrderIntent:
        return OrderIntent.create(
            signal_symbol="MU",
            execution_symbol="MUU",
            side=Side.BUY,
            quantity=quantity,
            estimated_price=Decimal("36"),
            exposure_multiplier=Decimal("2"),
        )

    def test_approves_two_muu_shares_inside_ten_percent_risk_cap(self) -> None:
        decision = self.engine.evaluate(
            intent=self._intent(2),
            account=self.account,
            positions={},
            market_prices={"MUU": Decimal("36")},
            estimated_commission=Decimal("0.35"),
        )
        self.assertTrue(decision.approved, decision.reasons)

    def test_rejects_three_muu_shares_over_risk_cap(self) -> None:
        decision = self.engine.evaluate(
            intent=self._intent(3),
            account=self.account,
            positions={},
            market_prices={"MUU": Decimal("36")},
        )
        self.assertFalse(decision.approved)
        self.assertIn(
            "gross exposure limit exceeded",
            decision.reasons,
        )

    def test_daily_account_loss_halts_new_buys(self) -> None:
        losing_account = AccountSnapshot(
            net_liquidation=Decimal("1469"),
            cash=Decimal("1469"),
            day_start_equity=Decimal("1500"),
            high_watermark=Decimal("1500"),
        )
        decision = self.engine.evaluate(
            intent=self._intent(1),
            account=losing_account,
            positions={},
            market_prices={"MUU": Decimal("36")},
        )
        self.assertFalse(decision.approved)
        self.assertIn("daily account loss halt is active", decision.reasons)

    def test_daily_loss_halt_still_allows_risk_reducing_sell(self) -> None:
        losing_account = AccountSnapshot(
            net_liquidation=Decimal("1469"),
            cash=Decimal("1397"),
            day_start_equity=Decimal("1500"),
            high_watermark=Decimal("1500"),
        )
        positions = {
            "MUU": Position(
                symbol="MUU",
                quantity=2,
                average_price=Decimal("36"),
                exposure_multiplier=Decimal("2"),
            )
        }
        intent = OrderIntent.create(
            signal_symbol="MU",
            execution_symbol="MUU",
            side=Side.SELL,
            quantity=1,
            estimated_price=Decimal("36"),
            exposure_multiplier=Decimal("2"),
        )
        decision = self.engine.evaluate(
            intent=intent,
            account=losing_account,
            positions=positions,
            market_prices={"MUU": Decimal("36")},
            estimated_commission=Decimal("0.35"),
        )
        self.assertTrue(decision.approved, decision.reasons)

    def test_reducing_an_over_limit_position_is_allowed(self) -> None:
        positions = {
            "MUU": Position(
                symbol="MUU",
                quantity=4,
                average_price=Decimal("36"),
                exposure_multiplier=Decimal("2"),
            )
        }
        intent = OrderIntent.create(
            signal_symbol="MU",
            execution_symbol="MUU",
            side=Side.SELL,
            quantity=1,
            estimated_price=Decimal("36"),
            exposure_multiplier=Decimal("2"),
        )
        decision = self.engine.evaluate(
            intent=intent,
            account=self.account,
            positions=positions,
            market_prices={"MUU": Decimal("36")},
        )
        self.assertTrue(decision.approved, decision.reasons)


if __name__ == "__main__":
    unittest.main()
