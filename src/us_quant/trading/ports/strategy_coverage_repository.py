"""Persistence ports for versioned coverage policies and coverage evaluations.

Policies and evaluations have different lifecycles and are deliberately
separate here: a policy is an immutable, CAS-appended revision that
*authorises*, while an evaluation is a durable record of what that
authorisation decided on one evidence set.  Neither table is the gate's or the
authentication's authority.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageEvaluation,
    StrategyCoveragePolicy,
)


class StrategyCoverageRepositoryError(RuntimeError):
    """Stored coverage state is unreadable or cannot be persisted."""


class StrategyCoverageRepositoryConflict(StrategyCoverageRepositoryError):
    """An immutable record already exists with a different payload, or a CAS lost."""


class StrategyCoverageRepositoryNotFound(StrategyCoverageRepositoryError):
    """No record exists for the requested identity."""


class StrategyCoveragePolicyStorePort(Protocol):
    """Store coverage policies immutably; make no coverage or lifecycle decision."""

    def append_policy_revision(
        self,
        policy: StrategyCoveragePolicy,
        *,
        expected_current_revision: int | None,
    ) -> None:
        """CAS-append one revision.

        ``expected_current_revision`` of ``None`` asserts that no revision
        exists yet; otherwise the stored latest revision must equal it and the
        new policy's revision must be exactly one greater.  A writer holding a
        stale revision loses instead of silently overwriting history.
        """

    def get_policy(self, policy_id: str, revision: int) -> StrategyCoveragePolicy:
        """Read one exact revision, raising a typed error when absent or corrupt."""

    def active_policy(self, policy_id: str) -> StrategyCoveragePolicy | None:
        """Return the highest stored revision for ``policy_id``, or ``None``."""

    def policy_revisions(self, policy_id: str) -> tuple[int, ...]:
        """Return every stored revision, oldest first."""


class StrategyCoverageRepositoryPort(Protocol):
    """Store coverage evaluations durably; make no lifecycle decision."""

    def record_evaluation(self, evaluation: StrategyCoverageEvaluation) -> None:
        """Persist once per semantic ID; timestamp-only retries keep the first row."""

    def get_evaluation(self, evaluation_id: str) -> StrategyCoverageEvaluation:
        """Read one evaluation, raising a typed error for missing/corrupt data."""

    def evaluations_for_version(
        self, version_id: str
    ) -> tuple[StrategyCoverageEvaluation, ...]:
        """Return evaluations for one version, newest first with a stable tie-break."""

    def latest_for_version(
        self, version_id: str
    ) -> StrategyCoverageEvaluation | None:
        """Return the newest evaluation for one version, or ``None``."""


__all__ = [
    "StrategyCoveragePolicyStorePort",
    "StrategyCoverageRepositoryConflict",
    "StrategyCoverageRepositoryError",
    "StrategyCoverageRepositoryNotFound",
    "StrategyCoverageRepositoryPort",
]
