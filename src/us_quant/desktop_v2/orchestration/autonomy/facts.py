"""Fresh Paper facts and one-shot startup classification for Desktop."""

from __future__ import annotations

from collections.abc import Callable

from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyRuntimeFacts,
    PaperAutonomySessionProvenance,
    PaperAutonomyStartupFacts,
)
from us_quant.trading.runtime.preflight import PaperLaunchAuthorization


class PaperAutonomyRuntimeFactsAdapter:
    """Project public Paper facts without retaining phase or session objects."""

    def __init__(
        self,
        *,
        shutting_down: Callable[[], bool],
        preparation_active: Callable[[], bool],
        preparation_ready: Callable[[], bool],
        launch_in_flight: Callable[[], bool],
        session_running: Callable[[], bool],
        session_paused: Callable[[], bool],
        manual_recovery_required: Callable[[], bool],
        finalization_pending: Callable[[], bool],
        paper_ownership_consistent: Callable[[], bool],
    ) -> None:
        self._shutting_down = shutting_down
        self._preparation_active = preparation_active
        self._preparation_ready = preparation_ready
        self._launch_in_flight = launch_in_flight
        self._session_running = session_running
        self._session_paused = session_paused
        self._manual_recovery_required = manual_recovery_required
        self._finalization_pending = finalization_pending
        self._paper_ownership_consistent = paper_ownership_consistent
        self._current_launch_source: PaperAutonomySessionProvenance | None = None

    def on_launch_authorization_published(
        self, authorization: PaperLaunchAuthorization
    ) -> None:
        self._current_launch_source = (
            PaperAutonomySessionProvenance.AUTONOMOUS
            if authorization is PaperLaunchAuthorization.AUTONOMOUS
            else PaperAutonomySessionProvenance.MANUAL
        )

    def on_session_finalized(self) -> None:
        self._current_launch_source = None

    def facts(self) -> PaperAutonomyRuntimeFacts:
        running = bool(self._session_running())
        paused = bool(self._session_paused())
        active = running or paused
        provenance = (
            self._current_launch_source
            if active and self._current_launch_source is not None
            else (
                PaperAutonomySessionProvenance.UNKNOWN
                if active
                else PaperAutonomySessionProvenance.NONE
            )
        )
        return PaperAutonomyRuntimeFacts(
            shutting_down=bool(self._shutting_down()),
            preparation_active=bool(self._preparation_active()),
            preparation_ready=bool(self._preparation_ready()),
            launch_in_flight=bool(self._launch_in_flight()),
            session_running=running,
            session_paused=paused,
            session_provenance=provenance,
            manual_recovery_required=bool(self._manual_recovery_required()),
            finalization_pending=bool(self._finalization_pending()),
            paper_ownership_consistent=bool(
                self._paper_ownership_consistent()
            ),
        )


class PaperAutonomyStartupFactsAdapter:
    """Read process-start facts through narrow injected readers exactly once."""

    def __init__(
        self,
        *,
        intent_store_readable: Callable[[], bool],
        unresolved_action_count: Callable[[], int | None],
        recovery_authorization_valid: Callable[[], bool | None],
        broker_state_known: Callable[[], bool],
        account_identity_known: Callable[[], bool],
        open_broker_orders: Callable[[], int | None],
        broker_positions: Callable[[], int | None],
        unreconciled_rows: Callable[[], int | None],
        paper_ownership_clear: Callable[[], bool],
        manual_recovery_required: Callable[[], bool],
    ) -> None:
        self._readers = (
            intent_store_readable,
            unresolved_action_count,
            recovery_authorization_valid,
            broker_state_known,
            account_identity_known,
            open_broker_orders,
            broker_positions,
            unreconciled_rows,
            paper_ownership_clear,
            manual_recovery_required,
        )
        self._facts: PaperAutonomyStartupFacts | None = None

    def startup_facts(self) -> PaperAutonomyStartupFacts:
        if self._facts is not None:
            return self._facts
        (
            intent_readable,
            unresolved_count,
            recovery_authorization_valid,
            broker_known,
            account_known,
            open_orders,
            positions,
            unreconciled,
            ownership_clear,
            recovery_required,
        ) = self._readers
        unresolved_count = unresolved_count()
        try:
            authorization_valid = recovery_authorization_valid()
        except Exception:  # noqa: BLE001 - authorization must fail closed
            authorization_valid = None
        self._facts = PaperAutonomyStartupFacts(
            intent_store_readable=bool(intent_readable()),
            action_store_readable=unresolved_count is not None,
            unresolved_action_count=unresolved_count,
            recovery_authorization_valid=authorization_valid,
            broker_state_known=bool(broker_known()),
            account_identity_known=bool(account_known()),
            open_broker_orders=open_orders(),
            broker_positions=positions(),
            unreconciled_rows=unreconciled(),
            paper_ownership_clear=bool(ownership_clear()),
            manual_recovery_required=bool(recovery_required()),
        )
        return self._facts


__all__ = [
    "PaperAutonomyRuntimeFactsAdapter",
    "PaperAutonomyStartupFactsAdapter",
]
