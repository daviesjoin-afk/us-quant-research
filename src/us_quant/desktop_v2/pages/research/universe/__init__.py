"""Native Universe workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.universe.page import UniversePage

__all__ = ["UniversePage"]


def __getattr__(name: str) -> Any:
    if name == "UniversePage":
        from us_quant.desktop_v2.pages.research.universe.page import UniversePage

        return UniversePage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
