from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from test_targeted_validation import _evidence
from us_quant.targeted_overfit import (
    load_targeted_overfits,
    run_targeted_overfit_diagnostics,
    save_targeted_overfit,
)


class TargetedOverfitTests(unittest.TestCase):
    def test_cscv_pbo_and_dsr_are_bounded_and_persisted(self) -> None:
        _, robustness = _evidence(25)
        varied = replace(
            robustness,
            session_outcomes=tuple(
                replace(
                    row,
                    total_return=(
                        row.total_return
                        + Decimal(
                            (
                                int(row.session_date[-2:])
                                % (index + 3)
                                - 1
                            )
                        )
                        * Decimal("0.0001")
                    ),
                )
                for index, row in enumerate(
                    robustness.session_outcomes
                )
            ),
        )
        result = run_targeted_overfit_diagnostics(varied)
        self.assertEqual(result.cscv_partitions, 10)
        self.assertEqual(result.cscv_combinations, 252)
        self.assertEqual(result.observations_used, 20)
        self.assertEqual(result.excluded_tail, 5)
        self.assertIsNotNone(result.pbo)
        self.assertIsNotNone(result.probability_oos_loss)
        self.assertIsNotNone(result.dsr_probability)
        self.assertGreaterEqual(result.pbo, 0)
        self.assertLessEqual(result.pbo, 1)
        self.assertGreaterEqual(result.dsr_probability, 0)
        self.assertLessEqual(result.dsr_probability, 1)

        with TemporaryDirectory() as directory:
            path = save_targeted_overfit(result, directory)
            loaded = load_targeted_overfits(directory)
            content = path.read_text(encoding="utf-8")
        self.assertEqual(loaded[0], result)
        self.assertIn('"orders_submitted": false', content)
        self.assertIn('"automatic_strategy_promotion": false', content)

    def test_constant_candidate_returns_make_dsr_unavailable(self) -> None:
        _, robustness = _evidence(20)
        result = run_targeted_overfit_diagnostics(robustness)
        self.assertIsNotNone(result.pbo)
        self.assertIsNone(result.dsr_probability)
        self.assertIn("DSR不可估", result.evidence_grade)
        self.assertTrue(
            any("方差不足" in note for note in result.limitations)
        )

    def test_short_sample_is_explicitly_unavailable(self) -> None:
        _, robustness = _evidence(19)
        result = run_targeted_overfit_diagnostics(robustness)
        self.assertIsNone(result.pbo)
        self.assertIsNone(result.dsr_probability)
        self.assertEqual(result.cscv_combinations, 0)
        self.assertEqual(result.evidence_grade, "样本不足·不可估计")

    def test_cscv_is_deterministic_for_same_evidence(self) -> None:
        _, robustness = _evidence(20)
        first = run_targeted_overfit_diagnostics(robustness)
        second = run_targeted_overfit_diagnostics(robustness)
        comparable = (
            "pbo",
            "probability_oos_loss",
            "mean_is_selected_return",
            "mean_oos_selected_return",
            "average_performance_degradation",
            "median_oos_rank",
        )
        self.assertEqual(
            tuple(getattr(first, field) for field in comparable),
            tuple(getattr(second, field) for field in comparable),
        )


if __name__ == "__main__":
    unittest.main()
