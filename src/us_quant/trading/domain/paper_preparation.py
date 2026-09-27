"""Shared, Qt-free request vocabulary for preparing Paper candidates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PaperPreparationRequest:
    """Ask Execution to prepare this many candidates within this capital cap."""

    candidate_limit: int
    capital_limit: Decimal

    def __post_init__(self) -> None:
        if self.candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        if self.capital_limit < 0:
            raise ValueError("capital_limit cannot be negative")


__all__ = ["PaperPreparationRequest"]
