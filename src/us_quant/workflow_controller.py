"""UI-independent aggregate workflow state for the desktop workbench.

This module contains only explicit workflow ownership and read-only snapshots.
Application adapters remain responsible for research, market, account, and
broker I/O; the desktop renders these snapshots and sends commands elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .trading.runtime.workflow import PaperWorkflowController
from .trading.runtime.workflow_state import (
    ExecutionLeaseManager,
    WorkflowSnapshot,
    WorkflowStateError,
)


@dataclass(frozen=True, slots=True)
class ResearchWorkflowSnapshot:
    """Read-only progress state for a local research workflow."""

    busy: bool
    stage: str
    status_message: str


@dataclass(frozen=True, slots=True)
class MarketAccountSnapshot:
    """Read-only readiness published by existing market/account adapters."""

    account_ready: bool
    market_ready: bool
    status_message: str


class ResearchWorkflowController:
    """Own explicit research progress without performing research I/O."""

    def __init__(self) -> None:
        self._snapshot = ResearchWorkflowSnapshot(False, "IDLE", "Research is idle.")

    @property
    def snapshot(self) -> ResearchWorkflowSnapshot:
        return self._snapshot

    def begin(self, stage: str) -> None:
        """Mark one named research stage as active."""

        if self._snapshot.busy:
            raise WorkflowStateError("A research workflow stage is already active.")
        if not isinstance(stage, str) or not stage.strip():
            raise WorkflowStateError("A non-empty research stage is required.")
        self._snapshot = ResearchWorkflowSnapshot(True, stage, f"Research: {stage}")

    def complete(self, message: str) -> None:
        """Finish the active stage and publish its caller-supplied result."""

        if not self._snapshot.busy:
            raise WorkflowStateError("No active research workflow stage can be completed.")
        self._snapshot = ResearchWorkflowSnapshot(False, "COMPLETE", message)

    def fail(self, message: str) -> None:
        """Fail the active stage explicitly; no automatic retry is attempted."""

        if not self._snapshot.busy:
            raise WorkflowStateError("No active research workflow stage can fail.")
        self._snapshot = ResearchWorkflowSnapshot(False, "FAILED", message)


class MarketAccountController:
    """Publish readiness supplied by existing account and market adapters."""

    def __init__(self) -> None:
        self._snapshot = MarketAccountSnapshot(
            account_ready=False,
            market_ready=False,
            status_message="Account and market data are not ready.",
        )

    @property
    def snapshot(self) -> MarketAccountSnapshot:
        return self._snapshot

    def update(self, account_ready: bool, market_ready: bool, message: str) -> None:
        """Atomically replace published readiness without invoking any adapter."""

        self._snapshot = MarketAccountSnapshot(
            account_ready=bool(account_ready),
            market_ready=bool(market_ready),
            status_message=message,
        )


class ShadowWorkflowController:
    """Own the internal-only Shadow execution lease; it has no broker ports."""

    def __init__(self, *, leases: ExecutionLeaseManager) -> None:
        self._leases = leases
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        """Acquire the shared Shadow lease before an external caller starts simulation."""

        if self._active:
            raise WorkflowStateError("Shadow workflow is already active.")
        self._leases.acquire_shadow()
        self._active = True

    def stop(self) -> None:
        """Release the shared Shadow lease after an external caller stops simulation."""

        if not self._active:
            raise WorkflowStateError("Shadow workflow is not active.")
        self._leases.release_shadow()
        self._active = False


class WorkflowController:
    """Aggregate independent workflow status behind one shared execution lease."""

    def __init__(self) -> None:
        leases = ExecutionLeaseManager()
        self.research = ResearchWorkflowController()
        self.market_account = MarketAccountController()
        self.shadow = ShadowWorkflowController(leases=leases)
        self.paper = PaperWorkflowController(leases=leases)

    def snapshot(self) -> WorkflowSnapshot:
        """Combine Paper truth with current Shadow and market/account readiness."""

        market_account = self.market_account.snapshot
        return replace(
            self.paper.snapshot(
                account_ready=market_account.account_ready,
                market_ready=market_account.market_ready,
            ),
            shadow_active=self.shadow.active,
        )
