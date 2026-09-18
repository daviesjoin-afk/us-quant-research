"""Numeric primitives and the execution-environment enum.

These are the lowest-level shared concepts.  They were moved verbatim from the
retired ``us_quant.domain`` module, so the arithmetic semantics that the
research, backtest and Paper paths already rely on are unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal(value: Decimal | str | int | float) -> Decimal:
    """Convert external numeric input without silently keeping binary floats."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class Environment(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"
