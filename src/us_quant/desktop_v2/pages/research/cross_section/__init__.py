"""Native cross-section research workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.cross_section.page import (
        CrossSectionResearchPage,
    )

__all__ = ["CrossSectionResearchPage"]


def __getattr__(name: str) -> Any:
    if name == "CrossSectionResearchPage":
        from us_quant.desktop_v2.pages.research.cross_section.page import (
            CrossSectionResearchPage,
        )

        return CrossSectionResearchPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")