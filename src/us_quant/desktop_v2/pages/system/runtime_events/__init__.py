"""Desktop UI v2 Runtime Events workspace package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .page import RuntimeEventsPage

__all__ = ["RuntimeEventsPage"]


def __getattr__(name: str) -> Any:
    if name == "RuntimeEventsPage":
        from .page import RuntimeEventsPage

        return RuntimeEventsPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
