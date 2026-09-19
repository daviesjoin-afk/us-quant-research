"""Strategy repository port: versioned strategy governance persistence.

The port is deliberately dumb.  It stores and returns what it is handed, and
it answers no governance question at all: it does not decide whether a
transition is legal, whether a gate has passed, or whether a clone is allowed.
Those are policy, they belong to ``StrategyApplication``, and keeping them out
of here is what allows a second adapter -- an in-memory one for tests, or a
different store -- to satisfy this protocol without re-implementing the state
machine or, worse, implementing a slightly different one.

The surface returns domain types, never rows.  The old registry-shaped view
type is gone: the runtime reads ``StrategyVersion``, and the SQLite adapter
owns every conversion from storage representation into that type, including
the decision to fail closed on a value it cannot interpret.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from us_quant.trading.domain.strategy import (
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)


class StrategyRepositoryError(RuntimeError):
    """Base class for every strategy persistence failure.

    Raised for unreadable storage as well: a stored status that is not a
    ``StrategyStatus``, a ``mode`` that is not a ``StrategyMode``, a
    ``risk_budget_pct`` that is not a decimal, malformed ``parameters_json``,
    or a naive timestamp.  None of those are repaired with a guessed default
    -- a version whose governance state had to be invented is worse than no
    version at all.
    """


class StrategyRepositoryConflict(StrategyRepositoryError):
    """The write would violate an existing uniqueness constraint.

    Today that means one thing: ``(strategy_id, semver)`` already exists.
    Immutable versions are only immutable if a second version cannot silently
    overwrite the first, so this is a refusal, not an upsert.
    """


class StrategyRepositoryNotFound(StrategyRepositoryError):
    """No version is stored under the requested ``version_id``.

    Split out from the base class so ``StrategyApplication`` can translate it
    into its own user-facing not-found error instead of string-matching a
    message.
    """


@dataclass(frozen=True, slots=True)
class StrategyAuditEvent:
    """One governance event, written in the same transaction as its change.

    ``occurred_at`` must be timezone-aware.  The audit trail is the only
    record of who moved a version to ``PAPER_SHADOW`` and when, and an
    unsigned timestamp in it cannot be ordered against anything else.
    """

    strategy_id: str
    version_id: str
    event: str
    detail: str
    occurred_at: datetime


class StrategyRepositoryPort(Protocol):
    """Persistence for strategy definitions, versions and their lifecycle."""

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        """Every stored version, including retired ones.

        Retired versions are not filtered out here.  Eligibility is a
        selection policy, and a repository that hid rows would make the
        governance audit view -- which must show them -- impossible to build.
        """

    def get_version(self, version_id: str) -> StrategyVersion:
        """One version by id.

        Raises ``StrategyRepositoryNotFound`` when it is not stored, and
        ``StrategyRepositoryError`` when a stored row cannot be believed.
        """

    def insert_version(
        self,
        version: StrategyVersion,
        *,
        audit: StrategyAuditEvent,
    ) -> None:
        """Store a brand-new version and its opening audit event.

        The definition row, the version row, the deployment row and the audit
        row are written in one transaction.  A half-written version would be
        a version whose status nobody recorded.
        """

    def update_deployment(
        self,
        *,
        version_id: str,
        status: StrategyStatus,
        mode: StrategyMode,
        updated_at: datetime,
        audit: StrategyAuditEvent,
    ) -> None:
        """Move a version's deployment row to ``status`` / ``mode``.

        The update and its audit event share one transaction, so a failed
        audit insert leaves the deployment where it was rather than
        producing an unexplained status change.
        """


__all__ = [
    "StrategyAuditEvent",
    "StrategyRepositoryConflict",
    "StrategyRepositoryError",
    "StrategyRepositoryNotFound",
    "StrategyRepositoryPort",
]
