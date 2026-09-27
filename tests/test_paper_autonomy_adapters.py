from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from us_quant.desktop_v2.orchestration.autonomy.facts import (
    PaperAutonomyRuntimeFactsAdapter,
    PaperAutonomyStartupFactsAdapter,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomySessionProvenance,
)
from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
)
from us_quant.trading.runtime.preflight import PaperLaunchAuthorization

_DAY = date(2026, 9, 28)
_NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


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
    reads = {"orders": 0, "actions": 0}

    def read_orders():
        reads["orders"] += 1
        return None

    def read_action_count():
        reads["actions"] += 1
        return 0

    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=read_action_count,
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
    assert reads["actions"] == 1


def test_startup_action_count_blocks_when_previous_action_is_unresolved():
    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=lambda: 1,
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=lambda: 0,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    )

    facts = adapter.startup_facts()

    assert facts.unresolved_action_count == 1
    assert facts.action_store_readable
    assert not facts.proven_safe


def test_startup_action_store_failure_is_unknown_and_unsafe():
    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=lambda: None,
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=lambda: 0,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    )

    facts = adapter.startup_facts()

    assert facts.unresolved_action_count is None
    assert not facts.action_store_readable
    assert not facts.proven_safe


@pytest.mark.parametrize(
    "action", [PaperAutonomyActionType.START, PaperAutonomyActionType.PREPARE]
)
def test_previous_process_requested_start_or_prepare_blocks_startup(
    tmp_path, action
):
    actions = SQLitePaperAutonomyActionRepository(tmp_path / "actions.sqlite3")
    key = f"old-process-{action.value}"
    actions.claim(
        PaperAutonomyActionRecord(
            action_key=key,
            intent_revision=3,
            trading_day=_DAY,
            action=action,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=_NOW,
            completed_at=None,
            detail="owner request before process stopped",
        )
    )
    actions.mark_requested(action_key=key, detail="owner admitted request")
    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=lambda: len(actions.unresolved()),
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=lambda: 0,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    )

    facts = adapter.startup_facts()

    assert facts.unresolved_action_count == 1
    assert not facts.proven_safe
    assert actions.unresolved()[0].status is PaperAutonomyActionStatus.REQUESTED


@pytest.mark.parametrize(
    "action", [PaperAutonomyActionType.START, PaperAutonomyActionType.PREPARE]
)
def test_terminal_historical_action_does_not_block_startup(tmp_path, action):
    actions = SQLitePaperAutonomyActionRepository(tmp_path / "actions.sqlite3")
    key = f"old-process-{action.value}"
    actions.claim(
        PaperAutonomyActionRecord(
            action_key=key,
            intent_revision=3,
            trading_day=_DAY,
            action=action,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=_NOW,
            completed_at=None,
            detail="owner request before process stopped",
        )
    )
    actions.mark_requested(action_key=key, detail="owner admitted request")
    actions.complete(
        action_key=key,
        status=PaperAutonomyActionStatus.SUCCEEDED,
        completed_at=_NOW,
        detail="canonical publication observed",
    )
    adapter = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=lambda: len(actions.unresolved()),
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=lambda: 0,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    )

    facts = adapter.startup_facts()

    assert facts.unresolved_action_count == 0
    assert facts.proven_safe
