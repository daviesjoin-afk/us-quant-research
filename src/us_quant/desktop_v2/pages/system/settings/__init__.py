"""Desktop UI v2 Settings workspace package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .page import SettingsPage

__all__ = ["SettingsPage"]


def __getattr__(name: str) -> Any:
    if name == "SettingsPage":
        from .page import SettingsPage

        return SettingsPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
