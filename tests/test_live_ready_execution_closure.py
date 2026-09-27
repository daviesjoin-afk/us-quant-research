from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from test_live_ready_execution_core import (
    _dispatch,
    _proposal,
    _refusing_dispatch,
)
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.composition.execution import build_execution_candidate_factory
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.execution_environment import (
    ExecutionDeploymentBlocker,
    ExecutionDeploymentError,
    evaluate_execution_deployment,
)
from us_quant.trading.domain.risk import RiskDecision


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"
TRADING = SRC / "trading"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _files(path: Path) -> list[Path]:
    return sorted(path.rglob("*.py"))


def _adapter_config() -> IBKRConnectionConfig:
    return IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=191,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=3,
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_c1_environment_enum_is_singleton() -> None:
    matches = [
        path.relative_to(SRC).as_posix()
        for path in _files(SRC)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef) and node.name == "Environment"
    ]
    assert matches == ["trading/domain/common.py"]


@pytest.mark.parametrize(
    ("class_name", "owner"),
    [
        ("RiskApplication", "application/risk.py"),
        ("ExecutionApplication", "application/execution.py"),
        ("TradingRuntime", "runtime/trading.py"),
    ],
)
def test_c2_c4_authority_classes_are_singletons(class_name: str, owner: str) -> None:
    matches = [
        path.relative_to(TRADING).as_posix()
        for path in _files(TRADING)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    assert matches == [owner]


def test_c5_dispatch_is_the_unique_risk_execution_seam() -> None:
    matches = []
    for path in _files(TRADING):
        calls = [
            _call_name(node)
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
        ]
        if "submit_approved" in calls:
            matches.append(path.relative_to(TRADING).as_posix())
    assert matches == ["runtime/dispatch.py"]


def test_c6_port_has_exact_provider_neutral_methods() -> None:
    tree = _tree(TRADING / "ports" / "broker_execution.py")
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BrokerExecutionPort")
    assert [node.name for node in cls.body if isinstance(node, ast.FunctionDef)] == [
        "connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"
    ]


def test_c7_paper_factory_constructs_real_paper_adapter_without_connecting(tmp_path) -> None:
    factory = build_execution_candidate_factory(
        environment=Environment.PAPER, live_trading_enabled=False
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    adapter = factory(_adapter_config(), repository=repository)
    from us_quant.trading.adapters.ibkr.execution import IBKRExecutionAdapter

    assert isinstance(adapter, IBKRExecutionAdapter)
    assert adapter.config.port == 4002
    assert adapter._connected is False


@pytest.mark.parametrize(
    ("environment", "flag", "blocker"),
    [
        (Environment.BACKTEST, False, ExecutionDeploymentBlocker.BACKTEST_HAS_NO_BROKER_CHANNEL),
        (Environment.BACKTEST, True, ExecutionDeploymentBlocker.BACKTEST_HAS_NO_BROKER_CHANNEL),
        (Environment.LIVE, False, ExecutionDeploymentBlocker.LIVE_FEATURE_DISABLED),
        (Environment.LIVE, True, ExecutionDeploymentBlocker.LIVE_ADAPTER_UNAVAILABLE),
    ],
)
def test_c8_c10_production_factory_fails_closed(
    tmp_path, environment, flag, blocker
) -> None:
    decision = evaluate_execution_deployment(
        environment=environment, live_trading_enabled=flag
    )
    assert decision.broker_submission_allowed is False
    assert decision.blocker is blocker
    factory = build_execution_candidate_factory(
        environment=environment, live_trading_enabled=flag
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    with patch("us_quant.trading.composition.execution.IBKRExecutionAdapter") as adapter:
        for _ in range(2):
            with pytest.raises(ExecutionDeploymentError):
                factory(_adapter_config(), repository=repository)
    adapter.assert_not_called()


def test_c11_c12_paper_adapter_boundary_guards_remain_present() -> None:
    adapter_path = TRADING / "adapters" / "ibkr" / "execution.py"
    tree = _tree(adapter_path)
    text = adapter_path.read_text(encoding="utf-8")
    assert "ensure_paper_order_config(config)" in text
    assert "config.port != 4002" in text
    assert 'startswith("DU")' in text
    assert any(isinstance(node, ast.FunctionDef) and node.name == "gateway_managed_accounts" for node in ast.walk(tree))


def test_c13_shared_core_has_no_environment_branching() -> None:
    files = [
        TRADING / "application" / "risk.py",
        TRADING / "application" / "execution.py",
        TRADING / "runtime" / "dispatch.py",
        TRADING / "runtime" / "trading.py",
    ]
    for path in files:
        tree = _tree(path)
        assert not any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                any(alias.name == "Environment" for alias in getattr(node, "names", ()))
            )
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Environment"
            for node in ast.walk(tree)
        )


def test_c14_shared_runtime_strings_are_provider_neutral() -> None:
    denied = (
        "ibkr paper", "paper order", "paper 订单", "paper 限价", "paper 回报",
        "live order", "live 订单",
    )
    paths = [TRADING / "runtime" / "dispatch.py", TRADING / "runtime" / "trading.py"]
    for path in paths:
        strings = [
            node.value.casefold()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not [value for value in strings if any(word in value for word in denied)]


@pytest.mark.parametrize("method", ["placeOrder", "cancelOrder"])
def test_c15_production_broker_api_calls_have_one_adapter_owner(method: str) -> None:
    owners = [
        path.relative_to(SRC).as_posix()
        for path in _files(SRC)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and _call_name(node) == method
    ]
    assert set(owners) == {
        "trading/adapters/ibkr/execution.py",
        "trading/adapters/ibkr/live_execution.py",
    }
    assert len(owners) == 2


def test_c15b_live_adapter_is_not_constructed_by_production_code() -> None:
    owners = [
        path.relative_to(SRC).as_posix()
        for path in _files(SRC)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and _call_name(node) == "IBKRLiveExecutionAdapter"
    ]
    assert owners == []


def test_c16_durable_record_precedes_submit() -> None:
    tree = _tree(TRADING / "application" / "execution.py")
    submit = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_submit")
    calls = [node for node in ast.walk(submit) if isinstance(node, ast.Call)]
    durable_line = next(node.lineno for node in calls if _call_name(node) == "record_intent")
    broker_submit_line = next(node.lineno for node in calls if _call_name(node) == "submit")
    assert durable_line < broker_submit_line


def test_c17_uncertain_outcome_is_not_retried() -> None:
    dispatch, repository, broker, trace = _dispatch(uncertain=True)
    result = dispatch.submit(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        reason="closure uncertainty proof",
        session_id="closure-session",
    )

    assert result.halt is True
    assert result.intent is not None
    assert repository.intent(result.intent.order_id) is result.intent
    assert trace == ["reserve", "durable", "submit"]
    assert broker.submit_attempts == 1


def test_c18_explicit_refusal_has_no_fallback() -> None:
    dispatch, repository, broker, trace = _refusing_dispatch()
    result = dispatch.submit(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        reason="closure refusal proof",
        session_id="closure-session",
    )

    assert result.halt is True
    assert result.submitted is False
    assert trace == ["refused"]
    assert repository.intents == {}
    assert broker.submit_attempts == 0


def test_c19_paper_autonomy_wiring_uses_existing_service_boundary() -> None:
    tree = _tree(SRC / "desktop.py")
    calls = [_call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert calls.count("build_execution_candidate_factory") == 1
    service = _tree(TRADING / "application" / "paper" / "service.py")
    assert not any(isinstance(node, ast.Name) and node.id == "Environment" for node in ast.walk(service))


def test_c20_no_live_endpoint_or_account_configuration_added() -> None:
    config = _tree(SRC / "ibkr.py")
    cls = next(node for node in config.body if isinstance(node, ast.ClassDef) and node.name == "IBKRConnectionConfig")
    names = {node.target.id for node in cls.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    assert not names.intersection({"live_host", "live_port", "live_account", "live_credentials"})
    assert "7496" not in (SRC / "ibkr.py").read_text(encoding="utf-8")
