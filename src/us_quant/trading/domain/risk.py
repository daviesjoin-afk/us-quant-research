"""Risk domain.

``RiskDecision`` keeps its existing ``approved`` / ``reasons`` shape.  A
richer outcome vocabulary (APPROVE / REJECT / PAUSE / HALT) is deliberately
*not* introduced here: it would be a behaviour change to the live
``PreTradeRiskEngine``, which this change freezes.  That upgrade belongs to a
Risk v2 change, where the engine and its callers can move together.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def approve(cls) -> RiskDecision:
        return cls(approved=True)

    @classmethod
    def reject(cls, *reasons: str) -> RiskDecision:
        return cls(approved=False, reasons=tuple(reasons))
