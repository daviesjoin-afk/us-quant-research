"""Architecture guards for the shadow simulation subsystem.

Shadow Framework v2 moved ``shadow_paper.py`` -- one 875-line module holding four
data models, a SQLite store, a simulation lifecycle and the strategy's entry/exit
behaviour -- into a five-module package.  The risk of that kind of move is a
"split" that is really one large class renamed, or a second state source created
so the behaviour could be relocated.

Each guard below pins one of those failure modes: the old root module is gone and
unreferenced; models import nothing; the store cannot reach the engine; the whole
package is broker-isolated; the behaviour mixin holds no state of its own; and no
method is defined twice across the two halves of the engine.  The size guard
keeps the split honest.

These guards live here rather than in ``test_trading_framework_closure.py`` so
each subsystem owns its own rules as the refactor moves into later phases.
"""

from __future__ import annotations

import ast
import pathlib

from us_quant.shadow import engine as engine_module
from us_quant.shadow import models as models_module
from us_quant.shadow import store as store_module
from us_quant.shadow import trade_logic as trade_logic_module

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_SHADOW_DIR = _SRC / "shadow"
SCRIPTS_DIR = _REPO_ROOT / "scripts"
_TESTS_DIR = _REPO_ROOT / "tests"

#: The module this round retires.  Deleted, not re-exported: a shim would keep a
#: second import path alive for the same types.
RETIRED_SHADOW_ROOT_MODULE = "us_quant.shadow_paper"

#: Every production module of the shadow package.
SHADOW_MODULES = (
    _SHADOW_DIR / "__init__.py",
    _SHADOW_DIR / "config.py",
    _SHADOW_DIR / "models.py",
    _SHADOW_DIR / "store.py",
    _SHADOW_DIR / "engine.py",
    _SHADOW_DIR / "trade_logic.py",
)

#: Guard G: the per-file budgets this round declares.
SHADOW_MODULE_LINE_LIMITS = {
    "__init__.py": 60,
    "config.py": 180,
    "models.py": 150,
    "store.py": 300,
    "engine.py": 380,
    "trade_logic.py": 330,
}

#: The spec's absolute ceiling for any new production file.
SHADOW_MODULE_HARD_LIMIT = 400

#: Guard D: a simulated subsystem must never be able to reach a broker, and must
#: never be able to name an order it could send.
SHADOW_FORBIDDEN_IMPORTS = (
    "ibapi",
    "us_quant.ibkr",
    "us_quant.trading.adapters",
    "us_quant.trading.ports",
    "us_quant.trading.application",
    "us_quant.sqlite_support.ibkr",
    "PySide6",
    "us_quant.desktop",
    "us_quant.desktop_v2",
)

SHADOW_FORBIDDEN_NAMES = (
    "IBKRExecutionAdapter",
    "IBKRPaperOrderService",
    "BrokerExecutionPort",
    "ExecutionApplication",
    "OrderIntent",
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "submit_approved",
)

#: Python's own object surface: excluded from the duplicate-method check because
#: both halves inherit it rather than define it.
_OBJECT_METHODS = frozenset(
    {
        "__init__",
        "__init_subclass__",
        "__subclasshook__",
        "__class_getitem__",
    }
)


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in directory.rglob("*.py"))


def _imports(path: pathlib.Path) -> set[str]:
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


def _line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _identifier_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
    return names


def _class_methods(module: object, name: str) -> set[str]:
    """The methods a class defines in ``module``, by name."""

    path = pathlib.Path(getattr(module, "__file__") or "")
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.name
                for statement in node.body
                if isinstance(
                    statement, (ast.FunctionDef, ast.AsyncFunctionDef)
                )
            }
    raise AssertionError(f"{name} is not defined in {path.name}")


# -- Guard A -------------------------------------------------------------


