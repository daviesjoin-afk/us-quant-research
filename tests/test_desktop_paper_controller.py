"""Desktop boundary tests for the Paper workflow controller integration."""

from __future__ import annotations

import inspect

from us_quant.desktop import MainWindow


def _source(name: str) -> str:
    return inspect.getsource(getattr(MainWindow, name))


def test_normal_paper_ingress_delegates_once_to_workflow_controller() -> None:
    expected = {
        "_pause_auto_quant_entries": "paper_workflow.set_entries_paused(True)",
        "_resume_auto_quant_entries": "paper_workflow.set_entries_paused(False)",
        "_stop_auto_quant": "paper_workflow.request_stop",
        "_poll_auto_quant_orders": "paper_workflow.poll()",
        "_stream_snapshot_received": "paper_workflow.on_stream(result)",
    }
    for name, delegation in expected.items():
        source = _source(name)
        assert source.count(delegation) == 1
    stream_source = _source("_stream_snapshot_received")
    assert "_poll_auto_quant_orders()" not in stream_source


def test_manual_resume_cannot_resubmit_pending_orders() -> None:
    source = _source("_resume_auto_quant_from_reconciliation")
    assert "paper_workflow.confirm_manual_resume" in source
    assert "resubmit_pending_intent" not in source
    assert ".submit(" not in source


def test_shadow_uses_the_shared_lease_and_releases_it_on_stop() -> None:
    assert "self.shadow_workflow.start()" in _source("_start_shadow")
    assert "self.shadow_workflow.stop()" in _source("_stop_shadow")
    assert "self.shadow_workflow.stop()" in _source("closeEvent")


def test_close_blocks_unfinalized_paper_before_any_disconnect() -> None:
    source = _source("closeEvent")
    # The gate now reads the controller through the Paper trading facade; the
    # guarantee is unchanged -- an unfinalized session is refused before any
    # disconnect runs.
    assert "self.paper_trading.is_finalized()" in source
    assert source.index("self.paper_trading.is_finalized()") < source.index(
        "self.paper_trading.disconnect()"
    )


def test_unarmed_launch_rejection_is_controller_scoped() -> None:
    source = _source("_start_auto_quant")
    assert "paper_workflow.begin_connecting(plan)" in source
    assert "paper_workflow.reject_connecting(plan)" in source
    assert "paper_workflow.reject_connecting(plan)" in _source(
        "_reject_unpublished_auto_candidate"
    )


def test_async_preparation_and_reconciliation_fail_closed() -> None:
    assert "on_failure=self._auto_candidate_preparation_failed" in _source(
        "_prepare_auto_quant_candidates"
    )
    assert "paper_workflow.cancel_preparing()" in _source(
        "_auto_candidate_preparation_failed"
    )
    reconnect = _source("_reconnect_auto_order_service")
    assert "complete_manual_reconciliation(attempt_id)" in reconnect
    assert "self._auto_order_reconciliation_failed(" in reconnect
    assert "if not started:" in reconnect
    assert "fail_manual_reconciliation(attempt_id)" in reconnect
    assert "paper_workflow.fail_manual_reconciliation(attempt_id)" in _source(
        "_auto_order_reconciliation_failed"
    )


def test_manual_resume_requires_current_evidence_and_runs_off_ui_thread() -> None:
    source = _source("_resume_auto_quant_from_reconciliation")
    assert "PaperWorkflowPhase.RECONCILING_READY" in source
    assert "reconciliation_evidence" in source
    assert "confirm_manual_resume(evidence_id)" in source
    assert "self._start_task(" in source


def test_snapshot_renderer_does_not_enable_manual_resume_from_engine_flags() -> None:
    """The render path draws facts; it never decides a recovery control.

    Manual resume is opened by the control publisher from the workflow phase and
    the presence of a proof, so a renderer that also enabled it would be a second
    writer for the same button -- the arrangement this migration removes.
    """

    source = _source("_render_auto_quant_snapshot")
    assert "resume_reconciliation" not in source
    assert "set_control_state" not in source
    assert "setEnabled" not in source


def test_finalization_proves_zero_state_before_disconnect_and_lease_release() -> None:
    source = _source("_start_paper_finalization_refresh")
    assert source.index("capture_finalization_evidence()") < source.index(
        "self.paper_trading.disconnect()"
    )
    assert source.index("self.paper_trading.disconnect()") < source.index(
        "confirm_finalization_after_disconnect"
    )
    assert "resource_group=\"broker\"" in source


def test_finalization_suppresses_desktop_poll_and_stream_ingress() -> None:
    poll_source = _source("_poll_auto_quant_orders")
    stream_source = _source("_stream_snapshot_received")
    assert poll_source.index("_paper_finalization_inflight") < poll_source.index(
        "paper_workflow.poll()"
    )
    assert stream_source.index("_paper_finalization_inflight") < stream_source.index(
        "paper_workflow.on_stream(result)"
    )
