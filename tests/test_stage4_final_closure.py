"""Cross-stage architecture closure for Stage 4 Live canary boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.live_safety import (
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveSafetyRecord,
)
from us_quant.trading.ports.broker_execution import BrokerExecutionPort
from us_quant.trading.runtime.dispatch import OrderDispatch
from us_quant.trading.runtime.trading import TradingRuntime


_SRC = Path(__file__).resolve().parents[1] / "src" / "us_quant"
_AUTHORITY_MODULES = {
    "RiskApplication": "trading/application/risk.py",
    "ExecutionApplication": "trading/application/execution.py",
    "OrderDispatch": "trading/runtime/dispatch.py",
    "TradingRuntime": "trading/runtime/trading.py",
    "Environment": "trading/domain/common.py",
}


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def _call_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Attribute):
            names.append(function.attr)
        elif isinstance(function, ast.Name):
            names.append(function.id)
    return names


def test_stage4_has_exactly_one_shared_authority_stack_and_environment() -> None:
    expected = {
        name: f"us_quant.{relative[:-3].replace('/', '.')}"
        for name, relative in _AUTHORITY_MODULES.items()
    }
    definitions = {name: [] for name in expected}
    for path in _python_files(_SRC):
        module = f"us_quant.{path.relative_to(_SRC).with_suffix('').as_posix().replace('/', '.')}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(module)

    for name, owner in expected.items():
        assert definitions[name] == [owner], (name, definitions[name])

    for authority in (
        RiskApplication,
        ExecutionApplication,
        OrderDispatch,
        TradingRuntime,
    ):
        assert authority.__module__ == expected[authority.__name__]


def test_stage4_preserves_the_seven_method_port_and_adapter_ownership() -> None:
    methods = [
        name
        for name, value in vars(BrokerExecutionPort).items()
        if not name.startswith("_") and callable(value)
    ]
    assert methods == [
        "connect",
        "disconnect",
        "reserve",
        "submit",
        "cancel",
        "events",
        "fills",
    ]

    construction_owners: list[str] = []
    broker_api_owners: dict[str, set[str]] = {
        "placeOrder": set(),
        "cancelOrder": set(),
    }
    for path in _python_files(_SRC):
        relative = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {
                    "IBKRExecutionAdapter",
                    "IBKRLiveExecutionAdapter",
                }:
                    construction_owners.append(relative)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in broker_api_owners
            ):
                broker_api_owners[node.func.attr].add(relative)

    assert construction_owners
    assert set(construction_owners) == {"trading/composition/execution.py"}
    assert broker_api_owners == {
        "placeOrder": {"trading/adapters/ibkr/execution.py", "trading/adapters/ibkr/live_execution.py"},
        "cancelOrder": {"trading/adapters/ibkr/execution.py", "trading/adapters/ibkr/live_execution.py"},
    }

    desktop = ast.parse((_SRC / "desktop.py").read_text(encoding="utf-8"))
    desktop_names = {
        node.id for node in ast.walk(desktop) if isinstance(node, ast.Name)
    }
    assert "IBKRExecutionAdapter" not in desktop_names
    assert "IBKRLiveExecutionAdapter" not in desktop_names


def test_stage4_shared_authorities_are_environment_blind() -> None:
    for relative in (
        "trading/application/risk.py",
        "trading/application/execution.py",
        "trading/runtime/dispatch.py",
        "trading/runtime/trading.py",
    ):
        tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"))
        offending = [
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id.lower() in {"environment", "is_live", "live_trading_enabled"}
        ]
        assert offending == [], (relative, offending)
    assert Environment.LIVE.value == "live"


def test_stage4_execution_keeps_durable_reservation_before_submit_and_never_retries() -> None:
    tree = ast.parse(
        (_SRC / "trading/application/execution.py").read_text(encoding="utf-8")
    )
    submit_method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_submit"
    )
    call_lines: dict[str, int] = {}
    for node in ast.walk(submit_method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_lines.setdefault(node.func.attr, node.lineno)
    assert call_lines["reserve"] < call_lines["record_intent"] < call_lines["submit"]

    uncertain_handler = next(
        node
        for node in ast.walk(submit_method)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ExecutionSubmissionUncertain"
    )
    assert "submit" not in _call_names(uncertain_handler)
    assert "ExecutionSubmissionUncertain" in _call_names(uncertain_handler)


def test_stage4_restart_is_unarmed_and_default_limits_cannot_authorize() -> None:
    durable = LiveSafetyRecord()
    process_state = LiveAuthorizationState(
        durable.authorization, durable.kill_latch, durable.recovery_latch
    )
    assert durable.revision == 0
    assert durable.authorization is None
    assert not durable.kill_latch.is_latched
    assert not process_state.session_armed
    assert not process_state.after_restart().session_armed
    assert not (set(LiveSafetyRecord.__dataclass_fields__) & {
        "session_armed",
        "session_arm_id",
        "session_arm_revision",
    })

    limits = LiveCanaryLimits()
    assert limits.capital_limit == 0
    assert limits.max_order_notional == 0
    assert limits.max_daily_loss == 0
    assert limits.blockers()


def test_stage4_execution_path_has_no_ai_or_broker_fallback_dependency() -> None:
    guarded_modules = (
        "trading/application/risk.py",
        "trading/application/execution.py",
        "trading/runtime/dispatch.py",
        "trading/runtime/trading.py",
        "trading/composition/execution.py",
    )
    forbidden_import_prefixes = ("openai", "langchain", "llama_index")
    for relative in guarded_modules:
        tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        offending = [
            name
            for name in imports
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_import_prefixes)
        ]
        assert offending == [], (relative, offending)

    composition = ast.parse(
        (_SRC / "trading/composition/execution.py").read_text(encoding="utf-8")
    )
    assert "build_live_execution_candidate_factory" in {
        node.name for node in composition.body if isinstance(node, ast.FunctionDef)
    }
    assert "build_execution_candidate_factory" in {
        node.name for node in composition.body if isinstance(node, ast.FunctionDef)
    }
