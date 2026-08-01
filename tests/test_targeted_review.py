from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from test_targeted_validation import PARAMETERS, _evidence
from us_quant.targeted_overfit import (
    run_targeted_overfit_diagnostics,
)
from us_quant.targeted_data_quality import (
    run_targeted_data_quality,
)
from us_quant.targeted_execution_stress import (
    ExecutionStressScenario,
    TargetedExecutionStressResult,
)
from us_quant.targeted_review import (
    load_targeted_reviews,
    run_targeted_review,
    save_targeted_review,
)
from us_quant.targeted_validation import run_targeted_walk_forward


class TargetedReviewTests(unittest.TestCase):
    def test_review_can_become_eligible_without_auto_approval(self) -> None:
        records, robustness = _evidence(25)
        day_order = {
            day: index
            for index, day in enumerate(
                sorted(
                    {
                        row.session_date
                        for row in robustness.session_outcomes
                    }
                )
            )
        }
        varied = replace(
            robustness,
            session_outcomes=tuple(
                replace(
                    row,
                    total_return=(
                        Decimal("0.002")
                        + Decimal(
                            str(
                                (
                                    -1
                                    if day_order[row.session_date] % 2
                                    else 1
                                )
                                * 0.00015
                            )
                        )
                    ),
                )
                for row in robustness.session_outcomes
            ),
        )
        validation = run_targeted_walk_forward(
            varied,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        validation = replace(
            validation,
            folds=tuple(
                replace(fold, validation_passed=True)
                for fold in validation.folds
            ),
            out_of_sample_metrics=replace(
                validation.out_of_sample_metrics,
                compounded_return=Decimal("0.02"),
                total_fills=20,
                commission_cost=Decimal("7"),
            ),
            out_of_sample_benchmark=replace(
                validation.out_of_sample_benchmark,
                compounded_return=Decimal("0.005"),
            ),
            out_of_sample_excess_return=Decimal("0.015"),
            validation_passed_folds=len(validation.folds),
            review_ready=True,
        )
        overfit = replace(
            run_targeted_overfit_diagnostics(varied),
            pbo=Decimal("0.10"),
            dsr_probability=Decimal("0.99"),
        )
        enriched_records = tuple(
            replace(
                row,
                source_age_seconds=1,
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
            )
            for row in records
        )
        data_quality = run_targeted_data_quality(
            varied, enriched_records
        )
        scenario = ExecutionStressScenario(
            scenario="压力",
            slippage_bps=Decimal("10"),
            commission_per_order=Decimal("0.70"),
            session_count=25,
            compounded_return=Decimal("0.01"),
            maximum_drawdown=Decimal("0.01"),
            total_fills=20,
            commission_cost=Decimal("14"),
            degradation_vs_configured=Decimal("-0.01"),
        )
        execution_stress = TargetedExecutionStressResult(
            run_id="stress-run",
            robustness_run_id=varied.run_id,
            symbol=varied.symbol,
            strategy_version_id=varied.strategy_version_id,
            strategy_semver=varied.strategy_semver,
            base_parameter_hash=varied.base_parameter_hash,
            data_hash=varied.data_hash,
            provider=varied.provider,
            scenarios=(scenario,),
            worst_stressed_return=Decimal("0.01"),
            worst_performance_degradation=Decimal("-0.01"),
            size_observations=20,
            p95_top_of_book_participation=Decimal("0.01"),
            maximum_top_of_book_participation=Decimal("0.02"),
            capacity_status="通过",
            stress_resilient=True,
            evidence_grade="test",
            limitations=(),
            status="research_execution_stress",
        )
        review = run_targeted_review(
            varied,
            validation,
            overfit,
            parameters=PARAMETERS,
            data_quality=data_quality,
            execution_stress=execution_stress,
        )
        self.assertTrue(review.eligible_for_independent_review)
        self.assertEqual(review.blocking_failures, 0)
        self.assertEqual(
            review.decision, "ELIGIBLE_FOR_INDEPENDENT_REVIEW"
        )
        self.assertIsNotNone(
            review.dependence.lag1_autocorrelation
        )
        self.assertGreaterEqual(
            review.dependence.effective_sample_size_ar1,
            Decimal("10"),
        )

        with TemporaryDirectory() as directory:
            path = save_targeted_review(review, directory)
            loaded = load_targeted_reviews(directory)
            content = path.read_text(encoding="utf-8")
        self.assertEqual(loaded[0], review)
        self.assertIn('"manual_approval_recorded": false', content)
        self.assertIn('"automatic_strategy_promotion": false', content)
        self.assertIn('"orders_submitted": false', content)

    def test_synthetic_origin_is_a_hard_block(self) -> None:
        records, robustness = _evidence(25)
        robustness = replace(
            robustness,
            evidence_origins=("synthetic_preview",),
        )
        validation = run_targeted_walk_forward(
            robustness,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        overfit = run_targeted_overfit_diagnostics(robustness)
        review = run_targeted_review(
            robustness,
            validation,
            overfit,
            parameters=PARAMETERS,
        )
        origin_gate = next(
            gate
            for gate in review.gates
            if gate.code == "captured_origin"
        )
        self.assertFalse(origin_gate.passed)
        self.assertFalse(review.eligible_for_independent_review)
        self.assertEqual(review.decision, "BLOCKED")

    def test_missing_walk_forward_returns_explicit_blockers(self) -> None:
        _, robustness = _evidence(19)
        overfit = run_targeted_overfit_diagnostics(robustness)
        review = run_targeted_review(
            robustness,
            None,
            overfit,
            parameters=PARAMETERS,
        )
        self.assertGreater(review.blocking_failures, 0)
        self.assertEqual(
            review.dependence.status, "样本不足·不可估计"
        )
        self.assertFalse(review.eligible_for_independent_review)


if __name__ == "__main__":
    unittest.main()
