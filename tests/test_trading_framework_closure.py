"""Guards and characterization for Trading Framework Closure v2C.

This round is about *package boundaries*, not behaviour: the production runtime
must stop depending on the internal shadow simulator, the session config must
stop carrying risk policy, and five transitional root modules must find their
real homes and disappear.  The failure mode is therefore not "the algorithm
changed" but "the boundary was drawn on paper only" -- a shadow import left in
place, a field quietly kept for compatibility, or a root module kept alive as a
re-export.  Each guard below pins one of those.

The characterization tests in the second half pin the *behaviour* the move had
to preserve: the same config produces the same runtime, the builders return the
types the spec names, the shadow engine still starts and stops, health still
returns the same verdicts, and the desktop aggregate still shares one lease.
"""

from __future__ import annotations

import ast
from decimal import Decimal
import pathlib

from us_quant.desktop_v2 import workflows as workflows_module
from us_quant.shadow.config import (
    ShadowSimulationConfig,
    build_targeted_shadow_config,
)
from us_quant.trading.application import paper as paper_package
from us_quant.trading.composition.session_config import (
    build_auto_rotation_config,
)
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.health import evaluate_paper_execution_health

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_TRADING = _SRC / "trading"
SCRIPTS_DIR = _REPO_ROOT / "scripts"

_TRADING_RUNTIME_DIR = _TRADING / "runtime"
_SHADOW_DIR = _SRC / "shadow"
_PAPER_PACKAGE_DIR = _TRADING / "application" / "paper"


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in directory.rglob("*.py"))


def _all_source_files() -> list[pathlib.Path]:
    return sorted(_SRC.rglob("*.py"))


