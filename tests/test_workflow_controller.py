from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from us_quant.workflow_controller import (
    MarketAccountController,
    ResearchWorkflowController,
    ShadowWorkflowController,
    WorkflowController,
)
from us_quant.trading.runtime.workflow_state import ExecutionLease, ExecutionLeaseManager, WorkflowStateError


def test_research_transitions_are_explicit_and_snapshot_is_read_only() -> None:
    controller = ResearchWorkflowController()

    controller.begin("scan")
    assert controller.snapshot.busy
    assert controller.snapshot.stage == "scan"
    with pytest.raises(WorkflowStateError):
        controller.begin("backtest")
    controller.complete("Scan complete.")

    assert controller.snapshot.busy is False
    assert controller.snapshot.stage == "COMPLETE"
    with pytest.raises(FrozenInstanceError):
        controller.snapshot.status_message = "changed"  # type: ignore[misc]


def test_shadow_duplicate_transitions_fail_closed() -> None:
    controller = ShadowWorkflowController(leases=ExecutionLeaseManager())

    controller.start()
    with pytest.raises(WorkflowStateError):
        controller.start()
    controller.stop()
    with pytest.raises(WorkflowStateError):
        controller.stop()


def test_aggregate_shares_one_lease_between_paper_and_shadow() -> None:
    controller = WorkflowController()

    controller.shadow.start()
    assert controller.snapshot().lease is ExecutionLease.SHADOW
    controller.shadow.stop()
    controller.paper.begin_preparing()
    controller.paper.mark_ready()

    # Paper can reserve the shared lease before its caller connects a service.
    from us_quant.auto_launch import build_auto_launch_plan
    from decimal import Decimal

    plan = build_auto_launch_plan(
        attempt_id=1,
        strategy_version_id="strategy@1",
        parameter_hash="parameters",
        candidate_symbols=("AAA",),
        requested_capital_limit=Decimal("1000"),
    )
    controller.paper.begin_connecting(plan)
    with pytest.raises(WorkflowStateError):
        controller.shadow.start()


def test_aggregate_snapshot_combines_paper_shadow_account_and_market_truth() -> None:
    controller = WorkflowController()
    controller.market_account.update(True, True, "Account and market ready.")
    controller.shadow.start()

    snapshot = controller.snapshot()

    assert snapshot.lease is ExecutionLease.SHADOW
    assert snapshot.shadow_active is True
    assert snapshot.account_ready is True
    assert snapshot.market_ready is True
    assert snapshot.status_message == "IDLE"


def test_market_account_controller_only_publishes_readiness() -> None:
    controller = MarketAccountController()
    controller.update(True, False, "Market data delayed.")

    assert controller.snapshot.account_ready is True
    assert controller.snapshot.market_ready is False
    assert controller.snapshot.status_message == "Market data delayed."
