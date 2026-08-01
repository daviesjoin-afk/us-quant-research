from decimal import Decimal
import unittest

from us_quant.portfolio import IntegerPositionSizer, SubstitutionRule


class IntegerPositionSizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sizer = IntegerPositionSizer(
            {
                "MU": SubstitutionRule(
                    source_symbol="MU",
                    execution_symbol="MUU",
                    exposure_multiplier=Decimal("2"),
                    holding_mode="intraday_or_short_term",
                )
            }
        )

    def test_uses_approved_substitute_when_mu_is_unaffordable(self) -> None:
        resolved = self.sizer.resolve(
            signal_symbol="MU",
            target_risk_exposure=Decimal("150"),
            prices={"MU": Decimal("990"), "MUU": Decimal("36")},
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.execution_symbol, "MUU")
        self.assertEqual(resolved.quantity, 2)
        self.assertEqual(resolved.risk_exposure, Decimal("144"))
        self.assertTrue(resolved.used_substitution)

    def test_prefers_mu_when_whole_share_is_affordable(self) -> None:
        resolved = self.sizer.resolve(
            signal_symbol="MU",
            target_risk_exposure=Decimal("1200"),
            prices={"MU": Decimal("990"), "MUU": Decimal("36")},
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.execution_symbol, "MU")
        self.assertEqual(resolved.quantity, 1)
        self.assertFalse(resolved.used_substitution)

    def test_can_force_approved_substitute_for_research(self) -> None:
        sizer = IntegerPositionSizer(
            {
                "MU": SubstitutionRule(
                    source_symbol="MU",
                    execution_symbol="MUU",
                    exposure_multiplier=Decimal("2"),
                    holding_mode="intraday_or_short_term",
                )
            },
            force_substitution_symbols=frozenset({"MU"}),
        )
        resolved = sizer.resolve(
            signal_symbol="MU",
            target_risk_exposure=Decimal("150"),
            prices={"MU": Decimal("100"), "MUU": Decimal("30")},
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.execution_symbol, "MUU")
        self.assertEqual(resolved.quantity, 2)
        self.assertTrue(resolved.used_substitution)

    def test_rejects_forced_substitution_without_approved_rule(self) -> None:
        with self.assertRaises(ValueError):
            IntegerPositionSizer(
                {},
                force_substitution_symbols=frozenset({"MU"}),
            )

    def test_returns_none_without_affordable_or_approved_instrument(self) -> None:
        sizer = IntegerPositionSizer({})
        resolved = sizer.resolve(
            signal_symbol="ABC",
            target_risk_exposure=Decimal("50"),
            prices={"ABC": Decimal("100")},
        )
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
