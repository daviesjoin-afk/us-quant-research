"""Stage 6-B2 architecture guards.

Coverage composes two authorities; it must not become a third one.  The claims
asserted here: coverage reaches storage and the trust root only through ports,
it never re-runs a statistical threshold, it never mutates lifecycle, and the
versioned policy is the only thing that decides what "covered" means.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"

DOMAIN = SRC / "trading" / "domain" / "strategy_coverage.py"
APPLICATION = SRC / "trading" / "application" / "strategy_coverage.py"
REPOSITORY_PORT = SRC / "trading" / "ports" / "strategy_coverage_repository.py"
REPOSITORY_ADAPTER = (
    SRC / "trading" / "adapters" / "sqlite" / "strategy_coverage_repository.py"
)
COMPOSITION = SRC / "trading" / "composition" / "strategy_coverage.py"

COVERAGE_SURFACE = (
    DOMAIN, APPLICATION, REPOSITORY_PORT, REPOSITORY_ADAPTER, COMPOSITION,
)

#: Statistical hard-gates that belong to TargetedReview alone.  Coverage must
#: never name one, let alone re-derive it.
STATISTICAL_THRESHOLDS = (
    "MAXIMUM_PBO",
    "MINIMUM_DSR_PROBABILITY",
    "MINIMUM_HAC_POSITIVE_PROBABILITY",
    "MINIMUM_COMPLETE_SESSIONS",
    "MINIMUM_OOS_SESSIONS",
    "OOS_RETURN_THRESHOLD",
    "TOP_BOOK_CAPACITY_THRESHOLD",
    "walk_forward",
    "walkforward",
    "deflated_sharpe",
    "probability_of_backtest_overfitting",
)


def _text(path):
    return path.read_text(encoding="utf-8")


def _import_targets(path):
    """Every dotted path an import can reach, in either import form."""

    targets: set[str] = set()
    for node in ast.walk(ast.parse(_text(path))):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                targets.add(base)
            for alias in node.names:
                targets.add(f"{base}.{alias.name}" if base else alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
    return targets


def _production_files():
    return tuple(
        path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts
    )


# -- single authority ----------------------------------------------------


def test_c01_exactly_one_coverage_evaluator_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "StrategyCoverageEvaluator"
        )

    assert len(matches) == 1
    assert matches[0][0] == APPLICATION


# -- coverage is not a statistics authority ------------------------------


def test_c02_coverage_never_names_a_statistical_threshold():
    """PBO, DSR, HAC, walk-forward and data-quality stay in TargetedReview."""

    for path in COVERAGE_SURFACE:
        text = _text(path)
        for name in STATISTICAL_THRESHOLDS:
            assert name not in text, f"{path.name} names {name}"


def test_c03_coverage_does_not_import_the_targeted_review_pipeline():
    for path in COVERAGE_SURFACE:
        assert not any(
            "targeted_" in target for target in _import_targets(path)
        ), path


def test_c04_coverage_does_not_re_run_the_gate():
    """It consumes gate *evaluations*; it must not evaluate gates itself."""

    targets = _import_targets(APPLICATION)

    assert not any("application.strategy_gate" in target for target in targets)
    assert any("domain.strategy_gate" in target for target in targets)


def test_c05_coverage_does_not_re_authenticate():
    """It consumes authenticated evidence; authentication is B1's authority."""

    targets = _import_targets(APPLICATION)

    assert not any(
        "application.evidence_authentication" in target for target in targets
    )
    assert not any("evidence_signature" in target for target in targets)
    assert not any("research_evidence_sealing" in target for target in targets)


# -- dependency direction ------------------------------------------------


def test_c06_application_reaches_only_domain_and_ports():
    targets = _import_targets(APPLICATION)

    assert not any("trading.adapters" in target for target in targets)
    assert not any(target == "sqlite3" for target in targets)
    assert any("trading.ports" in target for target in targets)


def test_c07_domain_stays_free_of_frameworks_and_storage():
    targets = _import_targets(DOMAIN)

    assert not any(
        term in target.lower()
        for target in targets
        for term in ("pyside", "pyqt", "sqlite", "desktop", "ibapi")
    )


