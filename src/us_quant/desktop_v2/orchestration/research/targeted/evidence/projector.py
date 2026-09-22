"""The adapter from the desktop evidence snapshot to the page's view model.

Two vocabularies meet here and nowhere else: the capability's
``TargetedEvidenceSnapshot`` (what the desktop stores) and the page's
``TargetedEvidenceView`` (what the page draws).  The projection *rules* stay in
the page's own presenter; this module only names which snapshot field feeds which
presenter argument.

It is spelled once because four paint paths must project identically -- startup
restore, a selection, a replay and a suite.  Four hand-written call sites would be
four chances for one family to be silently dropped from the page while remaining
present in the snapshot.

Qt-free and pure.
"""

from __future__ import annotations

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceSnapshot,
)
from us_quant.desktop_v2.pages.research.targeted.evidence_presenter import (
    evidence_view,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceView,
)


def project_evidence(snapshot: TargetedEvidenceSnapshot) -> TargetedEvidenceView:
    """One snapshot as the page's evidence view model."""

    return evidence_view(
        replay_results=snapshot.replay_results,
        robustness_results=snapshot.robustness_results,
        walk_forward_results=snapshot.walk_forward_results,
        overfit_results=snapshot.overfit_results,
        data_quality_results=snapshot.data_quality_results,
        execution_stress_results=snapshot.execution_stress_results,
        review_results=snapshot.review_results,
        selected_robustness_run_id=snapshot.selected_robustness_run_id,
        selected_review_run_id=snapshot.selected_review_run_id,
    )


__all__ = ["project_evidence"]