def test_the_retired_shadow_root_module_is_gone_and_unreferenced() -> None:
    """Guard A: deleted, and no source, script or test imports it."""

    path = _SRC / f"{RETIRED_SHADOW_ROOT_MODULE.removeprefix('us_quant.')}.py"
    assert not path.exists(), (
        f"{path.relative_to(_SRC).as_posix()} must not exist; its behaviour "
        "moved into the shadow package"
    )

    offenders: list[str] = []
    for source in (
        *_python_files(_SRC),
        *_python_files(SCRIPTS_DIR),
        *_python_files(_TESTS_DIR),
    ):
        if _matches(_imports(source), (RETIRED_SHADOW_ROOT_MODULE,)):
            offenders.append(source.relative_to(_REPO_ROOT).as_posix())
    assert not offenders, offenders


def test_the_shadow_package_holds_exactly_the_declared_modules() -> None:
    """The package is the five responsibilities, and nothing slipped in."""

    declared = {path.name for path in SHADOW_MODULES}
    actual = {
        path.name
        for path in _python_files(_SHADOW_DIR)
    }
    assert actual == declared, sorted(actual ^ declared)


# -- Guard B -------------------------------------------------------------


def test_the_models_are_plain_data() -> None:
    """Guard B: the models import nothing but the standard library.

    A model that could reach SQLite, the store or the engine would make the data
    shapes depend on how they are persisted or simulated.
    """

    offending = _matches(
        _imports(_SHADOW_DIR / "models.py"),
        (
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.shadow.store",
            "us_quant.shadow.engine",
            "us_quant.shadow.trade_logic",
            "us_quant.trading",
            "PySide6",
            "us_quant.desktop",
        ),
    )
    assert not offending, sorted(offending)

    # It really does define the four facts, so the guard cannot pass by the
    # module having been emptied.
    for name in (
        "ShadowPosition",
        "ShadowFill",
        "ShadowSessionProvenance",
        "ShadowSnapshot",
    ):
        assert hasattr(models_module, name), name


# -- Guard C -------------------------------------------------------------


def test_the_store_does_not_reach_the_engine_or_a_trading_application() -> None:
    """Guard C: persistence knows about rows, not about simulation or policy."""

    offending = _matches(
        _imports(_SHADOW_DIR / "store.py"),
        (
            "us_quant.shadow.engine",
            "us_quant.shadow.trade_logic",
            "us_quant.shadow.config",
            "us_quant.trading.application",
            "us_quant.trading.adapters",
            "us_quant.trading.runtime",
            "us_quant.desktop",
            "ibapi",
            "PySide6",
        ),
    )
    assert not offending, sorted(offending)

    # The dependency that *is* allowed, asserted so the guard documents it.
    assert {"us_quant.shadow.models"} <= _imports(_SHADOW_DIR / "store.py")


# -- Guard D -------------------------------------------------------------


def test_the_shadow_package_cannot_reach_a_broker() -> None:
    """Guard D: the simulator is broker-isolated, by import and by vocabulary."""

    offenders: list[tuple[str, list[str]]] = []
    for path in SHADOW_MODULES:
        offending = _matches(_imports(path), SHADOW_FORBIDDEN_IMPORTS)
        if offending:
            offenders.append(
                (path.relative_to(_REPO_ROOT).as_posix(), sorted(offending))
            )
    assert not offenders, offenders

    for path in SHADOW_MODULES:
        names = _identifier_names(path)
        for forbidden in SHADOW_FORBIDDEN_NAMES:
            assert forbidden not in names, (path.name, forbidden)


# -- Guard E / F ---------------------------------------------------------


def test_the_behaviour_mixin_defines_no_state() -> None:
    """Guard E: ``ShadowTradeLogic`` supplies methods and owns nothing.

    No ``__init__`` means it cannot hold a position, cash, config or store of its
    own, which is what keeps the engine the single state owner.
    """

    methods = _class_methods(trade_logic_module, "ShadowTradeLogic")
    assert "__init__" not in methods, sorted(methods)

    for name, value in vars(
        trade_logic_module.ShadowTradeLogic
    ).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        assert callable(value), f"ShadowTradeLogic.{name} is not a method"


