"""Native market scanner workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.scanner.page import ScannerPage

__all__ = ["ScannerPage"]


def __getattr__(name: str) -> Any:
    if name == "ScannerPage":
        from us_quant.desktop_v2.pages.research.scanner.page import ScannerPage

        return ScannerPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
