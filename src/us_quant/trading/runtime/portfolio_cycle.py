"""Cycle driver joining signal workers to one portfolio operations authority."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from threading import Lock

from us_quant.trading.application.portfolio_operations import (
    PortfolioCycleRequest,
    PortfolioExecutionGates,
    PortfolioOperationsApplication,
    PortfolioOperatingPlanApplication,
)
from us_quant.trading.runtime.portfolio_strategies import PortfolioStrategyWorkers


class PortfolioPaperCycleDriver:
    """Revalidate the frozen plan and run one freshly reconciled portfolio cycle."""

    def __init__(self, *, operations: PortfolioOperationsApplication, plan_application: PortfolioOperatingPlanApplication, frozen_plan, workers: PortfolioStrategyWorkers, session_id: str, account_alias: str, broker_observation_source, autonomous_entries_allowed=lambda: True, publish_operations_view=lambda: None) -> None:
        self.runtime = operations.runtime
        self._operations = operations
        self._plan_application = plan_application
        self._frozen_plan = frozen_plan
        self._workers = workers
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("portfolio Paper session id is required")
        self._session_id = session_id
        self._account_alias = account_alias
        self._broker_observation_source = broker_observation_source
        self._autonomous_entries_allowed = autonomous_entries_allowed
        self._publish_operations_view = publish_operations_view
        self._sequence = 0
        self._lock = Lock()

    def __call__(self, market_snapshot, observed_at: datetime, allow_entries: bool, flatten: bool):
        try:
            current_plan = self._plan_application.load()
            if current_plan != self._frozen_plan:
                raise RuntimeError("frozen Paper portfolio plan changed during the active session")
            if allow_entries and not self._autonomous_entries_allowed():
                # The persisted Paper autonomy intent can be killed or paused between
                # scheduler ticks. Re-read it at every autonomous market cycle so the
                # asynchronous supervisor never creates an exposure window.
                allow_entries = False
            self._workers.observe(
                market_snapshot,
                observed_at=observed_at,
                entries_enabled=allow_entries,
                flatten=flatten,
            )
            self._broker_observation_source.set_market_snapshot(market_snapshot)
            with self._lock:
                self._sequence += 1
                cycle_id = f"{self._session_id}:{self._sequence}"
            request = PortfolioCycleRequest(
                portfolio_cycle_id=cycle_id,
                observed_at=observed_at,
                proposal_cutoff=observed_at,
                snapshot_identity=sha256(
                    f"{self._account_alias}\0{cycle_id}\0{observed_at.isoformat()}".encode()
                ).hexdigest(),
                policy=self._frozen_plan.policy,
                policy_identity=sha256(
                    repr((self._frozen_plan.plan_id, self._frozen_plan.revision, self._frozen_plan.policy)).encode()
                ).hexdigest(),
                policy_revision=str(self._frozen_plan.revision),
                selected_version_ids=frozenset(self._frozen_plan.selected_version_ids),
            )
            # The caller invokes cycles only for an active Paper session. Stop
            # and pause affect worker entries; snapshots still require fresh,
            # reconciled broker and durable facts before Risk is reachable.
            gates = PortfolioExecutionGates(
                reconciliation_clear=True,
                execution_clear=True,
                paper_clear=True,
                live_kill_clear=True,
                live_recovery_clear=True,
                ledger_readable=True,
            )
            result = self._operations.evaluate_cycle(gates=gates, request=request)
            if result is None:
                raise RuntimeError("portfolio Paper execution gates are closed")
        except Exception:
            self._publish_view_safely()
            raise
        self._publish_view_safely()
        return result

    def _publish_view_safely(self) -> None:
        try:
            self._publish_operations_view()
        except Exception:
            # Presentation is a read-only observer. A broken table update must
            # not change the portfolio/Risk/Execution outcome.
            pass


__all__ = ["PortfolioPaperCycleDriver"]
