from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"
TRADING = SRC / "trading"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _python_files(path: Path):
    return sorted(path.rglob("*.py"))


def test_environment_vocabulary_has_one_canonical_enum() -> None:
    definitions = []
    for path in _python_files(SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name == "Environment":
                definitions.append(path.relative_to(SRC).as_posix())
    assert definitions == ["trading/domain/common.py"]


def test_risk_application_does_not_import_or_branch_on_environment() -> None:
    source = _source(TRADING / "application" / "risk.py")
    assert "Environment" not in source
    assert "is_live" not in source
    assert "is_paper" not in source


def test_execution_application_does_not_import_or_branch_on_environment() -> None:
    source = _source(TRADING / "application" / "execution.py")
    assert "Environment" not in source
    assert "is_live" not in source
    assert "is_paper" not in source


def test_order_dispatch_does_not_import_or_branch_on_environment() -> None:
    source = _source(TRADING / "runtime" / "dispatch.py")
    assert "Environment" not in source
    assert "is_live" not in source
    assert "is_paper" not in source


def test_trading_runtime_does_not_import_or_branch_on_environment() -> None:
    source = _source(TRADING / "runtime" / "trading.py")
    assert "Environment" not in source
    assert "is_live" not in source
    assert "is_paper" not in source


def test_canonical_risk_execution_and_runtime_authorities_are_not_forked() -> None:
    forbidden = {
        "LiveRiskApplication",
        "LiveExecutionApplication",
        "LiveTradingRuntime",
    }
    found = {
        node.name
        for path in _python_files(TRADING)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef) and node.name in forbidden
    }
    assert found == set()


def test_broker_execution_port_has_no_provider_specific_methods() -> None:
    port = _tree(TRADING / "ports" / "broker_execution.py")
    cls = next(
        node
        for node in port.body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerExecutionPort"
    )
    methods = {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {
        "connect",
        "disconnect",
        "reserve",
        "submit",
        "cancel",
        "events",
        "fills",
    }
    assert not any(
        method.startswith(("paper_", "live_", "ibkr_")) for method in methods
    )


def test_environment_knowledge_is_limited_to_domain_rule_and_composition() -> None:
    authority = [
        TRADING / "application" / "risk.py",
        TRADING / "application" / "execution.py",
        TRADING / "runtime" / "dispatch.py",
        TRADING / "runtime" / "trading.py",
    ]
    assert all("Environment" not in _source(path) for path in authority)
    assert "Environment" in _source(
        TRADING / "domain" / "execution_environment.py"
    )
    assert "Environment" in _source(TRADING / "composition" / "execution.py")


def test_desktop_only_injects_the_gated_factory() -> None:
    source = _source(SRC / "desktop.py")
    assert "build_execution_candidate_factory(" in source
    assert "build_execution_candidate," not in source
    assert "IBKRExecutionAdapter" not in source
    assert "Environment.LIVE" not in source
    assert "Environment.PAPER" not in source


def test_paper_trading_service_is_environment_blind() -> None:
    source = _source(TRADING / "application" / "paper" / "service.py")
    assert "Environment" not in source
    assert "live_trading_enabled" not in source
    assert "if LIVE" not in source


def test_production_factory_has_no_raw_builder_or_paper_fallback() -> None:
    source = _source(TRADING / "composition" / "execution.py")
    tree = _tree(TRADING / "composition" / "execution.py")
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "build_execution_candidate_factory" in functions
    assert "build_execution_candidate" not in functions
    assert "_build_paper_execution_candidate" in functions
    assert "evaluate_execution_deployment" in source


def test_current_ibkr_execution_adapter_keeps_paper_boundary() -> None:
    source = _source(TRADING / "adapters" / "ibkr" / "execution.py")
    assert "config.port != 4002" in source
    assert "DU" in source
    assert "paper_order_submission_enabled" in source
    assert "whole-share" in source.lower()


def test_no_production_live_execution_adapter_exists() -> None:
    adapters = TRADING / "adapters"
    classes = {
        node.name
        for path in _python_files(adapters)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
    }
    assert "LiveExecutionAdapter" not in classes
