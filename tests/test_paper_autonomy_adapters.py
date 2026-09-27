from __future__ import annotations

from us_quant.desktop_v2.orchestration.autonomy.facts import (
    PaperAutonomyRuntimeFactsAdapter,
    PaperAutonomyStartupFactsAdapter,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomySessionProvenance,
)
from us_quant.trading.runtime.preflight import PaperLaunchAuthorization


def test_runtime_facts_are_fresh_and_active_restart_is_unknown():
    state = {
        "active": True,
        "ready": False,
        "running": True,
        "paused": False,
    }
    adapter = PaperAutonomyRuntimeFactsAdapter(
        shutting_down=lambda: False,
        preparation_active=lambda: False,
        preparation_ready=lambda: state["ready"],
        launch_in_flight=lambda: False,
        session_running=lambda: state["running"],
        session_paused=lambda: state["paused"],
        manual_recovery_required=lambda: False,
        finalization_pending=lambda: False,
        paper_ownership_consistent=lambda: True,
    )

    initial = adapter.facts()
    assert initial.session_provenance is PaperAutonomySessionProvenance.UNKNOWN
    adapter.on_launch_authorization_published(PaperLaunchAuthorization.MANUAL)
    manual = adapter.facts()
    assert manual.session_provenance is PaperAutonomySessionProvenance.MANUAL
    state["running"] = False
    state["paused"] = True
    state["ready"] = True
    paused = adapter.facts()
    assert paused.session_paused and paused.preparation_ready
    assert paused.session_provenance is PaperAutonomySessionProvenance.MANUAL
    adapter.on_session_finalized()
    state["paused"] = False
    assert adapter.facts().session_provenance is PaperAutonomySessionProvenance.NONE


def test_runtime_facts_record_autonomous_source_and_ownership_truth():
    adapter = PaperAutonomyRuntimeFactsAdapter(
        shutting_down=lambda: False,
        preparation_active=lambda: False,
        preparation_ready=lambda: False,
        launch_in_flight=lambda: False,
        session_running=lambda: True,
        session_paused=lambda: False,
        manual_recovery_required=lambda: False,
        finalization_pending=lambda: False,
        paper_ownership_consistent=lambda: False,
    )
    adapter.on_launch_authorization_published(PaperLaunchAuthorization.AUTONOMOUS)

    facts = adapter.facts()

    assert facts.session_provenance is PaperAutonomySessionProvenance.AUTONOMOUS
    assert not facts.paper_ownership_consistent


def test_startup_facts_are_read_once_and_unknown_is_preserved():
    reads = {"orders": 0}

    def read_orders():
        reads["orders"] += 1
        return None

    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        action_store_readable=lambda: True,
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=read_orders,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    )

    first = adapter.startup_facts()
    second = adapter.startup_facts()

    assert first is second
    assert first.open_broker_orders is None
    assert not first.proven_safe
    assert reads["orders"] == 1
