"""Cross-process tests proving restart reconstructs durable truth only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def run_boundary(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    helper = project_root / "tests" / "support" / "paper_crash_process.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(project_root / "src"), str(project_root / "tests")))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"

    def run(boundary):
        phase_a = subprocess.run(
            [sys.executable, str(helper), "a", str(tmp_path), boundary],
            cwd=project_root, env=environment, capture_output=True, text=True,
            timeout=45, check=False,
        )
        assert phase_a.returncode == 0, phase_a.stderr
        phase_a_report = json.loads(phase_a.stdout) if phase_a.stdout else {}
        # This is a second OS process, created only after phase A has exited.
        phase_b = subprocess.run(
            [sys.executable, str(helper), "b", str(tmp_path), boundary],
            cwd=project_root, env=environment, capture_output=True, text=True,
            timeout=45, check=False,
        )
        assert phase_b.returncode == 0, phase_b.stderr
        report = json.loads(phase_b.stdout)
        report["phase_a"] = phase_a_report
        return report

    return run


def test_durable_decision_without_order_truth_never_invents_submit(run_boundary):
    report = run_boundary("decision_only")
    assert report["before_decisions"] == 1
    assert report["after_decisions"] == 1
    assert report["local_orders"] == report["broker_orders"] == 0
    assert report["submit_calls_after_restart"] == 0
    assert report["portfolio_dispatch_calls_after_restart"] == 0
    assert report["risk_evaluations_after_restart"] == 0
    assert report["actions_recovered"] == [True]
    assert report["action_dispatch_halts"] == [True]
    assert report["restart_is_session_resume"] is False


def test_uncertain_submit_remains_fail_closed_after_process_restart(run_boundary):
    report = run_boundary("uncertain")
    assert report["local_orders"] == 1
    assert report["broker_orders"] == 1
    assert report["submit_calls_after_restart"] == 0
    assert report["portfolio_dispatch_calls_after_restart"] == 0
    assert report["risk_evaluations_after_restart"] == 0
    assert report["risk_outcomes"] == ["approved"]
    assert report["dispatch_recorded"] == [True]
    assert report["attributions"] == 1
    assert report["restart_is_session_resume"] is False


def test_intent_durable_submit_outcome_unknown_never_retries_after_restart(run_boundary):
    report = run_boundary("intent_unknown")
    assert report["local_orders"] == 1
    assert report["broker_orders"] == 0
    assert report["submit_calls_after_restart"] == 0
    assert report["portfolio_dispatch_calls_after_restart"] == 0
    assert report["risk_evaluations_after_restart"] == 0
    assert report["risk_outcomes"] == ["approved"]
    assert report["dispatch_recorded"] == [False]
    assert report["attributions"] == 0


def test_process_restart_never_arms_or_resumes_paper_automatically(run_boundary):
    report = run_boundary("decision_only")
    assert report["armed"] is False
    assert report["session_active"] is False
    assert report["restart_is_session_resume"] is False


def test_broker_local_order_disagreement_blocks_without_second_order(run_boundary):
    report = run_boundary("broker_only")
    assert report["local_orders"] == report["broker_orders"] == 1
    assert report["submit_calls_after_restart"] == 0
    assert report["portfolio_dispatch_calls_after_restart"] == 0
    assert report["risk_evaluations_after_restart"] == 0
    assert "unexplained_order" in report["reconciliation_blockers"]


def test_partial_fill_and_attribution_reconstruct_once_after_restart(run_boundary):
    report = run_boundary("partial")
    assert report["local_orders"] == report["attributions"] == report["fills"] == 1
    assert report["order_events"] == 1
    assert report["reconciliation_blockers"] == []
    assert report["submit_calls_after_restart"] == 0
    assert report["portfolio_dispatch_calls_after_restart"] == 0
    assert report["risk_evaluations_after_restart"] == 0
    assert report["reconciliation_positions"] == [{"symbol": "AAPL", "broker_quantity": "4", "attributed_quantity": 4}]
    assert report["reconciliation_accounting"] == [{"strategy_version_id": "crash-matrix-v1", "symbol": "AAPL", "quantity": 4, "filled_quantity": 4}]
    assert report["performance"] == report["phase_a"]["performance"]


def test_terminal_fill_reconciles_cleanly_after_restart(run_boundary):
    report = run_boundary("terminal")
    assert report["local_orders"] == report["attributions"] == report["fills"] == 1
    assert report["order_events"] == 1
    assert report["reconciliation_blockers"] == []
    assert report["submit_calls_after_restart"] == 0
    assert report["performance"] == report["phase_a"]["performance"]
    assert report["reconciliation_positions"] == [{"symbol": "AAPL", "broker_quantity": "10", "attributed_quantity": 10}]
    assert report["reconciliation_accounting"] == [{"strategy_version_id": "crash-matrix-v1", "symbol": "AAPL", "quantity": 10, "filled_quantity": 10}]


def test_unrelated_repository_recovery_error_fails_the_restart_process(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    helper = project_root / "tests" / "support" / "paper_crash_process.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(project_root / "src"), str(project_root / "tests")))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"

    phase_a = subprocess.run(
        [sys.executable, str(helper), "a", str(tmp_path), "decision_only"],
        cwd=project_root, env=environment, capture_output=True, text=True,
        timeout=45, check=False,
    )
    assert phase_a.returncode == 0, phase_a.stderr

    phase_b = subprocess.run(
        [sys.executable, str(helper), "b", str(tmp_path), "injected_recovery_error"],
        cwd=project_root, env=environment, capture_output=True, text=True,
        timeout=45, check=False,
    )
    assert phase_b.returncode != 0
    assert "injected repository recovery read failure" in phase_b.stderr
