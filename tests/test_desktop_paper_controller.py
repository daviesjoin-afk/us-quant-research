"""Desktop boundary tests for the Paper workflow controller integration."""

from __future__ import annotations

import inspect

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.execution import ExecutionOrchestrator
from us_quant.desktop_v2.orchestration.paper import PaperOrchestrator


def _source(name: str) -> str:
    return inspect.getsource(getattr(MainWindow, name))


def _orchestrator_source(name: str) -> str:
    """One launch method on its owner since v2O-E1.

    The launch assertions below used to read ``MainWindow``; the sequence is the
    capability's now, so they read it there.  Deleting them instead would have
    silently dropped the safety claim they encode.
    """

    return inspect.getsource(getattr(PaperOrchestrator, name))


def _execution_source(name: str) -> str:
    """One execution-route method on its owner since G2-B.

    The candidate preparation and the session render used to be ``MainWindow``
    methods; the AutoQuant route is ``ExecutionOrchestrator``'s now, so a claim
    about it reads there rather than being dropped with the window's copy.
    """

    return inspect.getsource(getattr(ExecutionOrchestrator, name))


def test_normal_paper_ingress_delegates_once_to_workflow_controller() -> None:
    """Each active-session intent reaches the workflow exactly once, from the capability.

    v2O-E2 moved the pause/resume/stop controls, the watchdog poll and the market
    ingress into ``PaperOrchestrator``, so these read the capability -- and the
    fan-out assertion is the other half: the window hands the fact over instead of
    delegating on the session's behalf.
    """

    expected = {
        "pause": "self._workflow.set_entries_paused(True)",
        "resume": "self._workflow.set_entries_paused(False)",
        "stop": "self._workflow.request_stop",
        "poll": "self._workflow.poll()",
        "on_market_snapshot": "self._workflow.on_stream(snapshot)",
    }
    for name, delegation in expected.items():
        source = _orchestrator_source(name)
        assert source.count(delegation) == 1, name
    stream_source = _source("_on_market_snapshot_changed")
    assert "paper_orchestrator.on_market_snapshot(snapshot)" in stream_source
    assert "paper_workflow.on_stream" not in stream_source
    assert "paper_workflow.poll" not in stream_source


def test_manual_resume_cannot_resubmit_pending_orders() -> None:
    """Reconciliation confirmation resumes; it never re-submits or rebuilds an intent.

    v2O-E3 moved the confirmation's *sequencing* into the capability, so the claim reads
    there now.  The property is the one that was asserted of the window all along:
    pending broker rows are review evidence, and the only engine call this path can make
    is the workflow's own ``confirm_manual_resume``.
    """

    source = _orchestrator_source("confirm_reconciliation_resume")
    assert "self._workflow.confirm_manual_resume(evidence_id)" in source
    assert "resubmit_pending_intent" not in source
    assert ".submit(" not in source
    # And nothing in the path reconnects, arms or disconnects: the session's order port
    # is the one it was armed with.
    for forbidden in ("connect_active", "disconnect", "arm(", "reserve_candidate"):
        assert forbidden not in source, forbidden


def test_shadow_uses_the_shared_lease_and_releases_it_on_stop() -> None:
    """The lease is still shared with Paper; its holder moved in v2O-D.

    ``ShadowOrchestrator`` acquires and releases the same ``ExecutionLeaseManager``
    handle ``WorkflowController`` gives to the Paper workflow, so "Shadow and
    Paper cannot both hold execution" stays structural.

    Every teardown path hands the lease back through **one guarded helper** rather
    than calling ``lease.stop()`` inline.  That is a safety requirement, not
    tidiness: the lease is shared, so ``lease.active`` cannot tell this capability
    whether *it* is the holder, and releasing on that condition once let a
    duplicate start un-enforce the mutex while a simulation kept running.  See
    ``test_desktop_shadow_orchestration_architecture`` for the guards.
    """

    from us_quant.desktop_v2.orchestration.shadow import ShadowOrchestrator

    start = inspect.getsource(ShadowOrchestrator.start)
    stop = inspect.getsource(ShadowOrchestrator.stop)
    shutdown = inspect.getsource(ShadowOrchestrator.shutdown)
    release = inspect.getsource(ShadowOrchestrator._release_lease)
    assert "self._lease.start()" in start
    assert "self._release_lease()" in stop
    assert "self._release_lease()" in shutdown
    # The one place the lease is actually handed back, gated on this capability
    # holding it.  That the *condition* is never ``self._lease.active`` is pinned
    # by ``test_the_orchestrator_only_releases_a_lease_it_acquired`` in
    # ``test_desktop_shadow_orchestration_architecture`` -- asserting it here as a
    # substring would trip on the helper's own docstring, which names the bad
    # condition in order to explain why it is wrong.
    assert "self._lease.stop()" in release
    assert "if not self._holds_lease:" in release
    # And the window hands over the shared handle rather than composing its own.
    desktop_source = inspect.getsource(MainWindow)
    assert "lease=self.shadow_workflow" in desktop_source


