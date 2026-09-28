from __future__ import annotations

import ast
import inspect
from pathlib import Path

from us_quant.trading.application.live_canary_execution import (
    LiveCanaryExecutionGuard,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "us_quant"
GUARD_SOURCE = SRC / "trading" / "application" / "live_canary_execution.py"
TRUTH_SOURCE = SRC / "trading" / "domain" / "live_canary.py"
COMPOSITION_SOURCE = SRC / "trading" / "composition" / "execution.py"


def test_canary_guard_keeps_the_frozen_broker_execution_surface() -> None:
    expected = {
        "connect",
        "disconnect",
        "reserve",
        "submit",
        "cancel",
        "events",
        "fills",
    }
    public_methods = {
        name
        for name, value in inspect.getmembers(LiveCanaryExecutionGuard, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == expected


def test_canary_application_and_truth_domain_do_not_import_broker_or_storage_adapters():
    forbidden = {
        "sqlite3",
        "ibapi",
        "us_quant.trading.adapters",
        "us_quant.trading.adapters.ibkr",
        "us_quant.trading.adapters.sqlite",
    }
    for path in (GUARD_SOURCE, TRUTH_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(
            module in forbidden
            or any(module.startswith(prefix + ".") for prefix in forbidden)
            for module in imported
        ), path


def test_only_the_dedicated_live_factory_wraps_live_adapter_with_guard():
    tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_live_execution_candidate_factory"
    )
    names = [
        node.func.id
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert names.count("IBKRLiveExecutionAdapter") == 1
    assert names.count("LiveCanaryExecutionGuard") == 1
    paper_factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_execution_candidate_factory"
    )
    paper_calls = [
        node.func.id
        for node in ast.walk(paper_factory)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "IBKRLiveExecutionAdapter" not in paper_calls
    assert "LiveCanaryExecutionGuard" not in paper_calls
