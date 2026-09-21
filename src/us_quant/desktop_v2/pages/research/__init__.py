"""Desktop UI v2 research route package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .navigation import ResearchWorkspace

if TYPE_CHECKING:
    from .page import ResearchPage

__all__ = ["ResearchPage", "ResearchWorkspace"]


def __getattr__(name: str) -> Any:
    if name == "ResearchPage":
        from .page import ResearchPage

        return ResearchPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")