"""The neutral contract shared by the targeted evidence service and its capability.

This module is deliberately Qt-free and free of both the desktop window and the
orchestration package, so the *direction* of the dependency stays one-way::

    TargetedEvidenceOrchestrator
            ↓
    DesktopTargetedEvidenceService
            ↓
    targeted_replay / targeted_robustness / targeted_validation /
    targeted_overfit / targeted_data_quality / targeted_execution_stress /
    targeted_review / MinuteQuoteStore

If the service imported the capability's own models, that arrow would reverse and
the service could reach back into orchestration. So the four types both sides
need live here instead, and both sides import *this*.

They exist for four separate reasons, and none of them is "a place to put
things":

* ``TargetedEvidenceRunInputs`` -- the request-time freeze.  Replay and
  robustness run with the *same* facts, so they share one input value rather
  than each reading the strategy selection and the research capital again
  inside a worker.  A worker that re-read either one would make the run about
  something other than what the operator clicked;
* ``TargetedRobustnessBundle`` -- the six results one robustness suite produces.
  It replaced an anonymous ``tuple[6]`` whose members were addressed by index,
  so ``result[5]`` was the only way to name the review;
* ``TargetedEvidenceSnapshot`` -- the canonical desktop evidence truth.  It is a
  single immutable value rather than seven accessors, because there is exactly
  one cross-capability consumer (the terminal export) and it reads all seven;
* ``TargetedEvidenceRuntimeEvent`` -- the event the capability wants recorded.
  The orchestrator does not hold the event store: it asks, and the window
  writes.  That keeps ``Evidence -> System`` from becoming a dependency.

The seven *result* types are the research artifacts themselves and are imported
rather than re-declared: they are already immutable, already Qt-free and already
the schema on disk, so a second declaration here would be a second truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import TargetedExecutionStressResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_replay import TargetedReplayResult
from us_quant.targeted_review import TargetedReviewResult
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult
from us_quant.trading.domain.strategy import StrategyVersion


@dataclass(frozen=True, slots=True)
class TargetedEvidenceRunInputs:
    """The facts a targeted evidence run is frozen at, at *request* time.

    Replay and robustness consume the same value, and freezing it once is what
    makes "the strategy version the operator saw" and "the capital the operator
    saw" true statements about a run that starts later on a worker.

    ``parameters`` is a tuple of pairs rather than a mapping so a caller cannot
    hand over a dict and keep mutating it behind the capability's back, which
    would silently change what the run computes after the freeze point.
    ``decimal_value`` is never round-tripped through ``float``: research
    artifacts record an exact ``Decimal`` initial equity.
    """

    symbol: str
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    parameters: tuple[tuple[str, object], ...]
    initial_equity: Decimal

    @classmethod
    def of(
        cls,
        *,
        symbol: str,
        strategy: StrategyVersion,
        initial_equity: Decimal,
    ) -> "TargetedEvidenceRunInputs":
        """Freeze one selected strategy version and one scenario capital."""

        return cls(
            symbol=symbol,
            strategy_version_id=strategy.version_id,
            strategy_semver=strategy.semver,
            parameter_hash=strategy.parameter_hash,
            parameters=tuple(
                (str(name), value)
                for name, value in strategy.parameters.items()
            ),
            initial_equity=initial_equity,
        )

    def parameters_mapping(self) -> dict[str, object]:
        """The research-facing projection: the executors take a mapping.

        A fresh dict on every call, so the caller cannot retain it and mutate
        the frozen inputs through it.
        """

        return dict(self.parameters)


@dataclass(frozen=True, slots=True)
class TargetedRobustnessBundle:
    """The six results one multi-day robustness suite produces, named.

    The pipeline is fixed and sequential -- robustness, overfit, data quality,
    walk-forward when there are at least 20 usable sessions, execution stress,
    review -- and these six are its output as one unit.  They are committed
    together or not at all, which is why they travel as a bundle rather than as
    six separately-returned values a caller could partially apply.
    """

    robustness: TargetedRobustnessResult
    walk_forward: TargetedWalkForwardResult | None
    overfit: TargetedOverfitResult
    data_quality: TargetedDataQualityResult
    execution_stress: TargetedExecutionStressResult
    review: TargetedReviewResult


@dataclass(frozen=True, slots=True)
class TargetedEvidenceSnapshot:
    """The canonical desktop truth for targeted research evidence.

    All seven result families plus the two historical selections, as one
    immutable value.  The selections are ``None`` at startup on purpose: this
    round does not introduce "select the latest run automatically", because
    that would be a behaviour change dressed up as an extraction.

    The snapshot is published to the terminal export, which genuinely reads all
    seven families; a per-family accessor would be seven surfaces where one is
    needed.
    """

    replay_results: tuple[TargetedReplayResult, ...] = ()
    robustness_results: tuple[TargetedRobustnessResult, ...] = ()
    walk_forward_results: tuple[TargetedWalkForwardResult, ...] = ()
    overfit_results: tuple[TargetedOverfitResult, ...] = ()
    data_quality_results: tuple[TargetedDataQualityResult, ...] = ()
    execution_stress_results: tuple[
        TargetedExecutionStressResult, ...
    ] = ()
    review_results: tuple[TargetedReviewResult, ...] = ()

    selected_robustness_run_id: str | None = None
    selected_review_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class TargetedEvidenceRuntimeEvent:
    """One runtime event the evidence capability wants recorded.

    The orchestrator holds no event store.  It publishes this and the window
    writes it, so targeted research cannot reach the System workspace.
    """

    severity: str
    component: str
    code: str
    message: str


__all__ = [
    "TargetedEvidenceRunInputs",
    "TargetedEvidenceRuntimeEvent",
    "TargetedEvidenceSnapshot",
    "TargetedRobustnessBundle",
]
