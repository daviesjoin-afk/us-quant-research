"""Native History workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.history.page import HistoryPage

__all__ = ["HistoryPage"]


def __getattr__(name: str) -> Any:
    if name == "HistoryPage":
        from us_quant.desktop_v2.pages.research.history.page import HistoryPage

        return HistoryPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