def test_close_blocks_unfinalized_paper_before_any_disconnect() -> None:
    """An unfinalized session refuses the close before anything is torn down.

    Since v2O-E3 the *decision* is ``PaperOrchestrator.prepare_shutdown``'s and the
    release sequencing -- the only Paper disconnect there is -- lives beside it, behind
    the workflow's own ``finalize_if_safe`` gate.  The guarantee is unchanged: the close
    asks first, and a non-READY verdict stops it before shadow shutdown, before the
    supervisor and before any Paper disconnect.
    """

    close = _source("closeEvent")
    ask = close.index("self.paper_orchestrator.prepare_shutdown()")
    assert ask < close.index("self.shadow_orchestrator.shutdown()")
    assert ask < close.index("self.runtime_supervisor.shutdown()")
    assert "self.paper_trading.disconnect()" not in close
    assert "self.paper_trading.clear_active()" not in close

    # And the release it delegates to is gated on the workflow's own verdict, with the
    # slot reserved *before* the gate so a slot that cannot be accounted for refuses
    # while the lease is still held.
    release = _orchestrator_source("_release_paper_ownership_if_proven")
    reserve = release.index("self._paper_trading.reserve_active_release()")
    disconnect = release.index("self._paper_trading.disconnect()")
    finalize = release.index("if not self._workflow.finalize_if_safe():", reserve)
    clear = release.index("self._paper_trading.commit_active_release(reservation)")
    assert disconnect < reserve < finalize < clear


def test_unarmed_launch_rejection_is_controller_scoped() -> None:
    """The rejection is scoped to the attempt it belongs to, on its new owner.

    v2O-E1 moved the launch sequence into ``PaperOrchestrator``, so these read the
    capability.  The guarantee is the one that was asserted of the window all
    along: binding goes through ``begin_connecting`` (which is what takes PAPER),
    and an unarmed attempt is unwound through ``reject_connecting`` -- never by
    touching the lease or the active service directly.
    """

    start = _orchestrator_source("start")
    assert "self._workflow.begin_connecting(request.plan)" in start
    assert "self._workflow.reject_connecting(request.plan)" in start
    assert "self._workflow.reject_connecting(request.plan)" in _orchestrator_source(
        "_discard_candidate"
    )
    assert "self._workflow.reject_connecting(request.plan)" in _orchestrator_source(
        "_reject_without_candidate"
    )


def test_async_preparation_and_reconciliation_fail_closed() -> None:
    """Both asynchronous steps roll their own attempt back when the task is refused.

    v2O-E3 moved the reconciliation into the capability, so the assertions read there.
    G2-B moved the candidate preparation onto ``ExecutionOrchestrator``, so its half
    reads there -- and the claim is the fail-closed one at its new owner: an attempt
    that was never admitted hands PREPARING back instead of leaving a phase whose only
    exit is a task nobody ran, and a task that started and then failed gives back both
    what the attempt claimed.  Neither path reaches the workflow or interprets a phase
    to do it: the release goes through the capability's own delegated seam, and only
    when the capability says the preparation is active.
    """

    prepare = _execution_source("request_prepare")
    assert "on_failure=self._preparation_failed" in prepare
    assert "if not started:" in prepare
    assert "self._paper.cancel_preparation()" in prepare
    failed = _execution_source("_preparation_failed")
    assert "self._cancel_preparation_if_active()" in failed
    assert "self._set_launch_busy(False)" in failed
    cancel = _execution_source("_cancel_preparation_if_active")
    assert "self._paper.preparation_active" in cancel
    assert "self._paper.cancel_preparation()" in cancel
    for source in (prepare, failed, cancel):
        assert "PaperWorkflowPhase" not in source
        assert "paper_workflow" not in source
    reconcile = _orchestrator_source("reconcile")
    assert "complete_manual_reconciliation(attempt_id)" in reconcile
    assert "self._reconciliation_failed(" in reconcile
    assert "if not started:" in reconcile
    assert "fail_manual_reconciliation(attempt_id)" in reconcile
    assert "self._workflow.fail_manual_reconciliation(attempt_id)" in (
        _orchestrator_source("_reconciliation_failed")
    )
    # Reconnecting is evidence collection: the task must not reach anything that trades.
    assert "connect_active()" in reconcile
    for forbidden in ("resubmit", "placeOrder", "arm(", "request_stop"):
        assert forbidden not in reconcile, forbidden


