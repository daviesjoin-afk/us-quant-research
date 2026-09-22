"""Native targeted validation workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceWorkspace,
    TargetedReviewDetail,
    TargetedRobustnessDetail,
    TargetedWorkspace,
)

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.targeted.page import (
        TargetedValidationPage,
    )

__all__ = [
    "TargetedEvidenceWorkspace",
    "TargetedReviewDetail",
    "TargetedRobustnessDetail",
    "TargetedValidationPage",
    "TargetedWorkspace",
]


def __getattr__(name: str) -> Any:
    if name == "TargetedValidationPage":
        from us_quant.desktop_v2.pages.research.targeted.page import (
            TargetedValidationPage,
        )

        return TargetedValidationPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
