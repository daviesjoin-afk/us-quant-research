"""Stage 6-C architecture guards.

The claims asserted here are the ones that make single lifecycle authority
structural rather than a convention: only the controller can mint an
authorization, the legacy gate flag is not consulted anywhere in the transition
path, the evidence-driven targets are exactly the Paper ones, and nothing below
the composition layer can forge its way across the Paper boundary.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"

DOMAIN = SRC / "trading" / "domain" / "strategy_lifecycle.py"
APPLICATION = SRC / "trading" / "application" / "strategy_lifecycle.py"
POLICY_PORT = SRC / "trading" / "ports" / "strategy_lifecycle_repository.py"
REPOSITORY_ADAPTER = (
    SRC / "trading" / "adapters" / "sqlite" / "strategy_lifecycle_repository.py"
)
COMPOSITION = SRC / "trading" / "composition" / "strategy_lifecycle.py"
STRATEGIES = SRC / "trading" / "application" / "strategies.py"

LIFECYCLE_SURFACE = (
    DOMAIN, APPLICATION, POLICY_PORT, REPOSITORY_ADAPTER, COMPOSITION,
)

STATISTICAL_THRESHOLDS = (
    "MAXIMUM_PBO",
    "MINIMUM_DSR_PROBABILITY",
    "MINIMUM_HAC_POSITIVE_PROBABILITY",
    "walk_forward",
    "deflated_sharpe",
)


def _text(path):
    return path.read_text(encoding="utf-8")


def _import_targets(path):
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


def test_l01_exactly_one_lifecycle_controller_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "StrategyLifecycleController"
        )

    assert len(matches) == 1
    assert matches[0][0] == APPLICATION


def test_l02_only_the_controller_can_mint_an_authorization():
    references = [
        path for path in _production_files() if "_CONTROLLER_TOKEN" in _text(path)
    ]

    assert set(references) == {DOMAIN, APPLICATION}


def test_l03_authorization_requires_the_private_token():
    tree = ast.parse(_text(DOMAIN))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StrategyLifecycleAuthorization":
            init = next(
                child for child in node.body
                if isinstance(child, ast.FunctionDef) and child.name == "__init__"
            )
            break
    else:
        raise AssertionError("StrategyLifecycleAuthorization is missing")

    assert "_controller_token" in {argument.arg for argument in init.args.kwonlyargs}


# -- the legacy flag is not authority -----------------------------------


def test_l04_the_transition_path_never_consults_gate_passed():
    """The whole point of 6-C: a stored boolean decides nothing.

    Asserted on attribute *reads* inside the method rather than on the file
    text, so the docstring may explain the retirement while any actual
    ``current.gate_passed`` lookup turns this red.
    """

    tree = ast.parse(_text(STRATEGIES))
    transition = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "transition"
    )

    reads = {
        node.attr for node in ast.walk(transition) if isinstance(node, ast.Attribute)
    }

    assert "gate_passed" not in reads
    assert "gate_reason" not in reads


def test_l05_the_evidence_driven_targets_are_exactly_the_paper_ones():
    text = _text(STRATEGIES)
    assert "AUTHORIZATION_REQUIRED_TARGETS" in text

    from us_quant.trading.application.strategies import (
        AUTHORIZATION_REQUIRED_TARGETS,
    )
    from us_quant.trading.domain.strategy import StrategyStatus

    assert AUTHORIZATION_REQUIRED_TARGETS == {
        StrategyStatus.PAPER_SHADOW,
        StrategyStatus.PAUSED,
    }
    # STOPPED stays explicit governance, not an autonomous decision.
    assert StrategyStatus.STOPPED not in AUTHORIZATION_REQUIRED_TARGETS


def test_l06_stopped_is_not_a_lifecycle_action():
    from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction

    assert "STOPPED" not in {action.value for action in StrategyLifecycleAction}
    assert [action.value for action in StrategyLifecycleAction] == [
        "PROMOTE_TO_PAPER_SHADOW",
        "PAUSE",
        "RESUME_PAPER_SHADOW",
    ]


def test_l07_the_lifecycle_surface_never_recomputes_statistics():
    for path in LIFECYCLE_SURFACE:
        text = _text(path)
        for name in STATISTICAL_THRESHOLDS:
            assert name not in text, f"{path.name} names {name}"


# -- dependency direction ------------------------------------------------


def test_l08_application_reaches_only_domain_and_ports():
    targets = _import_targets(APPLICATION)

    assert not any("trading.adapters" in target for target in targets)
    assert not any(target == "sqlite3" for target in targets)
    assert any("trading.ports" in target for target in targets)


def test_l09_domain_stays_free_of_frameworks_and_storage():
    targets = _import_targets(DOMAIN)

    assert not any(
        term in target.lower()
        for target in targets
        for term in ("pyside", "pyqt", "sqlite", "desktop", "ibapi")
    )


def test_l10_composition_is_the_only_place_naming_the_lifecycle_adapter():
    for path in (DOMAIN, APPLICATION, POLICY_PORT):
        assert not any(
            "sqlite.strategy_lifecycle_repository" in target
            for target in _import_targets(path)
        ), path

    assert any(
        "sqlite.strategy_lifecycle_repository" in target
        for target in _import_targets(COMPOSITION)
    )


def test_l11_no_protected_layer_mints_an_authorization():
    """Runtime, broker adapters and desktop must not cross the Paper boundary."""

    protected = (
        SRC / "trading" / "runtime",
        SRC / "trading" / "adapters" / "ibkr",
        SRC / "desktop.py",
        SRC / "desktop_v2",
    )
    files = []
    for root in protected:
        files.extend(root.rglob("*.py") if root.is_dir() else (root,))

    offenders = []
    for path in files:
        if not path.exists():
            continue
        targets = _import_targets(path)
        if any("_CONTROLLER_TOKEN" in target for target in targets):
            offenders.append(str(path.relative_to(SRC)))
        if "StrategyLifecycleAuthorization(" in _text(path):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_l12_lifecycle_never_becomes_execution_authority():
    for path in LIFECYCLE_SURFACE:
        text = _text(path)
        for forbidden in (
            "RiskApplication", "ExecutionApplication", "OrderDispatch",
            "BrokerExecutionPort", "PortfolioRuntime", "submit_order",
        ):
            assert forbidden not in text, f"{path.name} names {forbidden}"


def test_l13_no_ai_or_optimizer_imports_in_the_lifecycle_surface():
    for path in LIFECYCLE_SURFACE:
        targets = _import_targets(path)
        assert not any(
            term in target.lower()
            for target in targets
            for term in ("openai", "anthropic", "optimizer", "llm", "torch")
        ), path


# -- storage hygiene -----------------------------------------------------


def test_l14_the_lifecycle_store_never_alters_the_frozen_schema():
    text = _text(REPOSITORY_ADAPTER)

    for frozen in ("strategy_version", "strategy_deployment"):
        assert f"CREATE TABLE {frozen}" not in text
        assert f"ALTER TABLE {frozen}" not in text
        assert f"DROP TABLE {frozen}" not in text


def test_l15_the_lifecycle_store_is_separate_from_the_other_governance_stores():
    text = _text(REPOSITORY_ADAPTER)

    for other in (
        "strategy_gate_evaluation", "strategy_evidence_authentication",
        "strategy_coverage_evaluation", "strategy_coverage_policy",
    ):
        assert other not in text


def test_l16_the_decision_store_is_crash_safe_by_construction():
    """PREPARED must be written before the transition, and reconciled after."""

    text = _text(APPLICATION)

    assert "PREPARED" in text or "StrategyLifecycleDecisionState.PREPARED" in text
    assert "record_decision" in text
    assert "mark_applied" in text
    assert "mark_superseded" in text
    assert "def reconcile(" in text
    # The decision is persisted before the state machine moves.
    assert text.index("record_decision") < text.index("self._strategies.transition")


def test_l17_the_policy_store_appends_by_compare_and_set():
    text = _text(REPOSITORY_ADAPTER)

    assert "expected_current_revision" in text
    assert "MAX(revision)" in text


def test_l18_lifecycle_paths_do_not_import_the_signing_tool():
    for path in _production_files():
        if "strategy_lifecycle" not in path.name:
            continue
        assert not any(
            "research_evidence_sealing" in target
            for target in _import_targets(path)
        ), path


# -- the Paper launch gate ------------------------------------------------

LAUNCH_GATE = SRC / "trading" / "application" / "paper_authorization.py"
PLAN_BOUNDARY = SRC / "trading" / "application" / "portfolio_operations.py"
PLAN_COMPOSITION = SRC / "trading" / "composition" / "portfolio_operations.py"
PATHS = SRC / "paths.py"

GOVERNED_LAYERS = (
    SRC / "trading" / "runtime",
    SRC / "trading" / "adapters" / "ibkr",
    SRC / "trading" / "application" / "portfolio_runtime.py",
    SRC / "desktop_v2",
)


def test_l19_the_launch_gate_lives_at_the_plan_boundary_only():
    """Instruction 25: not in the runtime, not in risk, not in execution."""

    assert 'self._paper_authorization(' in _text(PLAN_BOUNDARY)

    offenders = []
    for root in GOVERNED_LAYERS:
        files = root.rglob("*.py") if root.is_dir() else (root,)
        for path in files:
            if not path.exists() or "__pycache__" in path.parts:
                continue
            if "paper_authorization" in _text(path):
                offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_l20_the_plan_boundary_no_longer_reads_the_legacy_flag():
    tree = ast.parse(_text(PLAN_BOUNDARY))
    boundary = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "PortfolioOperatingPlanApplication"
    )
    validate = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.FunctionDef) and node.name == "_validate"
    )

    reads = {
        node.attr for node in ast.walk(validate) if isinstance(node, ast.Attribute)
    }

    assert "gate_passed" not in reads


def test_l21_the_plan_boundary_fails_closed_without_a_gate():
    text = _text(PLAN_BOUNDARY)

    assert "self._paper_authorization is None" in text
    assert "or not self._paper_authorization(" in text


def test_l22_the_authorizer_returns_rather_than_raising():
    """A launch gate that can throw is one a caller wraps in a broad except.

    Scoped to ``authorises``: the constructor *should* raise on a missing
    dependency, since that is a wiring fault rather than a launch decision.
    """

    tree = ast.parse(_text(LAUNCH_GATE))
    authorizer = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "PaperLaunchAuthorizer"
    )
    authorises = next(
        node
        for node in ast.walk(authorizer)
        if isinstance(node, ast.FunctionDef) and node.name == "authorises"
    )

    raises = [node for node in ast.walk(authorises) if isinstance(node, ast.Raise)]
    returns = [node for node in ast.walk(authorises) if isinstance(node, ast.Return)]

    assert raises == []
    assert len(returns) >= 8


def test_l23_pause_is_never_an_entry_action():
    from us_quant.trading.application.paper_authorization import PAPER_ENTRY_ACTIONS
    from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction

    assert PAPER_ENTRY_ACTIONS == {
        StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        StrategyLifecycleAction.RESUME_PAPER_SHADOW,
    }
    assert StrategyLifecycleAction.PAUSE not in PAPER_ENTRY_ACTIONS


def test_l24_composition_is_the_only_place_that_names_the_authorizer():
    for path in (PLAN_BOUNDARY, LAUNCH_GATE):
        assert not any(
            "adapters" in target for target in _import_targets(path)
        ), path

    assert any(
        "paper_authorization" in target for target in _import_targets(PLAN_COMPOSITION)
    )


def test_l25_the_trust_root_stays_outside_the_runtime_store():
    """B1 requires verification trust material outside the artifact store."""

    tree = ast.parse(_text(PATHS))
    trust = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "trust_root"
    )

    reads = {
        node.attr for node in ast.walk(trust) if isinstance(node, ast.Attribute)
    }

    assert "state_root" in reads
    assert "runtime_root" not in reads
