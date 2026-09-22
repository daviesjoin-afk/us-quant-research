"""The pure ownership rules the targeted evidence capability delegates to.

Qt-free, window-free and side-effect-free: the same inputs give the same answer
and nothing is touched.  A refusal rule and a commit rule are the two places a
migration silently changes behaviour, so both live here where a test can call them
with plain values instead of driving a Qt event loop.

The refusal words themselves are in ``messages``, next to the other text this
capability publishes.

* **refusal order.**  Missing strategy version, then invalid symbol, then the
  universe eligibility gate.  Unchanged: an operator with no strategy chosen is
  told that rather than sent to fix a symbol;
* **the universe gate is one-way.**  No universe is *not* a refusal -- targeted
  research has always been allowed before an official universe exists, and making
  it fail closed would be a product-behaviour change smuggled into an extraction.
  Only a universe that exists *and* does not list the symbol as research-eligible
  refuses;
* **commit shape.**  Both commits build a whole new snapshot rather than mutating
  one, so a snapshot is never observable half-updated -- the failure the old
  per-list ``insert(0, ...)`` sequence could produce, where the robustness list was
  already new while the review entry was still the old one.

The symbol pattern stays private here: there is no global ``SymbolValidator``,
because a shared validator would be a new cross-capability abstraction created by
an extraction.
"""

from __future__ import annotations

from dataclasses import replace

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceSnapshot,
    TargetedRobustnessBundle,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence.messages import (
    INELIGIBLE_MESSAGE,
    INELIGIBLE_TITLE,
    INVALID_SYMBOL_TITLE,
    MISSING_STRATEGY_MESSAGE,
    MISSING_STRATEGY_TITLE,
    REPLAY_INVALID_SYMBOL_MESSAGE,
    ROBUSTNESS_INVALID_SYMBOL_MESSAGE,
    is_valid_symbol,
)
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import TargetedExecutionStressResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_replay import TargetedReplayResult
from us_quant.targeted_review import TargetedReviewResult
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.universe import UniverseSnapshot

REPLAY_PURPOSE = "replay"
ROBUSTNESS_PURPOSE = "robustness"


def is_research_eligible(
    universe: UniverseSnapshot | None,
    symbol: str,
) -> bool:
    """Whether the symbol clears the universe's non-China research gate.

    A missing universe answers ``True``; ``False`` is reserved for an *existing*
    pool that says no.
    """

    if universe is None:
        return True
    return any(
        record.symbol == symbol and record.eligible_for_research
        for record in universe.records
    )


def request_refusal(
    *,
    purpose: str,
    strategy: StrategyVersion | None,
    symbol: str,
    universe: UniverseSnapshot | None,
) -> tuple[str, str] | None:
    """The refusal one request earns, or ``None`` to proceed.

    ``purpose`` is ``"replay"`` or ``"robustness"``: the two name their action in
    the invalid-symbol message, which is the only place these checks differ.
    """

    if strategy is None:
        return MISSING_STRATEGY_TITLE, MISSING_STRATEGY_MESSAGE
    if not is_valid_symbol(symbol):
        message = (
            REPLAY_INVALID_SYMBOL_MESSAGE
            if purpose == REPLAY_PURPOSE
            else ROBUSTNESS_INVALID_SYMBOL_MESSAGE
        )
        return INVALID_SYMBOL_TITLE, message
    if not is_research_eligible(universe, symbol):
        return INELIGIBLE_TITLE, INELIGIBLE_MESSAGE.format(symbol=symbol)
    return None


def validate_replay(result: object) -> TargetedReplayResult:
    """Return ``result`` as a replay result, or raise ``TypeError``."""

    if not isinstance(result, TargetedReplayResult):
        raise TypeError("unexpected targeted replay result")
    return result


def validate_bundle(result: object) -> TargetedRobustnessBundle:
    """Return ``result`` as a complete robustness bundle, or raise ``TypeError``.

    The service already returns a typed bundle, so this is a boundary check rather
    than a conversion.  It checks the *members* as well as the container, because
    the six results are committed together: a bundle whose review slot holds the
    wrong object would otherwise be committed whole and then render as six tables
    reading attributes off an unrelated type.  ``walk_forward`` is the one member
    allowed to be absent, since the suite legitimately skips it below 20 sessions.
    """

    if not isinstance(result, TargetedRobustnessBundle):
        raise TypeError("unexpected targeted robustness bundle")
    if not isinstance(result.robustness, TargetedRobustnessResult):
        raise TypeError("unexpected targeted robustness result")
    if result.walk_forward is not None and not isinstance(
        result.walk_forward, TargetedWalkForwardResult
    ):
        raise TypeError("unexpected targeted walk-forward result")
    if not isinstance(result.overfit, TargetedOverfitResult):
        raise TypeError("unexpected targeted overfit result")
    if not isinstance(result.data_quality, TargetedDataQualityResult):
        raise TypeError("unexpected targeted data-quality result")
    if not isinstance(result.execution_stress, TargetedExecutionStressResult):
        raise TypeError("unexpected targeted execution-stress result")
    if not isinstance(result.review, TargetedReviewResult):
        raise TypeError("unexpected targeted review result")
    return result


def commit_replay(
    snapshot: TargetedEvidenceSnapshot,
    result: TargetedReplayResult,
) -> TargetedEvidenceSnapshot:
    """The snapshot with one replay prepended.  Newest first, as before."""

    return replace(
        snapshot, replay_results=(result, *snapshot.replay_results)
    )


def commit_bundle(
    snapshot: TargetedEvidenceSnapshot,
    bundle: TargetedRobustnessBundle,
) -> TargetedEvidenceSnapshot:
    """The snapshot with a whole suite prepended and both selections moved.

    Five lists and both selections move in one ``replace``, so no observer can see
    a snapshot in which the robustness list is new but the review selection still
    points at the previous run.  The walk-forward family grows only when the suite
    actually produced one, the same conditional the inline handler applied.
    """

    walk_forward = snapshot.walk_forward_results
    if bundle.walk_forward is not None:
        walk_forward = (bundle.walk_forward, *walk_forward)
    return replace(
        snapshot,
        robustness_results=(
            bundle.robustness,
            *snapshot.robustness_results,
        ),
        walk_forward_results=walk_forward,
        overfit_results=(bundle.overfit, *snapshot.overfit_results),
        data_quality_results=(
            bundle.data_quality,
            *snapshot.data_quality_results,
        ),
        execution_stress_results=(
            bundle.execution_stress,
            *snapshot.execution_stress_results,
        ),
        review_results=(bundle.review, *snapshot.review_results),
        selected_robustness_run_id=bundle.robustness.run_id,
        selected_review_run_id=bundle.review.run_id,
    )


__all__ = [
    "REPLAY_PURPOSE",
    "ROBUSTNESS_PURPOSE",
    "commit_bundle",
    "commit_replay",
    "is_research_eligible",
    "request_refusal",
    "validate_bundle",
    "validate_replay",
]