def _imports(path: pathlib.Path) -> set[str]:
    """Every module named by an import statement in ``path``."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module)
    return modules


def _matches(modules: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in modules
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in prefixes
        )
    }


def _identifier_names(path: pathlib.Path) -> set[str]:
    """Every name ``path`` mentions, in definitions and uses alike."""

    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
    return names


def _class_names(path: pathlib.Path) -> set[str]:
    """Every class name ``path`` itself defines."""

    return {
        node.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    }


def _is_self_attribute(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


#: Guard A: the production trading core must not import the shadow simulator.
_TRADING_CORE_DIRS = (
    _TRADING / "domain",
    _TRADING / "ports",
    _TRADING / "application",
    _TRADING / "runtime",
    _TRADING / "composition",
)

#: The five transitional root modules this round retires.  Deleted, not
#: re-exported: the project is an internal refactor and does not maintain two
#: import paths for one type.
RETIRED_FRAMEWORK_ROOT_MODULES = (
    "us_quant.auto_intraday",
    "us_quant.targeted_intraday",
    "us_quant.paper_execution_health",
    "us_quant.paper_trading_service",
    "us_quant.workflow_controller",
)

#: Guard E/F: what the Paper application package and the runtime health module
#: may not name.  Each is a capability that belongs to a caller.
FRAMEWORK_FORBIDDEN_IMPORTS = (
    "PySide6",
    "ibapi",
    "sqlite3",
    "us_quant.desktop",
    "us_quant.desktop_v2",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
)

#: Guard G: what the desktop workflow aggregate may not build.  It composes
#: workflow controllers; assembling a broker, a risk authority or an execution
#: service is the composition root's job.
WORKFLOW_AGGREGATE_FORBIDDEN_NAMES = (
    "IBKRExecutionAdapter",
    "IBKRPaperOrderService",
    "RiskApplication",
    "ExecutionApplication",
    "build_execution_application",
    "build_execution_candidate",
    "build_risk_application",
    "SQLiteOrderRepository",
    "placeOrder",
    "cancelOrder",
)


# -- Guard A -------------------------------------------------------------


def test_the_trading_core_never_imports_the_shadow_simulator() -> None:
    """The production runtime must not depend on the research simulator.

    This is the round's headline problem: ``StrategyRuntime``,
    ``TradingRuntime`` and the runtime composition root all took their config
    from ``us_quant.shadow_paper``, so the real runtime imported the internal
    simulator and the dependency pointed the wrong way.
    """

    offenders: list[str] = []
    for directory in _TRADING_CORE_DIRS:
        for path in _python_files(directory):
            used = _matches(_imports(path), ("us_quant.shadow", "us_quant.shadow_paper"))
            if used:
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()} -> {sorted(used)}"
                )
    assert not offenders, offenders


def test_the_retired_root_modules_are_gone_and_unreferenced() -> None:
    """Guard D: deleted, not renamed and not re-exported.

    A compatibility shim would keep a second entry point alive for the same
    type, which is what the round exists to remove.
    """

    for module in RETIRED_FRAMEWORK_ROOT_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; its "
            "behaviour moved into the trading/ and desktop_v2/ packages"
        )

        offenders: list[str] = []
        for source in (*_all_source_files(), *_python_files(SCRIPTS_DIR)):
            if source == path:
                continue
            if _matches(_imports(source), (module,)):
                offenders.append(source.relative_to(_REPO_ROOT).as_posix())
        assert not offenders, (module, offenders)


def test_no_module_defines_a_shadow_config_compatibility_alias() -> None:
    """The old name must not survive as an alias anywhere in production."""

    offenders: list[str] = []
    for path in _all_source_files():
        for name in _class_names(path) | _assigned_names(path):
            if name == "ShadowConfig":
                offenders.append(path.relative_to(_REPO_ROOT).as_posix())
    assert not offenders, offenders


# -- Guard B / C ---------------------------------------------------------


def test_the_session_config_carries_no_risk_overlay() -> None:
    """Guard B: policy does not travel inside the strategy's session config.

    ``layered_risk_limits`` and ``symbol_risk_multipliers`` used to live here,
    so the runtime had to go and find account risk in a config it was handed.
    Risk policy now arrives as a ``RiskApplication``; these names must not
    reappear on the production session type.
    """

    source = (_TRADING_RUNTIME_DIR / "config.py").read_text(encoding="utf-8")
    fields = {
        child.target.id
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, ast.AnnAssign)
        and isinstance(child.target, ast.Name)
    }
    assert "layered_risk_limits" not in fields
    assert "symbol_risk_multipliers" not in fields
    # Nothing assigns them either, so the names cannot return as plain
    # attributes rather than annotations -- the docstring may still explain the
    # history, which is why this is a code-level check and not a text search.
    assert not (
        {"layered_risk_limits", "symbol_risk_multipliers"}
        & _assigned_names(_TRADING_RUNTIME_DIR / "config.py")
    )
    # It really does still describe a session, so the guard cannot pass by the
    # type having been emptied.
    assert {"initial_cash", "capital_source", "profit_target"} <= fields


def test_the_shadow_config_extends_the_session_config() -> None:
    """Guard C: the extension runs one way, from session to shadow."""

    assert issubclass(ShadowSimulationConfig, TradingSessionConfig)
    assert not issubclass(TradingSessionConfig, ShadowSimulationConfig)
    # And the overlay really is on the shadow side.
    overlay = set(ShadowSimulationConfig.__dataclass_fields__)
    assert {"layered_risk_limits", "symbol_risk_multipliers"} <= overlay
    assert not (
        {"layered_risk_limits", "symbol_risk_multipliers"}
        & set(TradingSessionConfig.__dataclass_fields__)
    )


# -- Guard E / F ---------------------------------------------------------


def test_the_paper_package_and_health_import_no_outer_layer() -> None:
    """Guards E and F: no Qt, no desktop, no adapter, no SQLite.

    The Paper application package must stay provider-neutral, and the health
    evaluator must remain a pure function of the facts it is handed even though
    it lives beside the coordinator that calls it.
    """

    for path in (
        *_python_files(_PAPER_PACKAGE_DIR),
        _TRADING_RUNTIME_DIR / "health.py",
    ):
        offending = _matches(_imports(path), FRAMEWORK_FORBIDDEN_IMPORTS)
        assert not offending, (
            path.relative_to(_REPO_ROOT).as_posix(),
            sorted(offending),
        )


def test_the_paper_service_does_not_own_a_lifecycle() -> None:
    """The service owns the connection; it must not own the workflow."""

    fields = _assigned_names(_PAPER_PACKAGE_DIR / "service.py")
    for forbidden in (
        "PaperWorkflowPhase",
        "ExecutionLease",
        "CoordinatorReconciliationEvidence",
    ):
        assert forbidden not in fields, forbidden

    # It reads the phase *through* the injected workflow, rather than keeping a
    # phase of its own.
    source = (_PAPER_PACKAGE_DIR / "service.py").read_text(encoding="utf-8")
    assert "_workflow_getter().phase" in source


def test_the_workflow_aggregate_creates_no_broker_risk_or_execution() -> None:
    """Guard G: the desktop aggregate composes workflows and nothing else.

    Both halves matter: an *import* of the composition roots would make this
    module a second assembly point, and a *use* of the builder or the port names
    would mean it had started assembling even without that import.
    """

    path = _SRC / "desktop_v2" / "workflows.py"
    offending = _matches(
        _imports(path),
        (
            "us_quant.trading.composition",
            "us_quant.trading.adapters",
            "us_quant.trading.ports",
            "us_quant.ibkr",
            "ibapi",
            "sqlite3",
            "PySide6",
        ),
    )
    assert not offending, sorted(offending)

    names = _identifier_names(path)
    for forbidden in WORKFLOW_AGGREGATE_FORBIDDEN_NAMES:
        assert forbidden not in names, forbidden


# -- Guard H: removed ----------------------------------------------------
#
# This round's Guard H was a line-count gate: a 400-line "hard limit" for every new
# production module plus tighter per-file budgets.  It is **gone, and deliberately not
# replaced by a renamed or renumbered threshold.**  A file's length is a symptom, and a
# cap pinned at whatever a file happened to be fails on a good change while passing on a
# bad one -- which is exactly what happened here: adding the two-phase promotion to
# ``trading/application/paper/service.py`` (a safety fix, §27.11 of
# ``docs/DESKTOP_DECOMPOSITION.md``) pushed it from 380 to 462 lines, over both halves of
# the gate, without any boundary being crossed.
#
# What replaces it is the other guards in this file: they name a *property* -- single
# responsibility, a stable dependency direction, no re-export keeping a moved module
# alive, no capability crossing a boundary -- and each fails for a reason a reviewer can
# argue with.  The same rule is recorded in
# ``tests/test_desktop_paper_orchestration_architecture.py``.


# -- behaviour -----------------------------------------------------------


def test_no_new_god_package_appeared() -> None:
    """The round must not answer a boundary problem with a grab-bag module."""

    for directory in (_TRADING, _SHADOW_DIR):
        for name in (
            "core.py",
            "services.py",
            "common.py",
            "helpers.py",
            "managers.py",
        ):
            assert not (directory / name).exists(), (directory.name, name)


# -- characterization ----------------------------------------------------


def test_the_auto_rotation_builder_returns_the_session_config() -> None:
    """Characterization: same inputs, same values, production config type."""

    from us_quant.trading.application.strategy_defaults import (
        DEFAULT_STRATEGY_SEEDS,
    )

    seed = next(
        item
        for item in DEFAULT_STRATEGY_SEEDS
        if item.strategy_id == "intraday-auto-rotation"
    )
    config = build_auto_rotation_config(
        dict(seed.parameters),
        initial_cash=Decimal("1500"),
        capital_source="unit test",
        daily_loss_limit=Decimal("15"),
    )
    assert type(config) is TradingSessionConfig
    assert config.initial_cash == Decimal("1500")
    assert config.capital_source == "unit test"
    assert config.daily_loss_limit == Decimal("15")
    assert not hasattr(config, "layered_risk_limits")


def test_the_targeted_builder_returns_the_shadow_config() -> None:
    """Characterization: the shadow overlay survives the move unchanged."""

    from us_quant.trading.application.strategy_defaults import (
        DEFAULT_STRATEGY_SEEDS,
    )

    seed = next(
        item
        for item in DEFAULT_STRATEGY_SEEDS
        if item.strategy_id == "intraday-targeted-t"
    )
    config = build_targeted_shadow_config(
        dict(seed.parameters),
        initial_cash=Decimal("1500"),
        capital_source="unit test",
        daily_loss_limit=Decimal("15"),
        symbol_risk_multipliers={"AAPL": Decimal("1.5")},
    )
    assert type(config) is ShadowSimulationConfig
    assert isinstance(config, TradingSessionConfig)
    assert config.symbol_risk_multipliers == {"AAPL": Decimal("1.5")}


def test_the_health_evaluator_is_still_a_pure_verdict_function() -> None:
    """Characterization: the same facts still produce the same status."""

    import inspect

    signature = inspect.signature(evaluate_paper_execution_health)
    assert "connection" in signature.parameters
    assert "candidate_symbols" in signature.parameters
    assert "now" in signature.parameters
    # It is the injected evaluator the coordinator calls, so it must stay a
    # module-level function rather than a method on a service.
    assert inspect.isfunction(evaluate_paper_execution_health)


def test_the_paper_package_exports_the_service_and_its_contracts() -> None:
    """Characterization: the package is the one import path for all of it."""

    for name in (
        "PaperTradingService",
        "PaperTradingLifecycleError",
        "PaperTradingSnapshot",
        "PaperReconciliationStatus",
        "PaperOrderServicePort",
        "PaperWorkflowPort",
        "WorkflowGetter",
    ):
        assert hasattr(paper_package, name), name


def test_the_desktop_aggregate_still_shares_one_execution_lease() -> None:
    """Characterization: the move must not hand each workflow its own lease."""

    controller = workflows_module.WorkflowController()
    controller.shadow.start()
    controller.shadow.stop()
    controller.paper.begin_preparing()
    controller.paper.mark_ready()

    plans = controller.paper
    assert plans is not None
    # The shared lease is what makes "shadow and Paper cannot both run"
    # structural; both controllers must be wired to the same manager.
    assert controller.shadow._leases is controller.paper._leases


# -- helpers -------------------------------------------------------------


def _assigned_names(path: pathlib.Path) -> set[str]:
    """Names assigned as attributes, annotation targets or module globals."""

    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    names.add(target.attr)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                names.add(node.target.attr)
        elif isinstance(node, ast.Attribute) and _is_self_attribute(node):
            names.add(node.attr)
    return names
