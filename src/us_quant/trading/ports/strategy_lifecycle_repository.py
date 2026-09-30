"""Persistence ports for lifecycle policies and lifecycle decisions.

Policies authorise; decisions record.  They are separate here for the same
reason they are separate in coverage: a policy is an immutable, CAS-appended
statement of what may happen, while a decision is the durable progress of one
attempt to make it happen -- including the ``PREPARED`` row that survives a
crash between "we decided" and "it applied".
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleDecision,
    StrategyLifecyclePolicy,
)


class StrategyLifecycleRepositoryError(RuntimeError):
    """Stored lifecycle state is unreadable or cannot be persisted."""


class StrategyLifecycleRepositoryConflict(StrategyLifecycleRepositoryError):
    """An immutable record already exists with a different payload, or a CAS lost."""


class StrategyLifecycleRepositoryNotFound(StrategyLifecycleRepositoryError):
    """No record exists for the requested identity."""


class StrategyLifecyclePolicyStorePort(Protocol):
    """Store lifecycle policies immutably; make no lifecycle decision."""

    def append_policy_revision(
        self,
        policy: StrategyLifecyclePolicy,
        *,
        expected_current_revision: int | None,
    ) -> None:
        """CAS-append one revision; a writer holding a stale revision loses."""

    def get_policy(self, policy_id: str, revision: int) -> StrategyLifecyclePolicy:
        """Read one exact revision, raising a typed error when absent or corrupt."""

    def active_policy(self, policy_id: str) -> StrategyLifecyclePolicy | None:
        """Return the highest stored revision for ``policy_id``, or ``None``."""

    def policy_revisions(self, policy_id: str) -> tuple[int, ...]:
        """Return every stored revision, oldest first."""


class StrategyLifecycleDecisionRepositoryPort(Protocol):
    """Store lifecycle decisions durably; make no decision of its own."""

    def record_decision(self, decision: StrategyLifecycleDecision) -> None:
        """Persist once per semantic ID; a timestamp-only retry keeps the first row."""

    def get_decision(self, decision_id: str) -> StrategyLifecycleDecision:
        """Read one decision, raising a typed error for missing/corrupt data."""

    def decisions_for_version(
        self, version_id: str
    ) -> tuple[StrategyLifecycleDecision, ...]:
        """Return decisions for one version, newest first with a stable tie-break."""

    def prepared_decisions(
        self, version_id: str
    ) -> tuple[StrategyLifecycleDecision, ...]:
        """Return the decisions still awaiting reconciliation, oldest first."""

    def mark_applied(
        self, decision_id: str, *, applied_at: datetime
    ) -> StrategyLifecycleDecision:
        """Transition ``PREPARED`` -> ``APPLIED``, or return the applied row as-is."""

    def mark_superseded(self, decision_id: str) -> StrategyLifecycleDecision:
        """Transition ``PREPARED`` -> ``SUPERSEDED`` because the world moved on."""


__all__ = [
    "StrategyLifecycleDecisionRepositoryPort",
    "StrategyLifecyclePolicyStorePort",
    "StrategyLifecycleRepositoryConflict",
    "StrategyLifecycleRepositoryError",
    "StrategyLifecycleRepositoryNotFound",
]
