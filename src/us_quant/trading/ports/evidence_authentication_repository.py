"""Persistence port for durable research-evidence authentication records.

Kept separate from the strategy gate repository on purpose: authentication
answers a different question from statistical eligibility, and neither table
may become the other's authority.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.evidence_auth import EvidenceAuthenticationResult


class EvidenceAuthenticationRepositoryError(RuntimeError):
    """Stored authentication evidence is unreadable or cannot be persisted."""


class EvidenceAuthenticationRepositoryConflict(EvidenceAuthenticationRepositoryError):
    """An authentication ID already exists with a different immutable payload."""


class EvidenceAuthenticationRepositoryNotFound(EvidenceAuthenticationRepositoryError):
    """No authentication record exists for the requested deterministic identity."""


class StrategyEvidenceAuthenticationRepositoryPort(Protocol):
    """Store authentication facts; make no lifecycle or eligibility decisions."""

    def record(self, result: EvidenceAuthenticationResult) -> None:
        """Persist once per semantic ID; timestamp-only retries keep the first row."""

    def get(self, authentication_id: str) -> EvidenceAuthenticationResult:
        """Read one record, raising a typed error for missing/corrupt data."""

    def authentications_for_version(
        self, version_id: str
    ) -> tuple[EvidenceAuthenticationResult, ...]:
        """Return records for one version, newest first with a stable tie-break."""

    def authentications_for_review(
        self, review_run_id: str
    ) -> tuple[EvidenceAuthenticationResult, ...]:
        """Return records for one review run, newest first."""

    def latest_for_version(
        self, version_id: str
    ) -> EvidenceAuthenticationResult | None:
        """Return the newest record for one version, or ``None``."""


__all__ = [
    "EvidenceAuthenticationRepositoryConflict",
    "EvidenceAuthenticationRepositoryError",
    "EvidenceAuthenticationRepositoryNotFound",
    "StrategyEvidenceAuthenticationRepositoryPort",
]
