from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "us_quant"


def _python_files():
    return tuple(path for path in SOURCE.rglob("*.py") if "__pycache__" not in path.parts)


def _definitions(name: str):
    found = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                found.append(path)
    return tuple(found)


def _resolved_import_base(path: Path, node: ast.ImportFrom):
    if node.level == 0:
        return node.module
    module_parts = ["us_quant", *path.relative_to(SOURCE).with_suffix("").parts]
    package_parts = module_parts[:-1]
    parent_parts = package_parts[: len(package_parts) - (node.level - 1)]
    imported_parts = node.module.split(".") if node.module else []
    return ".".join([*parent_parts, *imported_parts])


def _imported_modules(path: Path, *, source_text: str | None = None):
    tree = ast.parse(
        source_text if source_text is not None else path.read_text(encoding="utf-8")
    )
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolved_import_base(path, node)
            if base:
                modules.add(base)
                modules.update(
                    f"{base}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
    return modules


def _called_symbols(path: Path, scope: ast.AST, *, source_text: str | None = None):
    tree = ast.parse(
        source_text if source_text is not None else path.read_text(encoding="utf-8")
    )
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = _resolved_import_base(path, node)
            if base:
                for alias in node.names:
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = f"{base}.{alias.name}"

    def dotted_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    targets = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        raw = dotted_name(node.func)
        if not raw:
            continue
        parts = raw.split(".")
        resolved = aliases.get(parts[0], parts[0])
        target = ".".join([resolved, *parts[1:]])
        targets.add(target)
        targets.add(target.rsplit(".", 1)[-1])
    return targets


def test_stage5_and_shared_execution_authorities_have_one_owner():
    expected = {
        "Environment": "trading/domain/common.py",
        "RiskApplication": "trading/application/risk.py",
        "ExecutionApplication": "trading/application/execution.py",
        "OrderDispatch": "trading/runtime/dispatch.py",
        "TradingRuntime": "trading/runtime/trading.py",
        "CapitalAllocator": "trading/application/portfolio.py",
        "PortfolioRuntime": "trading/application/portfolio_runtime.py",
        "PortfolioPaperEngine": "trading/runtime/portfolio_paper.py",
        "PaperAutonomySupervisor": "trading/application/paper_autonomy_supervisor.py",
        "PaperOrchestrator": "desktop_v2/orchestration/paper/orchestrator.py",
    }

    for name, relative_path in expected.items():
        definitions = _definitions(name)
        assert len(definitions) == 1
        assert definitions[0] == SOURCE / relative_path


def test_broker_execution_port_remains_exactly_seven_provider_neutral_methods():
    path = SOURCE / "trading" / "ports" / "broker_execution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    port = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerExecutionPort"
    )
    methods = [
        node.name for node in port.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert methods == ["connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"]


def test_production_paper_delegates_to_one_portfolio_composition_root():
    desktop_path = SOURCE / "desktop.py"
    desktop_tree = ast.parse(desktop_path.read_text(encoding="utf-8"))
    build = next(
        node for node in ast.walk(desktop_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_paper_session"
    )
    calls = _called_symbols(desktop_path, build)
    assert "build_portfolio_paper_session" in calls
    assert not calls.intersection({
        "PortfolioPaperEngine", "PortfolioRuntime", "CapitalAllocator",
        "OrderDispatch", "SessionBook", "TradingRuntime",
    })

    composition_path = SOURCE / "trading" / "composition" / "portfolio_paper.py"
    composition_tree = ast.parse(composition_path.read_text(encoding="utf-8"))
    composition_calls = _called_symbols(composition_path, composition_tree)
    assert {
        "PortfolioPaperEngine", "build_portfolio_runtime", "OrderDispatch", "SessionBook"
    } <= composition_calls
    assert "TradingRuntime" not in composition_calls


def test_signal_workers_and_paper_engine_do_not_own_risk_execution_or_adapters():
    worker = SOURCE / "trading" / "runtime" / "portfolio_strategies.py"
    worker_imports = _imported_modules(worker)
    assert not any(
        module.endswith("application.risk")
        or module.endswith("application.execution")
        or module.startswith("us_quant.trading.adapters")
        or module.endswith("runtime.dispatch")
        for module in worker_imports
    )
    engine = SOURCE / "trading" / "runtime" / "portfolio_paper.py"
    engine_imports = _imported_modules(engine)
    assert not any(
        module.startswith("us_quant.trading.adapters")
        or module.endswith("application.risk")
        or module.endswith("application.execution")
        for module in engine_imports
    )


def test_relative_imports_resolve_to_absolute_modules_for_layer_guards():
    runtime_path = SOURCE / "trading" / "runtime" / "portfolio_strategies.py"
    imports = _imported_modules(
        runtime_path,
        source_text=(
            "from ..adapters.sqlite import SQLiteOrderRepository\n"
            "from .dispatch import OrderDispatch\n"
            "from .. import adapters\n"
            "from ..application import execution as execution_application\n"
            "from us_quant.trading.application import risk\n"
        ),
    )

    assert "us_quant.trading.adapters.sqlite" in imports
    assert "us_quant.trading.runtime.dispatch" in imports
    assert "us_quant.trading.adapters" in imports
    assert "us_quant.trading.application.execution" in imports
    assert "us_quant.trading.application.risk" in imports


def test_composition_guards_resolve_qualified_and_aliased_constructors():
    desktop_path = SOURCE / "desktop.py"
    source_text = """
import us_quant.trading.runtime as runtime
from us_quant.trading.runtime.trading import TradingRuntime as LegacyRuntime
runtime.TradingRuntime()
LegacyRuntime()
"""
    targets = _called_symbols(
        desktop_path,
        ast.parse(source_text),
        source_text=source_text,
    )

    assert "TradingRuntime" in targets
    assert "us_quant.trading.runtime.TradingRuntime" in targets
    assert "us_quant.trading.runtime.trading.TradingRuntime" in targets


def test_execution_page_stays_render_and_intent_only():
    page = SOURCE / "desktop_v2" / "pages" / "execution" / "page.py"
    modules = _imported_modules(page)
    assert not any(
        ".application" in module
        or ".adapters" in module
        or ".runtime" in module
        for module in modules
    )


def test_final_architecture_docs_and_runbook_record_the_stage5_baseline_and_controls():
    architecture = (ROOT / "docs" / "TRADING_ARCHITECTURE_V2.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "STAGE_5_MULTI_STRATEGY_PAPER_RUNBOOK.md").read_text(encoding="utf-8")
    for marker in (
        "Stage 3 Final 为 PR #62",
        "Stage 4 Final 为 PR #69",
        "Stage 5-E PR #75",
        "daf88b39af1ad0a26e72fc73343ef1ce17821059",
        "reserve → durable record → submit",
        "unknown no-retry",
        "Stage 4 operationally complete = NO",
        "Stage 5 supervised multi-strategy Paper canary 未运行",
        "PortfolioReconciliationApplication",
    ):
        assert marker in architecture
    for marker in (
        "PAPER_SHADOW",
        "clean reconciliation",
        "PortfolioDecision",
        "partial fill",
        "zero-state",
        "final reconciliation",
        "canary",
    ):
        assert marker.casefold() in runbook.casefold()


def test_final_mutation_aggregate_contains_every_stage_and_fails_closed():
    path = ROOT / "scripts" / "mutation_stage5_final.ps1"
    script = path.read_text(encoding="utf-8")
    for child in (
        "mutation_portfolio_runtime_a.ps1",
        "mutation_portfolio_runtime_b.ps1",
        "mutation_portfolio_runtime_c.ps1",
        "mutation_portfolio_reconciliation_d.ps1",
        "mutation_portfolio_operations_e.ps1",
        "mutation_stage4_final.ps1",
    ):
        assert child in script
    assert "if ($exitCode -ne 0)" in script
    assert "if ($summaries.Count -eq 0)" in script
    assert "$_.Red -ne $_.Mutations" in script
    assert "$_.Survivors -ne 0" in script
    assert "exit 1" in script
