import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"
DOMAIN = SRC / "trading" / "domain" / "strategy_gate.py"
EVALUATOR = SRC / "trading" / "application" / "strategy_gate.py"
ADAPTER = SRC / "trading" / "adapters" / "research_evidence.py"
COMPOSITION = SRC / "trading" / "composition" / "strategy_gate.py"


def _text(path):
    return path.read_text(encoding="utf-8")


def _imports(path):
    tree = ast.parse(_text(path))
    return tuple(
        node.module or "" if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    )


def test_a01_evaluator_has_exactly_one_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend((path, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "StrategyGateEvaluator")
    assert len(matches) == 1
    assert matches[0][0] == EVALUATOR


def test_a02_domain_is_qt_free():
    assert not any("PySide" in item or "PyQt" in item for item in _imports(DOMAIN))


def test_a03_to_a05_application_is_framework_and_storage_free():
    for path in (EVALUATOR, ADAPTER):
        imports = _imports(path)
        assert not any(item == "sqlite3" or "adapters.sqlite" in item for item in imports)
        assert not any("desktop" in item.lower() or "PySide" in item or "PyQt" in item for item in imports)


def test_a06_to_a08_evaluator_has_no_broker_risk_or_execution_imports():
    imports = _imports(EVALUATOR)
    assert not any(any(term in item.lower() for term in ("broker", "risk", "execution")) for item in imports)


def test_a09_to_a11_evaluator_has_no_lifecycle_calls():
    tree = ast.parse(_text(EVALUATOR))
    forbidden = {"transition", "clone_version", "update_deployment"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden for node in ast.walk(tree))


def test_a12_to_a16_targeted_review_owns_statistical_thresholds():
    review = _text(SRC / "targeted_review.py")
    assert "MAXIMUM_PBO" in review
    assert "MINIMUM_DSR_PROBABILITY" in review
    assert "MINIMUM_HAC_POSITIVE_PROBABILITY" in review
    combined = "\n".join(_text(path) for path in (DOMAIN, EVALUATOR, ADAPTER, COMPOSITION))
    for name in (
        "MAXIMUM_PBO", "MINIMUM_DSR_PROBABILITY",
        "MINIMUM_HAC_POSITIVE_PROBABILITY", "MINIMUM_COMPLETE_SESSIONS",
        "MINIMUM_OOS_SESSIONS", "OOS_RETURN_THRESHOLD",
        "TOP_BOOK_CAPACITY_THRESHOLD",
    ):
        assert name not in combined


def test_a17_and_a18_stage4_and_stage5_production_paths_are_unchanged():
    changed = __import__("subprocess").run(["git", "diff", "--name-only", "acb5d23663f00644ff8391e948d961792225ccb9"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    forbidden_roots = ("src/us_quant/trading/application/portfolio", "src/us_quant/trading/application/risk", "src/us_quant/trading/application/execution", "src/us_quant/trading/adapters/ibkr", "src/us_quant/trading/application/live", "src/us_quant/desktop")
    assert not any(path.startswith(forbidden_roots) for path in changed)


def test_a19_and_a20_no_ai_or_optimizer_imports():
    imports = _imports(EVALUATOR) + _imports(ADAPTER) + _imports(COMPOSITION)
    assert not any("optimizer" in item.lower() or "openai" in item.lower() or "anthropic" in item.lower() for item in imports)


def test_a21_gate_is_not_wired_into_runtime_or_lifecycle():
    changed_refs = []
    allowed = {
        DOMAIN, EVALUATOR, ADAPTER, COMPOSITION,
        SRC / "trading" / "ports" / "strategy_gate_repository.py",
        SRC / "trading" / "adapters" / "sqlite" / "strategy_gate_repository.py",
    }
    for path in (SRC / "trading").rglob("*.py"):
        if path in allowed:
            continue
        if "StrategyGateEvaluation" in _text(path) or "SQLiteStrategyGateRepository" in _text(path):
            changed_refs.append(path)
    assert changed_refs == []


def test_a22_composition_constructs_only_gate_evaluator_and_repository():
    text = _text(COMPOSITION)
    assert "StrategyGateEvaluator()" in text
    assert "SQLiteStrategyGateRepository(database_path)" in text
    for forbidden in ("RiskApplication", "ExecutionApplication", "PortfolioRuntime", "BrokerExecutionPort", "OrderDispatch"):
        assert forbidden not in text


def test_a23_adapter_is_pure_projection_of_existing_review_fields():
    text = _text(ADAPTER)
    assert "TargetedReviewResult" in text
    assert "run_targeted_review" not in text
    assert "MAXIMUM_PBO" not in text
    assert "parameter_hash=result.base_parameter_hash" in text
