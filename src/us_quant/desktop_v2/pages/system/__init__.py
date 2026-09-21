"""Desktop UI v2 system route package.

``SystemWorkspace`` is Qt-free and importable on its own; ``SystemPage`` is
exported lazily so importing the package for its semantic keys never pulls in
PySide6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .navigation import SystemWorkspace

if TYPE_CHECKING:
    from .page import SystemPage

__all__ = ["SystemPage", "SystemWorkspace"]


def __getattr__(name: str) -> Any:
    if name == "SystemPage":
        from .page import SystemPage

        return SystemPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