def test_c08_composition_is_the_only_place_that_names_the_coverage_adapter():
    for path in (DOMAIN, APPLICATION, REPOSITORY_PORT):
        assert not any(
            "sqlite.strategy_coverage_repository" in target
            for target in _import_targets(path)
        ), path

    assert any(
        "sqlite.strategy_coverage_repository" in target
        for target in _import_targets(COMPOSITION)
    )


def test_c09_no_protected_layer_imports_the_coverage_machinery():
    """Runtime and execution must not start consulting coverage."""

    protected_roots = (
        SRC / "trading" / "runtime",
        SRC / "trading" / "adapters" / "ibkr",
        SRC / "desktop.py",
        SRC / "desktop_v2",
    )
    files = []
    for root in protected_roots:
        files.extend(root.rglob("*.py") if root.is_dir() else (root,))

    offenders = []
    for path in files:
        if not path.exists():
            continue
        if any(
            "strategy_coverage" in target for target in _import_targets(path)
        ):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


# -- coverage is not lifecycle authority ---------------------------------


def test_c10_coverage_surface_has_no_lifecycle_calls():
    forbidden = {"transition", "clone_version", "update_deployment"}
    for path in COVERAGE_SURFACE:
        tree = ast.parse(_text(path))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not (calls & forbidden), path


def test_c11_coverage_never_becomes_promotion_authority():
    for path in COVERAGE_SURFACE:
        text = _text(path)
        assert "gate_passed" not in text
        assert "StrategyStatus" not in text
        assert "PortfolioOperatingPlan" not in text


def test_c12_coverage_never_writes_to_the_strategy_repository():
    for path in COVERAGE_SURFACE:
        targets = _import_targets(path)
        assert not any(
            target.endswith("strategy_repository") for target in targets
        ), path
        assert "StrategyRepository" not in _text(path)


def test_c13_coverage_composition_binds_no_execution_surface():
    text = _text(COMPOSITION)

    for forbidden in (
        "RiskApplication", "ExecutionApplication", "PortfolioRuntime",
        "OrderDispatch", "BrokerExecutionPort", "PortfolioOperatingPlan",
    ):
        assert forbidden not in text
    assert "StrategyCoverageEvaluator(" in text
    assert "SQLiteStrategyCoverageRepository(database_path)" in text


# -- storage hygiene -----------------------------------------------------


def test_c14_coverage_store_never_alters_the_frozen_schema():
    text = _text(REPOSITORY_ADAPTER)

    for frozen in ("strategy_version", "strategy_deployment"):
        assert f"CREATE TABLE {frozen}" not in text
        assert f"ALTER TABLE {frozen}" not in text
        assert f"DROP TABLE {frozen}" not in text


def test_c15_coverage_store_is_separate_from_the_gate_and_auth_stores():
    text = _text(REPOSITORY_ADAPTER)

    assert "strategy_gate_evaluation" not in text
    assert "strategy_evidence_authentication" not in text
    assert "StrategyGateRepository" not in text
    assert "EvidenceAuthenticationRepository" not in text


def test_c16_coverage_store_appends_policies_by_compare_and_set():
    text = _text(REPOSITORY_ADAPTER)

    assert "expected_current_revision" in text
    assert "MAX(revision)" in text
    assert "StrategyCoverageRepositoryConflict" in text


# -- no AI, no hidden defaults -------------------------------------------


def test_c17_no_ai_or_optimizer_imports_in_the_coverage_surface():
    for path in COVERAGE_SURFACE:
        targets = _import_targets(path)
        assert not any(
            term in target.lower()
            for target in targets
            for term in ("openai", "anthropic", "optimizer", "llm", "torch")
        ), path


def test_c18_the_policy_is_the_only_source_of_coverage_thresholds():
    """No numeric coverage threshold may be hardcoded in the evaluator.

    The authorising numbers live in the stored policy.  The only literals the
    evaluator may compare against are counts derived from the evidence itself.
    """

    tree = ast.parse(_text(APPLICATION))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ]

    assert literals == [], literals


def test_c19_coverage_never_imports_the_signing_tool():
    for path in _production_files():
        if "strategy_coverage" not in path.name:
            continue
        assert not any(
            "research_evidence_sealing" in target
            for target in _import_targets(path)
        ), path
