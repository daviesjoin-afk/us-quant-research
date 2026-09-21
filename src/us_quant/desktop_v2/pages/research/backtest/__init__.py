"""Native Desktop UI v2 backtest page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from us_quant.desktop_v2.pages.research.backtest.page import BacktestPage

__all__ = ["BacktestPage"]


def __getattr__(name: str) -> Any:
    if name == "BacktestPage":
        from us_quant.desktop_v2.pages.research.backtest.page import BacktestPage

        return BacktestPage
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