def test_manual_resume_requires_current_evidence_and_runs_off_ui_thread() -> None:
    """The proof is read *after* the confirmation, and the engine work is a task.

    Reading it before the dialog is the failure this pins: a proof captured when the
    question was asked can be consumed or superseded while the operator reads it.  The
    capability therefore re-reads the phase and the evidence itself, freezes that id into
    the attempt's closure, and only then submits -- and it raises no fabricated result
    when the proof is missing.
    """

    source = _orchestrator_source("confirm_reconciliation_resume")
    assert "queries.reconciliation_resume_ready(" in source
    assert "self._workflow.reconciliation_evidence" in source
    assert "confirm_manual_resume(evidence_id)" in source
    assert "self._submit_task(" in source
    # The read order is the claim: the refusal comes first.
    assert source.index("reconciliation_resume_ready") < source.index(
        "self._submit_task("
    )
    # And the confirmation itself is presentation, on the window, with no orchestration.
    confirm = _source("_confirm_paper_reconciliation_resume")
    assert "QMessageBox.question(" in confirm
    assert "self.paper_orchestrator.confirm_reconciliation_resume()" in confirm
    for forbidden in (
        "reconciliation_evidence",
        "confirm_manual_resume",
        "evidence_id",
        "paper_workflow",
    ):
        assert forbidden not in confirm, forbidden


def test_snapshot_renderer_does_not_enable_manual_resume_from_engine_flags() -> None:
    """The render path draws facts; it never decides a recovery control.

    Manual resume is opened by the control publisher from the capability's own session
    facts, so a renderer that also enabled it would be a second writer for the same
    button -- the arrangement this migration removes.  G2-B moved the render onto
    ``ExecutionOrchestrator.refresh_current``, so the claim is made at its new owner.
    """

    source = _execution_source("refresh_current")
    assert "resume_reconciliation" not in source
    assert "set_control_state" not in source
    assert "setEnabled" not in source


def test_finalization_proves_zero_state_before_disconnect_and_lease_release() -> None:
    """The proof's order, asserted on its new owner.

    Evidence before disconnect (the proof reads the connection the session still holds)
    and disconnect before confirmation (the proof is only consumed once the callback
    thread has joined), on the broker resource group, with the busy dialog suppressed
    and the task marked shutdown-essential -- it is part of the close path itself.
    """

    source = _orchestrator_source("_start_finalization")
    assert source.index("capture_finalization_evidence()") < source.index(
        "self._paper_trading.disconnect()"
    )
    assert source.index("self._paper_trading.disconnect()") < source.index(
        "confirm_finalization_after_disconnect"
    )
    assert 'resource_group="broker"' in source
    assert "suppress_busy_message=True" in source
    assert "shutdown_essential=True" in source
    # And the proof releases nothing: only the workflow's own gate may do that.
    for forbidden in ("clear_active(", "release_paper(", "finalize_if_safe()"):
        assert forbidden not in source, forbidden


def test_finalization_suppresses_desktop_poll_and_stream_ingress() -> None:
    """Both entry points consult the proof *before* they do anything.

    The deferral is now a flag this capability owns rather than a provider read across
    the boundary, so the ordering is asserted where the flag lives: a poll or an ingress
    that ran first and consulted it second would interleave two readers of the same
    broker connection.
    """

    poll_source = _orchestrator_source("poll")
    stream_source = _orchestrator_source("on_market_snapshot")
    assert poll_source.index("self._finalization_inflight") < poll_source.index(
        "self._workflow.poll()"
    )
    assert stream_source.index("self._finalization_inflight") < stream_source.index(
        "self._workflow.on_stream(snapshot)"
    )
