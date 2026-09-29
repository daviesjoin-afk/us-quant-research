import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"
DOMAIN = SRC / "trading" / "domain" / "strategy_gate.py"
EVALUATOR = SRC / "trading" / "application" / "strategy_gate.py"
ADAPTER = SRC / "trading" / "adapters" / "research_evidence.py"
COMPOSITION = SRC / "trading" / "composition" / "strategy_gate.py"

# Stage 6-B2 coverage is the *sanctioned* consumer of gate evaluations: its
# whole job is to compose "authenticated evidence PASS" with "gate PASS".  It is
# therefore allowlisted below, by name and with a reason, rather than by
# loosening the guard.  The guard's teeth are unchanged: it still fails if a
# runtime, broker-adapter or desktop module starts reaching for the gate.
COVERAGE_DOMAIN = SRC / "trading" / "domain" / "strategy_coverage.py"
COVERAGE_APPLICATION = SRC / "trading" / "application" / "strategy_coverage.py"
SANCTIONED_GATE_CONSUMERS = frozenset(
    {EVALUATOR, COVERAGE_DOMAIN, COVERAGE_APPLICATION}
)


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
    """Runtime, broker adapters and the desktop must not reach the gate.

    The application layer is scanned too, minus the modules whose job *is* to
    consume gate evidence -- the gate evaluator itself and, since Stage 6-B2,
    coverage.  A new application module that mentions the gate without being
    allowlisted still turns this red, which is the property worth keeping.
    """

    protected_roots = (
        SRC / "trading" / "application",
        SRC / "trading" / "adapters" / "ibkr",
        SRC / "trading" / "runtime",
        SRC / "desktop.py",
    )
    protected_files = []
    for root in protected_roots:
        protected_files.extend(root.rglob("*.py") if root.is_dir() else (root,))
    references = [
        str(path.relative_to(SRC))
        for path in protected_files
        if path.exists()
        and path not in SANCTIONED_GATE_CONSUMERS
        and "strategy_gate" in _text(path).lower()
    ]
    assert references == []


def test_a18b_sanctioned_gate_consumers_are_exactly_the_gate_and_coverage():
    """The allowlist above must not quietly grow."""

    assert SANCTIONED_GATE_CONSUMERS == {
        EVALUATOR, COVERAGE_DOMAIN, COVERAGE_APPLICATION,
    }


def test_a19_and_a20_no_ai_or_optimizer_imports():
    imports = _imports(EVALUATOR) + _imports(ADAPTER) + _imports(COMPOSITION)
    assert not any("optimizer" in item.lower() or "openai" in item.lower() or "anthropic" in item.lower() for item in imports)


def test_a21_gate_is_not_wired_into_runtime_or_lifecycle():
    """Only the gate's own modules and coverage may name its evaluation type.

    Coverage is included because composing gate PASSes is precisely its
    authority; runtime, lifecycle, storage and desktop are still excluded, so a
    gate evaluation still cannot become a lifecycle input by accident.
    """

    changed_refs = []
    allowed = {
        DOMAIN, EVALUATOR, ADAPTER, COMPOSITION,
        SRC / "trading" / "ports" / "strategy_gate_repository.py",
        SRC / "trading" / "adapters" / "sqlite" / "strategy_gate_repository.py",
        COVERAGE_DOMAIN, COVERAGE_APPLICATION,
    }
    for path in SRC.rglob("*.py"):
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