def test_the_engine_and_the_mixin_define_no_method_twice() -> None:
    """Guard F: the split must not leave a second copy of a method behind.

    ``_evaluate_entry`` defined in both files would mean one of them is dead --
    exactly the ``_flatten`` defect Runtime v2A produced -- and the engine would
    silently run whichever the MRO picked.
    """

    engine_methods = _class_methods(engine_module, "ShadowPaperEngine")
    logic_methods = _class_methods(trade_logic_module, "ShadowTradeLogic")

    overlap = (engine_methods & logic_methods) - _OBJECT_METHODS
    assert not overlap, sorted(overlap)

    # Both halves really do hold their share, so the guard cannot pass by one of
    # them having stopped defining anything.
    assert "_evaluate_entry" in logic_methods
    assert "_exit_position" in logic_methods
    assert "on_stream" in engine_methods
    assert "start" in engine_methods
    assert issubclass(engine_module.ShadowPaperEngine, trade_logic_module.ShadowTradeLogic)


def test_the_engine_remains_the_only_state_owner() -> None:
    """The mutable facts are declared on the engine, asserted by name."""

    source = (_SHADOW_DIR / "engine.py").read_text(encoding="utf-8")
    for attribute in (
        "self.session_id",
        "self.active",
        "self.cash",
        "self.realized_pnl",
        "self.daily_realized_pnl",
        "self.position",
        "self.fills",
        "self.trades_today",
        "self._minute_prices",
        "self._marks",
        "self.status",
    ):
        assert attribute in source, attribute

    # The mixin mutates the engine's state through ``self`` -- that is the point
    # of it -- but it must not *create* any of that state: no store, no config,
    # no position of its own to diverge from the engine's.
    logic_source = (_SHADOW_DIR / "trade_logic.py").read_text(encoding="utf-8")
    for construction in (
        "ShadowPaperStore(",
        "ShadowSimulationConfig(",
        "self.store =",
        "self.config =",
    ):
        assert construction not in logic_source, construction


# -- Guard G -------------------------------------------------------------


def test_the_shadow_modules_stay_small() -> None:
    """Guard G: the per-file budgets, and the absolute 400-line ceiling."""

    oversized = [
        f"{name}: {_line_count(_SHADOW_DIR / name)} lines (limit {limit})"
        for name, limit in SHADOW_MODULE_LINE_LIMITS.items()
        if _line_count(_SHADOW_DIR / name) > limit
    ]
    assert not oversized, oversized

    hard = [
        f"{path.name}: {_line_count(path)} lines"
        for path in SHADOW_MODULES
        if _line_count(path) > SHADOW_MODULE_HARD_LIMIT
    ]
    assert not hard, hard


# -- Guard H -------------------------------------------------------------


def test_the_trading_core_still_cannot_import_shadow() -> None:
    """Guard H: Framework v2C's direction rule holds after this round too.

    The packages moved, so this re-checks the rule against the package that now
    exists rather than trusting that the earlier guard still covers it.
    """

    offenders: list[str] = []
    for directory in (
        _SRC / "trading" / "domain",
        _SRC / "trading" / "ports",
        _SRC / "trading" / "application",
        _SRC / "trading" / "runtime",
        _SRC / "trading" / "composition",
    ):
        for path in _python_files(directory):
            if _matches(_imports(path), ("us_quant.shadow",)):
                offenders.append(path.relative_to(_REPO_ROOT).as_posix())
    assert not offenders, offenders


def test_the_shadow_package_creates_no_god_object() -> None:
    """The split must not answer a boundary problem with a grab-bag module."""

    for name in (
        "manager.py",
        "service.py",
        "context.py",
        "application.py",
        "repository.py",
        "common.py",
        "utils.py",
        "ports.py",
    ):
        assert not (_SHADOW_DIR / name).exists(), name


def test_the_store_is_the_only_module_that_touches_sqlite() -> None:
    """Persistence is one module's job, and it stayed that way."""

    users = {
        path.name
        for path in SHADOW_MODULES
        if "sqlite3" in _imports(path)
        or "us_quant.sqlite_support" in _imports(path)
    }
    assert users == {"store.py"}, sorted(users)
    assert hasattr(store_module, "ShadowPaperStore")
