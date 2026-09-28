import ast
from pathlib import Path

from us_quant.trading.ports.broker_execution import BrokerExecutionPort


SOURCE = Path(__file__).resolve().parents[1] / "src" / "us_quant"
PORTFOLIO_DOMAIN = SOURCE / "trading" / "domain" / "portfolio.py"
PORTFOLIO_APPLICATION = SOURCE / "trading" / "application" / "portfolio.py"
PORTFOLIO_REPOSITORY_PORT = SOURCE / "trading" / "ports" / "portfolio_repository.py"
PORTFOLIO_SQLITE_REPOSITORY = SOURCE / "trading" / "adapters" / "sqlite" / "portfolio_repository.py"


def _all_python_files():
    return [path for path in SOURCE.rglob("*.py") if "__pycache__" not in path.parts]


def _module(path: Path) -> str:
    return path.relative_to(SOURCE).with_suffix("").as_posix().replace("/", ".")


def _imports(tree: ast.AST) -> set[str]:
    values = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_capital_allocator_has_exactly_one_definition():
    owners = []
    for path in _all_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners.extend(
            _module(path)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "CapitalAllocator"
        )

    assert owners == ["trading.application.portfolio"]


def test_portfolio_domain_is_qt_adapter_repository_and_broker_free():
    tree = ast.parse(PORTFOLIO_DOMAIN.read_text(encoding="utf-8"))
    imports = _imports(tree)

    assert not any(
        name.startswith(("PySide", "PyQt", "us_quant.trading.adapters"))
        or "broker" in name.casefold()
        or "repository" in name.casefold()
        for name in imports
    )


def test_portfolio_application_cannot_reach_execution_or_broker_authorities():
    tree = ast.parse(PORTFOLIO_APPLICATION.read_text(encoding="utf-8"))
    imports = _imports(tree)
    assert not any(
        "BrokerExecutionPort" in name
        or "ExecutionApplication" in name
        or name.startswith("us_quant.trading.adapters")
        for name in imports
    )

    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert "OrderDispatch" not in names
    assert "OrderIntent" not in names
    assert "StrategyVersion" not in names
    assert "StrategyStatus" not in names


def test_existing_execution_authorities_and_seven_method_port_remain_unique():
    expected = {
        "RiskApplication": "trading.application.risk",
        "ExecutionApplication": "trading.application.execution",
        "OrderDispatch": "trading.runtime.dispatch",
        "TradingRuntime": "trading.runtime.trading",
    }
    definitions = {name: [] for name in expected}
    for path in _all_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(_module(path))

    assert definitions == {name: [owner] for name, owner in expected.items()}
    methods = [
        name
        for name, value in vars(BrokerExecutionPort).items()
        if not name.startswith("_") and callable(value)
    ]
    assert methods == ["connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"]


def test_portfolio_does_not_duplicate_or_change_stage4_live_safety_owners():
    expected = {
        "LiveCanaryExecutionGuard": "trading.application.live_canary_execution",
        "LiveStartupProof": "trading.domain.live_startup",
        "LiveAuthorizationState": "trading.domain.live_safety",
        "LiveCanaryRecovery": "trading.application.live_recovery",
    }
    definitions = {name: [] for name in expected}
    for path in _all_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in definitions:
                definitions[node.name].append(_module(path))

    assert definitions == {name: [owner] for name, owner in expected.items()}
    imports = _imports(ast.parse(PORTFOLIO_APPLICATION.read_text(encoding="utf-8")))
    assert not any("live_safety" in name or "live_canary" in name for name in imports)


def test_portfolio_has_no_ai_dependency():
    for path in (PORTFOLIO_DOMAIN, PORTFOLIO_APPLICATION):
        imports = _imports(ast.parse(path.read_text(encoding="utf-8")))
        assert not any(
            name.startswith(("openai", "langchain", "llama_index"))
            for name in imports
        )


def test_portfolio_state_repository_port_is_sqlite_free_and_has_one_adapter():
    imports = _imports(ast.parse(PORTFOLIO_REPOSITORY_PORT.read_text(encoding="utf-8")))
    assert not any("sqlite" in name.casefold() or name == "sqlite3" for name in imports)
    owners = []
    for path in _all_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners.extend(
            _module(path)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "SQLitePortfolioRepository"
        )
    assert owners == ["trading.adapters.sqlite.portfolio_repository"]


def test_portfolio_persistence_has_no_execution_or_live_safety_authority():
    imports = _imports(ast.parse(PORTFOLIO_SQLITE_REPOSITORY.read_text(encoding="utf-8")))
    assert not any(
        "BrokerExecutionPort" in name
        or "ExecutionApplication" in name
        or "LiveAuthorization" in name
        or name.startswith("us_quant.trading.adapters.ibkr")
        for name in imports
    )
