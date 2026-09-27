from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"
TRADING = SRC / "trading"
CORE = (
    TRADING / "application" / "risk.py",
    TRADING / "application" / "execution.py",
    TRADING / "runtime" / "dispatch.py",
    TRADING / "runtime" / "trading.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _python_files(path: Path) -> list[Path]:
    return sorted(path.rglob("*.py"))


def _imports_environment(node: ast.AST) -> bool:
    return any(
        (isinstance(item, ast.Name) and item.id == "Environment")
        or (
            isinstance(item, ast.ImportFrom)
            and any(alias.name == "Environment" for alias in item.names)
        )
        for item in ast.walk(node)
    )


def _contains_environment_branch(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Match):
            if any(
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "Environment"
                for child in ast.walk(item.subject)
            ):
                return True
        if isinstance(item, (ast.If, ast.IfExp, ast.Compare)):
            test = item.test if hasattr(item, "test") else item
            if any(
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "Environment"
                for child in ast.walk(test)
            ):
                return True
            if any(
                isinstance(child, ast.Attribute)
                and child.attr in {"is_live", "is_paper", "is_backtest"}
                for child in ast.walk(test)
            ):
                return True
    return False


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_environment_vocabulary_has_one_canonical_enum() -> None:
    definitions = []
    for path in _python_files(SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name == "Environment":
                definitions.append(path.relative_to(SRC).as_posix())
    assert definitions == ["trading/domain/common.py"]


def test_shared_execution_core_has_no_environment_import_or_branch() -> None:
    for path in CORE:
        tree = _tree(path)
        assert not _imports_environment(tree), path
        assert not _contains_environment_branch(tree), path
        assert not any(
            isinstance(node, ast.Attribute)
            and node.attr == "environment"
            for node in ast.walk(tree)
        ), path


def test_runtime_status_and_reason_literals_are_provider_neutral() -> None:
    forbidden = (
        "ibkr paper", "paper order", "paper 订单", "paper 限价", "paper 回报",
        "live order", "live 订单",
    )
    for path in (TRADING / "runtime" / "dispatch.py", TRADING / "runtime" / "trading.py"):
        tree = _tree(path)
        literals = [
            node.value.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not [text for text in literals if any(token in text for token in forbidden)], path


def test_canonical_risk_execution_and_runtime_authorities_are_not_forked() -> None:
    expected = {
        "RiskApplication": "application/risk.py",
        "ExecutionApplication": "application/execution.py",
        "TradingRuntime": "runtime/trading.py",
    }
    actual: dict[str, list[str]] = {name: [] for name in expected}
    for path in _python_files(TRADING):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name in expected:
                actual[node.name].append(path.relative_to(TRADING).as_posix())
    assert actual == {name: [path] for name, path in expected.items()}


def test_order_dispatch_has_one_risk_to_execution_seam() -> None:
    path = TRADING / "runtime" / "dispatch.py"
    calls = [node for node in ast.walk(_tree(path)) if isinstance(node, ast.Call)]
    risk_calls = [
        node for node in calls
        if isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "risk"
    ]
    assert len(risk_calls) == 1
    assert sum(_call_name(node) == "submit_approved" for node in calls) == 1


def test_uncertain_submission_handler_does_not_retry() -> None:
    tree = _tree(TRADING / "application" / "execution.py")
    handlers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ExecutionSubmissionUncertain"
    ]
    assert len(handlers) == 1
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"submit", "reserve"}
        for node in ast.walk(handlers[0])
    )


def test_order_dispatch_has_no_refusal_retry_or_fallback_call() -> None:
    tree = _tree(TRADING / "runtime" / "dispatch.py")
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(_call_name(node) == "submit_approved" for node in calls) == 1


def test_broker_execution_port_has_exact_provider_neutral_surface() -> None:
    port = _tree(TRADING / "ports" / "broker_execution.py")
    cls = next(node for node in port.body if isinstance(node, ast.ClassDef) and node.name == "BrokerExecutionPort")
    methods = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert methods == {"connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"}


def test_composition_is_the_only_production_adapter_construction_owner() -> None:
    owners = []
    for path in _python_files(SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and _call_name(node) == "IBKRExecutionAdapter":
                owners.append(path.relative_to(SRC).as_posix())
    assert owners == ["trading/composition/execution.py"]


def test_production_broker_submit_and_cancel_are_adapter_owned() -> None:
    expected = "trading/adapters/ibkr/execution.py"
    for method in ("placeOrder", "cancelOrder"):
        owners = []
        for path in _python_files(SRC):
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Call) and _call_name(node) == method:
                    owners.append(path.relative_to(SRC).as_posix())
        assert owners == [expected], (method, owners)


def test_desktop_only_uses_the_gated_factory_and_no_adapter() -> None:
    tree = _tree(SRC / "desktop.py")
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(_call_name(node) == "build_execution_candidate_factory" for node in calls) == 1
    assert not any(_call_name(node) in {"IBKRExecutionAdapter", "_build_paper_execution_candidate"} for node in calls)
    assert not any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "IBKRExecutionAdapter" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_paper_service_remains_environment_blind() -> None:
    tree = _tree(TRADING / "application" / "paper" / "service.py")
    assert not _imports_environment(tree)
    assert not any(isinstance(node, ast.Name) and node.id == "live_trading_enabled" for node in ast.walk(tree))


def test_composition_selects_only_current_paper_candidate() -> None:
    tree = _tree(TRADING / "composition" / "execution.py")
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "build_execution_candidate_factory" in names
    assert "_build_paper_execution_candidate" in names
    assert not any("live" in name.casefold() for name in names)
    factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_execution_candidate_factory")
    calls = [node for node in ast.walk(factory) if isinstance(node, ast.Call)]
    assert sum(_call_name(node) == "_build_paper_execution_candidate" for node in calls) == 1


def test_current_ibkr_adapter_keeps_paper_boundary() -> None:
    source = (TRADING / "adapters" / "ibkr" / "execution.py").read_text(encoding="utf-8")
    assert "config.port != 4002" in source
    assert 'startswith("DU")' in source
    assert "paper_order_submission_enabled" in source
    assert "whole-share" in source.lower()
