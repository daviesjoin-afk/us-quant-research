"""Architecture boundary tests for Trading Core v2.

These are structural guards, not behaviour tests.  They read the source tree
and assert the dependency directions the architecture depends on, so that a
later change cannot quietly reintroduce a reverse edge.

The rules, from ``docs/TRADING_ARCHITECTURE_V2.md``:

* ``trading/domain`` is pure Python -- no Qt, no broker client, no SQL, no
  sibling layer;
* ``trading/ports`` may import only the standard library and the domain;
* ``desktop_v2/shell`` must not know any business service;
* each core type has exactly one canonical definition;
* the retired ``us_quant.domain`` module stays retired.

A note on what these guards can and cannot see.  They parse imports with
``ast``, which catches a real ``import`` statement anywhere in the file.  They
do not evaluate dynamic imports; the intent is to catch accidental coupling,
not to defeat a determined author.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import pathlib
import subprocess
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_TRADING = _SRC / "trading"

DOMAIN_DIR = _TRADING / "domain"
PORTS_DIR = _TRADING / "ports"

SHELL_PATH = _SRC / "desktop_v2" / "shell.py"
NAVIGATION_PATH = _SRC / "desktop_v2" / "navigation.py"

# The five skeletons this change creates.
SKELETON_PACKAGES = (
    "trading/application",
    "trading/runtime",
    "trading/adapters",
    "trading/adapters/sqlite",
    "trading/composition",
)

# Modules a pure domain must never reach for.
FORBIDDEN_DOMAIN_PREFIXES = (
    "PySide6",
    "ibapi",
    "sqlite3",
    "us_quant.desktop",
    "us_quant.desktop_v2",
    "us_quant.sqlite_support",
    "us_quant.market_data_service",
    "us_quant.ibkr_stream",
    "us_quant.paper",
    "us_quant.ibkr",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
    "us_quant.auto_quant",
    "us_quant.strategy_registry",
    "us_quant.strategy_schema",
    "us_quant.trading.application",
    "us_quant.trading.runtime",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
)

# Modules a port may reach for, beyond the standard library and the domain.
FORBIDDEN_PORT_PREFIXES = (
    "PySide6",
    "ibapi",
    "sqlite3",
    "us_quant.desktop",
    "us_quant.desktop_v2",
    "us_quant.sqlite_support",
    "us_quant.market_data_service",
    "us_quant.ibkr_stream",
    "us_quant.paper",
    "us_quant.ibkr",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
    "us_quant.auto_quant",
    "us_quant.strategy_registry",
    "us_quant.strategy_schema",
    "us_quant.trading.application",
    "us_quant.trading.runtime",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
)

# The canonical types, and the module each must be defined in *within the
# domain package*.
CANONICAL_TYPES = {
    "RiskAccountSnapshot": "account.py",
    "BrokerAccountSnapshot": "account.py",
    "BrokerPositionSnapshot": "account.py",
    "BrokerAccountPortfolio": "account.py",
    "BrokerDiagnostic": "account.py",
    "Position": "account.py",
    "BrokerConnectionState": "account.py",
    "Bar": "market.py",
    "MarketSlice": "market.py",
    "MarketQuote": "market.py",
    "MarketSnapshot": "market.py",
    "MarketSubscription": "market.py",
    "MarketDataHealth": "market.py",
    "Side": "orders.py",
    "OrderStatus": "orders.py",
    "OrderIntent": "orders.py",
    "OrderEvent": "orders.py",
    "ExecutionFill": "orders.py",
    "RiskDecision": "risk.py",
    "RiskEvaluationRequest": "risk.py",
    "RiskLimits": "risk.py",
    "LayeredRiskLimits": "risk.py",
    "SymbolRiskOverrides": "risk.py",
    "SessionRiskOverrides": "risk.py",
    "resolve_symbol_risk_overrides": "risk.py",
    "resolve_session_risk_overrides": "risk.py",
    "TradeAction": "strategy.py",
    "StrategyIdentity": "strategy.py",
    "TradeProposal": "strategy.py",
    "StrategyStatus": "strategy.py",
    "StrategyMode": "strategy.py",
    "StrategyDefinition": "strategy.py",
    "StrategyVersion": "strategy.py",
    "canonical_parameters_json": "strategy.py",
    "parameter_hash_for": "strategy.py",
    "StrategyParameterError": "strategy_parameters.py",
    "validate_strategy_parameters": "strategy_parameters.py",
    "strategy_schema_summary": "strategy_parameters.py",
    "TradingSessionPhase": "session.py",
    "TradingSnapshot": "session.py",
    "ZERO": "common.py",
    "ONE": "common.py",
    "Environment": "common.py",
}

# Spec 50: these types must have exactly one definition in the whole tree.
# They are the ones whose duplication would create two competing truths about
# live account, position, order or risk state.
GLOBALLY_UNIQUE_TYPES = (
    "RiskAccountSnapshot",
    "BrokerAccountSnapshot",
    "BrokerPositionSnapshot",
    "BrokerAccountPortfolio",
    "OrderIntent",
    "RiskDecision",
    "RiskLimits",
    "LayeredRiskLimits",
    "Position",
    # Strategy governance is the same kind of thing: two ``StrategyVersion``
    # definitions would mean two readings of one version's status.
    "StrategyVersion",
    "StrategyStatus",
)

# ``ALLOWED_TRANSITIONS`` is deliberately NOT in either table above: the order
# lifecycle in ``oms.py`` legitimately has its own table under the same name.
# The strategy machine is guarded by
# ``test_the_strategy_state_machine_is_domain_owned`` instead.

# Transitional overlaps: none, since Broker/Account v2.
#
# ``MarketQuote`` used to be defined twice: once as the provider-neutral
# domain type and once as the pre-existing IBKR-specific
# ``ibkr_readonly.MarketQuote`` (which carried ``request_id`` and
# ``market_data_type``).  ``ibkr_readonly.py`` is deleted by the Broker/Account
# v2 change, so the overlap is gone rather than merely recorded -- and this
# dict must stay empty: any new duplicate core type fails the guard below
# until it is deliberately written down.
KNOWN_TRANSITIONAL_OVERLAPS: dict[str, tuple[str, str]] = {}


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in directory.rglob("*.py"))


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


def _relative_imports(path: pathlib.Path) -> set[int]:
    """Return the levels of relative imports in ``path`` (``from . import x``)."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.level
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }


# -- package presence -----------------------------------------------------


@pytest.mark.parametrize("package", SKELETON_PACKAGES)
def test_the_trading_layer_packages_exist(package: str) -> None:
    path = _SRC / package
    assert path.is_dir(), f"{package} is missing"
    assert (path / "__init__.py").is_file(), f"{package}/__init__.py is missing"


@pytest.mark.parametrize("package", SKELETON_PACKAGES)
def test_every_trading_package_is_tracked_by_git(package: str) -> None:
    """A source package must not be swallowed by an ignore rule.

    ``.gitignore`` used to carry an unanchored ``runtime/`` entry, which also
    matched ``src/us_quant/trading/runtime/``.  The directory existed locally,
    every test passed, and the package was simply absent from the repository
    -- only a fresh clone revealed it.  This guard asks git directly, so an
    ignore rule that starts covering a source path fails here instead.
    """

    path = _SRC / package / "__init__.py"
    # ``--no-index`` is essential: without it, git reports nothing for a path
    # that is already tracked, so the guard would pass even while the ignore
    # rule matched.  With it, git answers "would this path be ignored?".
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", str(path)],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    # exit 0 means "ignored", exit 1 means "not ignored".
    assert result.returncode == 1, (
        f"{path.relative_to(_REPO_ROOT).as_posix()} is excluded by a "
        ".gitignore rule; the package would be missing from a fresh clone"
    )

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    assert tracked.returncode == 0, (
        f"{path.relative_to(_REPO_ROOT).as_posix()} is not tracked by git"
    )


def test_no_source_path_under_src_is_git_ignored() -> None:
    """Blanket guard: no tracked-looking source file may be ignored."""

    result = subprocess.run(
        ["git", "status", "--ignored", "--porcelain", "--", "src"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    ignored = [
        line[3:].strip()
        for line in result.stdout.splitlines()
        if line.startswith("!!")
    ]
    # Bytecode caches and the editable-install egg-info are expected.
    unexpected = [
        path
        for path in ignored
        if "__pycache__" not in path and "egg-info" not in path
    ]
    assert not unexpected, (
        f"source paths are ignored by .gitignore: {unexpected}"
    )


def test_domain_and_ports_have_the_expected_modules() -> None:
    assert {path.name for path in _python_files(DOMAIN_DIR)} == {
        "__init__.py",
        "account.py",
        "common.py",
        "market.py",
        "orders.py",
        "risk.py",
        "session.py",
        "strategy.py",
        "strategy_parameters.py",
    }
    assert {path.name for path in _python_files(PORTS_DIR)} == {
        "__init__.py",
        "broker_account.py",
        "broker_execution.py",
        "market_data.py",
        "order_repository.py",
        "strategy_repository.py",
    }


# -- domain purity --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    _python_files(DOMAIN_DIR),
    ids=lambda path: path.name,
)
def test_domain_imports_nothing_outward(path: pathlib.Path) -> None:
    offending = _matches(_imports(path), FORBIDDEN_DOMAIN_PREFIXES)
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize(
    "path",
    _python_files(DOMAIN_DIR),
    ids=lambda path: path.name,
)
def test_domain_only_uses_absolute_imports(path: pathlib.Path) -> None:
    """Domain modules import siblings by full path, never relatively."""

    assert not _relative_imports(path), f"{path.name} uses a relative import"


def test_domain_does_not_import_the_ports_or_any_adapter() -> None:
    for path in _python_files(DOMAIN_DIR):
        modules = _imports(path)
        assert not _matches(modules, ("us_quant.trading.ports",))
        assert not _matches(modules, ("us_quant.trading.adapters",))


def test_domain_package_does_not_re_export_its_types() -> None:
    """No star export: every type keeps exactly one import path."""

    source = (DOMAIN_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not any(
                alias.name == "*" for alias in node.names
            ), "domain/__init__.py must not star-export"


# -- ports purity ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    _python_files(PORTS_DIR),
    ids=lambda path: path.name,
)
def test_ports_import_only_stdlib_typing_and_domain(path: pathlib.Path) -> None:
    offending = _matches(_imports(path), FORBIDDEN_PORT_PREFIXES)
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize(
    "path",
    _python_files(PORTS_DIR),
    ids=lambda path: path.name,
)
def test_ports_import_the_domain_by_full_path(path: pathlib.Path) -> None:
    modules = _imports(path)
    for module in modules:
        if module.startswith("us_quant."):
            assert module.startswith("us_quant.trading.domain"), (
                f"{path.name} imports non-domain package {module}"
            )


# -- single canonical definition ------------------------------------------


def _definitions(name: str) -> list[pathlib.Path]:
    """Every module under ``src/us_quant`` that defines ``name`` at top level."""

    found: list[pathlib.Path] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and (
                node.name == name
            ):
                found.append(path)
                break
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                found.append(path)
                break
            if isinstance(node, ast.AnnAssign) and (
                isinstance(node.target, ast.Name)
                and node.target.id == name
            ):
                found.append(path)
                break
    return found


@pytest.mark.parametrize("name", sorted(CANONICAL_TYPES))
def test_each_core_type_is_defined_in_its_domain_module(name: str) -> None:
    """Each type lives in the domain module that owns it."""

    found = _definitions(name)
    assert found, f"{name} is not defined anywhere under src/us_quant"
    domain_hits = [path for path in found if path.parent == DOMAIN_DIR]
    assert len(domain_hits) == 1, (
        f"{name} must be defined once in trading/domain, found in "
        f"{[str(p.relative_to(_SRC)) for p in found]}"
    )
    assert domain_hits[0].name == CANONICAL_TYPES[name], (
        f"{name} should live in {CANONICAL_TYPES[name]}, "
        f"found in {domain_hits[0].name}"
    )


@pytest.mark.parametrize("name", GLOBALLY_UNIQUE_TYPES)
def test_no_duplicate_domain_type_anywhere_in_the_tree(name: str) -> None:
    """Spec 50: the live-state types have exactly one definition, full stop."""

    found = _definitions(name)
    assert len(found) == 1, (
        f"{name} is defined in {[str(p.relative_to(_SRC)) for p in found]}; "
        "exactly one canonical definition is allowed"
    )
    assert found[0].parent == DOMAIN_DIR


def test_transitional_overlaps_are_exactly_the_documented_ones() -> None:
    """Any duplicated core type must be a declared, explained overlap.

    This is what keeps the exception list from silently growing: adding a
    second definition of a core type fails here until it is written down.
    """

    duplicated = {
        name: sorted(
            path.relative_to(_SRC).as_posix()
            for path in _definitions(name)
        )
        for name in CANONICAL_TYPES
        if len(_definitions(name)) > 1
    }
    expected = {
        name: sorted(
            [
                f"trading/domain/{domain_module}",
                f"{legacy_module}",
            ]
        )
        for name, (domain_module, legacy_module) in (
            KNOWN_TRANSITIONAL_OVERLAPS.items()
        )
    }
    assert duplicated == expected


def test_known_overlaps_stay_vendor_specific_not_duplicated_domain_types() -> None:
    """The legacy side of an overlap must not be a copy of the domain type."""

    for name, (_domain_module, legacy_module) in (
        KNOWN_TRANSITIONAL_OVERLAPS.items()
    ):
        source = (_SRC / legacy_module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        fields: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                for statement in node.body:
                    if isinstance(statement, ast.AnnAssign) and isinstance(
                        statement.target, ast.Name
                    ):
                        fields.add(statement.target.id)
        assert fields, f"{name} not found in {legacy_module}"
        # It carries vendor-only fields, so it is not the domain type.
        assert fields - {
            "symbol",
            "bid",
            "ask",
            "last",
            "close",
        }, f"{legacy_module}.{name} looks like a duplicate domain type"


# -- retirement of the old architecture -----------------------------------


def test_the_legacy_domain_module_is_gone() -> None:
    assert not (_SRC / "domain.py").exists(), (
        "src/us_quant/domain.py must not exist; its types moved to "
        "us_quant.trading.domain"
    )


def test_the_unified_workflow_module_is_gone() -> None:
    assert not (_SRC / "unified_workflow_ui.py").exists(), (
        "src/us_quant/unified_workflow_ui.py was replaced by desktop_v2"
    )


def test_no_module_still_imports_the_legacy_domain_or_unified_shell() -> None:
    """Scan the whole source tree for imports of the retired modules."""

    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        modules = _imports(path)
        if "us_quant.domain" in modules:
            offenders.append(f"{path.relative_to(_SRC)} imports us_quant.domain")
        if "us_quant.unified_workflow_ui" in modules:
            offenders.append(
                f"{path.relative_to(_SRC)} imports us_quant.unified_workflow_ui"
            )
    assert not offenders, offenders


def test_no_module_reintroduces_the_legacy_ui_environment_switch() -> None:
    """The old/new UI toggle is gone; there is only Desktop UI v2."""

    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "US_QUANT_LEGACY_UI" in source:
            offenders.append(str(path.relative_to(_SRC)))
    assert not offenders, offenders


def test_desktop_has_no_legacy_shell_builders() -> None:
    desktop_source = (_SRC / "desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(desktop_source)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for removed in (
        "_build_legacy_workspace",
        "_build_unified_workflow",
        "_workflow_tabs",
        "_workflow_scroll_page",
        "_workspace_tabs",
    ):
        assert removed not in methods, f"MainWindow.{removed} should be deleted"


# -- desktop_v2 shell -----------------------------------------------------


def test_shell_does_not_import_application_runtime_or_adapters() -> None:
    modules = _imports(SHELL_PATH)
    offending = _matches(
        modules,
        (
            "us_quant.trading.application",
            "us_quant.trading.runtime",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
        ),
    )
    assert not offending, sorted(offending)


def test_shell_and_navigation_are_the_only_desktop_v2_modules() -> None:
    """The v2 package holds the shell, the route table and native pages.

    ``pages/account.py`` was the first genuinely native v2 page;
    ``pages/strategy.py`` is the second, ``pages/risk.py`` the third, and
    ``pages/execution/`` the fourth -- that route is a package rather than a
    module because it has four distinct jobs (view models, a Qt-free projection,
    the detail tables and the page), and one module holding all four would be the
    400-line page this migration exists to avoid.  None of those routes reuses a
    legacy builder from ``MainWindow`` any more.
    """

    desktop_v2 = _SRC / "desktop_v2"
    relative = {
        path.relative_to(desktop_v2).as_posix()
        for path in _python_files(desktop_v2)
    }
    assert relative == {
        "__init__.py",
        "navigation.py",
        "shell.py",
        "pages/__init__.py",
        "pages/account.py",
        "pages/risk.py",
        "pages/strategy.py",
        "pages/execution/__init__.py",
        "pages/execution/controls.py",
        "pages/execution/models.py",
        "pages/execution/page.py",
        "pages/execution/presenter.py",
        "pages/execution/rows.py",
        "pages/execution/tables.py",
    }


def test_navigation_does_not_import_qt_or_any_business_module() -> None:
    """Navigation is data: it must stay importable without a UI toolkit."""

    modules = _imports(NAVIGATION_PATH)
    assert not _matches(modules, ("PySide6",))
    for module in modules:
        if module.startswith("us_quant."):
            pytest.fail(f"navigation.py imports {module}")


def test_main_window_is_still_the_composition_root_for_now() -> None:
    """Transitional: MainWindow builds the v2 pages and hands them to the shell."""

    desktop_source = (_SRC / "desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(desktop_source)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_build_v2_pages" in methods
    assert "DesktopShellV2" in desktop_source


# -- Market Data v2 -------------------------------------------------------

# The v1 market data architecture, retired by the market data migration.  None
# of these may be imported or defined anywhere any more; the guards below
# check both, because a module that is merely unreferenced can still be
# resurrected by a later change.
RETIRED_MARKET_DATA_MODULES = (
    "us_quant.market_data_service",
    "us_quant.ibkr_stream",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
)

RETIRED_MARKET_DATA_NAMES = (
    "MarketDataService",
    "MarketDataRequest",
    "MarketDataServiceSnapshot",
    "MarketDataStreamActive",
)

#: The transport DTOs survive, but only inside the adapter layer.  They are
#: provider-shaped (ISO strings, vendor market-data numbers), so they must
#: never be defined anywhere a non-adapter module could import them from.
ADAPTER_INTERNAL_NAMES = {
    "StreamQuote": "trading/adapters/market_data_state.py",
    "StreamSnapshot": "trading/adapters/market_data_state.py",
}

MARKET_DATA_APPLICATION = _TRADING / "application" / "market_data.py"
MARKET_DATA_COMPOSITION = _TRADING / "composition" / "market_data.py"
MARKET_DATA_ADAPTERS = _TRADING / "adapters"

#: Adapter modules under ``trading/adapters`` that do NOT implement
#: ``MarketDataPort``: the package inits, the shared transport state, the
#: read-only account chain, the SQLite strategy repository, and -- since
#: Execution v2 -- the execution chain (the IBKR order adapter, its gateway
#: bridge, the SQLite order store and the two shared helpers the execution
#: adapters use).  Listed by relative path so a new module in any of those
#: packages cannot slip through the market-data surface guard by sharing a
#: filename.
NON_MARKET_DATA_ADAPTER_MODULES = {
    "__init__.py",
    "market_data_state.py",
    "clock.py",
    "order_status_mapping.py",
    "ibkr/__init__.py",
    "ibkr/account.py",
    "ibkr/execution.py",
    "ibkr/execution_gateway.py",
    "ibkr/support.py",
    "alpaca/__init__.py",
    "finnhub/__init__.py",
    "sqlite/__init__.py",
    "sqlite/order_repository.py",
    "sqlite/strategy_repository.py",
}


def _all_source_files() -> list[pathlib.Path]:
    return sorted(_SRC.rglob("*.py"))


def test_the_retired_market_data_modules_are_gone() -> None:
    """The v1 modules are deleted, not merely unreferenced."""

    for module in RETIRED_MARKET_DATA_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the "
            "market data migration moved its behaviour to trading/"
        )


def test_no_module_imports_a_retired_market_data_module() -> None:
    offenders: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        for retired in RETIRED_MARKET_DATA_MODULES:
            if retired in modules:
                offenders.append(
                    f"{path.relative_to(_SRC)} imports {retired}"
                )
    assert not offenders, offenders


def test_no_module_redefines_a_retired_market_data_name() -> None:
    """The transport DTOs and the v1 service must not come back."""

    offenders: list[str] = []
    for name in RETIRED_MARKET_DATA_NAMES:
        for path in _definitions(name):
            offenders.append(
                f"{path.relative_to(_SRC)} defines {name}"
            )
    assert not offenders, offenders


def test_the_market_data_application_does_not_import_any_adapter() -> None:
    """The load-bearing rule: the application is provider-blind.

    If the application could name an adapter, the port would be decorative and
    the dependency arrow would point outward.
    """

    modules = _imports(MARKET_DATA_APPLICATION)
    offending = _matches(
        modules,
        (
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.ibkr_stream",
            "us_quant.alpaca_stream",
            "us_quant.finnhub_stream",
            "ibapi",
        ),
    )
    assert not offending, sorted(offending)


def test_only_composition_wires_the_concrete_market_data_adapters() -> None:
    """Exactly one module knows both the market data application and adapters.

    Since Broker/Account v2 there are two composition roots, one per chain.
    This guard is scoped to the *market data* application so the account
    composition root does not register as an extra wiring module -- and so a
    new module wiring the market data chain still fails here.
    """

    wiring_modules: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        names_adapters = _matches(
            modules, ("us_quant.trading.adapters",)
        )
        names_application = _matches(
            modules, ("us_quant.trading.application.market_data",)
        )
        if names_adapters and names_application:
            wiring_modules.append(
                path.relative_to(_SRC).as_posix()
            )
    assert wiring_modules == ["trading/composition/market_data.py"], (
        wiring_modules
    )


def test_the_desktop_does_not_import_a_concrete_market_data_adapter() -> None:
    """The UI composes the application and talks only to it."""

    modules = _imports(_SRC / "desktop.py")
    offending = _matches(
        modules,
        (
            "us_quant.trading.adapters",
            "us_quant.ibkr_stream",
            "us_quant.alpaca_stream",
            "us_quant.finnhub_stream",
            "us_quant.market_data_service",
        ),
    )
    assert not offending, sorted(offending)


def test_the_strategy_and_risk_layers_do_not_import_a_market_data_adapter() -> None:
    """Strategy and risk see the domain, never a provider."""

    offenders: list[str] = []
    for path in _all_source_files():
        relative = path.relative_to(_SRC).as_posix()
        if not (
            relative.startswith("us_quant/strategy")
            or relative == "us_quant/strategy_registry.py"
            or relative == "us_quant/auto_quant.py"
            or relative.startswith("trading/domain/strategy")
            or relative.startswith("trading/application/strateg")
            or relative == "trading/ports/strategy_repository.py"
            or relative
            == "trading/adapters/sqlite/strategy_repository.py"
            or relative == "desktop_v2/pages/strategy.py"
        ):
            continue
        offending = _matches(
            _imports(path),
            (
                "us_quant.trading.adapters",
                "us_quant.ibkr_stream",
                "us_quant.alpaca_stream",
                "us_quant.finnhub_stream",
                "us_quant.market_data_service",
            ),
        )
        if offending:
            offenders.append(f"{relative} imports {sorted(offending)}")
    assert not offenders, offenders


def test_the_market_data_adapters_expose_the_port_surface() -> None:
    """Each market data adapter implements ``run``/``stop``/``snapshot``/``health``.

    Scoped to the modules that actually implement ``MarketDataPort``.  The
    package also holds the account adapter (``ibkr/account.py``), the shared
    IBKR support module (``ibkr/support.py``) and the SQLite strategy
    repository (``sqlite/strategy_repository.py``), which are different chains
    with different ports and must not be judged by this surface.
    """

    for path in sorted(MARKET_DATA_ADAPTERS.rglob("*.py")):
        relative = path.relative_to(MARKET_DATA_ADAPTERS).as_posix()
        if relative in NON_MARKET_DATA_ADAPTER_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        classes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name.endswith("Stream")
        ]
        assert classes, f"{relative} defines no stream adapter"
        for klass in classes:
            methods = {
                node.name
                for node in klass.body
                if isinstance(node, ast.FunctionDef)
            }
            for required in ("run", "stop", "snapshot", "health"):
                assert required in methods, (
                    f"{relative}:{klass.name} is missing {required}()"
                )


def test_every_market_data_adapter_module_is_covered_by_the_guard() -> None:
    """The scoped guard above must not be silencing a real adapter.

    If a new market data adapter is added under a name the guard skips, the
    skip list would hide it.  This asserts the skip list is exactly the
    known non-market-data modules.
    """

    present = {
        path.relative_to(MARKET_DATA_ADAPTERS).as_posix()
        for path in MARKET_DATA_ADAPTERS.rglob("*.py")
    }
    covered = present - NON_MARKET_DATA_ADAPTER_MODULES
    assert covered == {
        "ibkr/market_data.py",
        "alpaca/market_data.py",
        "finnhub/market_data.py",
    }, sorted(present)


def test_market_data_state_keeps_the_transport_types_adapter_internal() -> None:
    """``StreamQuote``/``StreamSnapshot`` live in exactly one adapter module.

    They are provider-shaped, so they must not leak upward.  Keeping them in
    one file is what makes "the domain type is the only upper-layer truth"
    checkable.
    """

    definitions = {
        name: [
            path.relative_to(_SRC).as_posix()
            for path in _definitions(name)
        ]
        for name in ADAPTER_INTERNAL_NAMES
    }
    assert definitions == {
        name: [module]
        for name, module in ADAPTER_INTERNAL_NAMES.items()
    }


def test_the_read_only_guard_is_still_present() -> None:
    """The safety boundary must survive the move.

    ``ReadOnlyEClientGuard`` is what makes the market-data connection
    structurally unable to place, cancel or exercise anything.  A relocation
    that dropped it would be a silent safety regression.

    Checked behaviourally *and* structurally: a method that merely exists but
    returns ``None`` would satisfy a name-only assertion while silently
    permitting the very call the guard exists to refuse.
    """

    from us_quant.trading.adapters.market_data_state import (
        ReadOnlyEClientGuard,
        ReadOnlyViolation,
    )

    source = (
        MARKET_DATA_ADAPTERS / "market_data_state.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    klass = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ReadOnlyEClientGuard"
        ),
        None,
    )
    assert klass is not None, "ReadOnlyEClientGuard is missing"
    methods = {
        node.name: node
        for node in klass.body
        if isinstance(node, ast.FunctionDef)
    }

    denied_calls = (
        "placeOrder",
        "placeOrderProtoBuf",
        "cancelOrder",
        "cancelOrderProtoBuf",
        "reqGlobalCancel",
        "exerciseOptions",
        "exerciseOptionsProtoBuf",
        "replaceFA",
        "updateDisplayGroup",
    )
    for denied in denied_calls:
        assert denied in methods, f"{denied} is no longer denied"
        body = ast.unparse(methods[denied])
        # The body must actually refuse: either raise directly or delegate to
        # the shared deny helper.
        assert (
            "ReadOnlyViolation" in body or "_deny(" in body
        ), f"{denied} no longer refuses the call"

    # And the refusal is real, not just spelled correctly.
    guard = ReadOnlyEClientGuard()
    for denied in denied_calls:
        with pytest.raises(ReadOnlyViolation):
            getattr(guard, denied)()


def test_the_ibkr_adapter_mixes_in_the_read_only_guard() -> None:
    """The guard must actually be applied, not just defined."""

    source = (MARKET_DATA_ADAPTERS / "ibkr" / "market_data.py").read_text(
        encoding="utf-8"
    )
    assert "ReadOnlyEClientGuard" in source, (
        "the IBKR stream no longer applies ReadOnlyEClientGuard"
    )


# -- Broker / Account v2 --------------------------------------------------

# The v1 account architecture, retired by the broker account migration.  Both
# modules are deleted outright: no compatibility re-export, no shim, no
# "deprecated" import path left behind.  A module that is merely unreferenced
# can still be resurrected, so existence is checked directly.
RETIRED_ACCOUNT_MODULES = (
    "us_quant.ibkr_readonly",
    "us_quant.portfolio_view",
)

# The presentation DTOs and the account/market-data coupling that went with
# them.  ``intraday_market_data_reasons`` is in the list because the account
# chain used to answer a market-readiness question -- Market Data v2 owns that
# answer now.
RETIRED_ACCOUNT_NAMES = (
    "IBKRReadOnlySnapshot",
    "AccountView",
    "PositionView",
    "PortfolioView",
    "intraday_market_data_reasons",
    "build_portfolio_view",
    "snapshot_to_redacted_dict",
    "collect_readonly_snapshot",
    # The generic name that invited a risk calculation to be read as broker
    # truth.  It must not come back under this name.
    "AccountSnapshot",
)

ACCOUNT_APPLICATION = _TRADING / "application" / "accounts.py"
ACCOUNT_COMPOSITION = _TRADING / "composition" / "accounts.py"
ACCOUNT_ADAPTER = _TRADING / "adapters" / "ibkr" / "account.py"
ACCOUNT_PAGE = _SRC / "desktop_v2" / "pages" / "account.py"
BROKER_ACCOUNT_PORT = _TRADING / "ports" / "broker_account.py"


def _identifier_names(path: pathlib.Path) -> set[str]:
    """Every *code* name and attribute the module mentions.

    Read from the AST rather than the text so a docstring that names a
    forbidden symbol -- explaining what was removed, for instance -- does not
    trip a guard.  A guard that failed on documentation would push authors to
    stop documenting removals, which is the opposite of what is wanted.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _attribute_calls(path: pathlib.Path) -> set[str]:
    """Attribute names used in a *call*, e.g. ``app.reqMktData(...)``."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(
            node.func, ast.Attribute
        ):
            calls.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    return calls


def test_the_retired_account_modules_are_gone() -> None:
    """``ibkr_readonly`` and ``portfolio_view`` are deleted, not orphaned."""

    for module in RETIRED_ACCOUNT_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the "
            "broker account migration moved its behaviour to trading/"
        )


def test_no_module_imports_a_retired_account_module() -> None:
    offenders: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        for retired in RETIRED_ACCOUNT_MODULES:
            if retired in modules:
                offenders.append(
                    f"{path.relative_to(_SRC)} imports {retired}"
                )
    assert not offenders, offenders


def test_no_module_redefines_a_retired_account_name() -> None:
    """The v1 DTOs and the account/market-data coupling must not return."""

    offenders: list[str] = []
    for name in RETIRED_ACCOUNT_NAMES:
        for path in _definitions(name):
            offenders.append(f"{path.relative_to(_SRC)} defines {name}")
    assert not offenders, offenders


def test_the_account_adapter_never_requests_market_data() -> None:
    """The account chain asks the broker about the account, nothing else.

    This is the load-bearing separation: the v1 collector requested market
    data and ran a SPY/QQQ readiness check from the account read, which is
    how an account refresh came to overwrite the market badge.  Market
    readiness belongs to Market Data v2 exclusively.
    """

    source = ACCOUNT_ADAPTER.read_text(encoding="utf-8")
    for forbidden in (
        "reqMarketDataType",
        "reqMktData",
        "cancelMktData",
        "marketDataType",
        "tickPrice",
        "TickTypeEnum",
        "reqContractDetails",
    ):
        assert forbidden not in _identifier_names(ACCOUNT_ADAPTER), (
            f"the account adapter must not use {forbidden}"
        )
        assert forbidden not in _attribute_calls(ACCOUNT_ADAPTER), (
            f"the account adapter must not call {forbidden}"
        )
    # And the module really does document the removal, so the guard above is
    # not passing because the explanation was deleted.
    assert "no market data" in source


def test_the_account_adapter_keeps_vendor_types_private() -> None:
    """Raw IBKR DTOs stay inside the adapter and are not exported."""

    source = ACCOUNT_ADAPTER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    exported = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        ),
        None,
    )
    assert exported is not None, "the adapter declares no __all__"
    names = {
        element.value
        for element in exported.value.elts  # type: ignore[attr-defined]
    }
    assert names == {"IBKRAccountAdapter"}, names


def test_the_account_application_does_not_import_an_adapter_or_qt() -> None:
    """The application is provider-blind and UI-free."""

    offending = _matches(
        _imports(ACCOUNT_APPLICATION),
        (
            "PySide6",
            "ibapi",
            "us_quant.trading.adapters",
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.paper",
            "us_quant.ibkr_paper_orders",
            "us_quant.ibkr_paper_gateway",
            "us_quant.trading.application.market_data",
        ),
    )
    assert not offending, sorted(offending)


def test_the_account_application_does_not_import_the_ibkr_config_module() -> None:
    """It owns the config *value*; it does not need the vendor module.

    ``IBKRConnectionConfig`` is imported, which is the one deliberate
    exception -- the config type is the connection contract.  What must not
    appear is an adapter, a stream or ``ibapi``.
    """

    modules = _imports(ACCOUNT_APPLICATION)
    assert "us_quant.ibkr" in modules
    assert "us_quant.trading.adapters" not in modules
    assert "ibapi" not in modules


def test_the_account_page_imports_no_business_service() -> None:
    """The page renders; it does not reach for a runtime or a transport."""

    offending = _matches(
        _imports(ACCOUNT_PAGE),
        (
            "PySide6.QtNetwork",
            "ibapi",
            "us_quant.trading.adapters",
            "us_quant.trading.application",
            "us_quant.trading.composition",
            "us_quant.ibkr",
            "us_quant.paper_trading_service",
            "us_quant.paper_session",
            "us_quant.paper_workflow",
            "us_quant.ibkr_paper_orders",
            "us_quant.ibkr_paper_gateway",
            "us_quant.auto_quant",
            "us_quant.risk",
            "us_quant.strategy_registry",
        ),
    )
    assert not offending, sorted(offending)


def test_only_composition_roots_wire_adapters_into_applications() -> None:
    """Exactly the composition roots know both an application and an adapter.

    Every other module must depend on one side or the other.  A module
    appearing here that is not a composition root is a caller that reached
    through the application to a concrete implementation.
    """

    wiring_modules: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        names_adapters = _matches(
            modules, ("us_quant.trading.adapters",)
        )
        names_application = _matches(
            modules, ("us_quant.trading.application",)
        )
        if names_adapters and names_application:
            wiring_modules.append(path.relative_to(_SRC).as_posix())
    assert wiring_modules == [
        "trading/composition/accounts.py",
        "trading/composition/execution.py",
        "trading/composition/market_data.py",
        "trading/composition/strategies.py",
    ], wiring_modules


def test_the_desktop_does_not_import_a_concrete_account_adapter() -> None:
    """The UI composes the application and talks only to it."""

    offending = _matches(
        _imports(_SRC / "desktop.py"),
        ("us_quant.trading.adapters",),
    )
    assert not offending, sorted(offending)


def test_the_market_data_application_has_no_ibkr_config_dependency() -> None:
    """The transitional dependency from Market Data v2 is fully removed.

    ``MarketDataApplication`` used to import ``IBKRConnectionConfig`` and
    hold ``self.config`` / ``update_config`` while Broker/Account v2 did not
    exist.  It must now know nothing about the IBKR endpoint at all.
    """

    source = MARKET_DATA_APPLICATION.read_text(encoding="utf-8")
    names = _identifier_names(MARKET_DATA_APPLICATION)
    assert "IBKRConnectionConfig" not in names
    assert "config" not in {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr == "config"
    }
    assert "update_config" not in names
    assert "ensure_config_update_allowed" not in names
    assert "ensure_reconfiguration_allowed" in names

    offending = _matches(
        _imports(MARKET_DATA_APPLICATION),
        ("us_quant.ibkr", "ibapi"),
    )
    assert not offending, sorted(offending)


def test_the_broker_account_port_is_read_only() -> None:
    """No submit, cancel or arm may appear on the account port."""

    source = BROKER_ACCOUNT_PORT.read_text(encoding="utf-8")
    names = _identifier_names(BROKER_ACCOUNT_PORT)
    for forbidden in (
        "placeOrder",
        "cancelOrder",
        "reqGlobalCancel",
        "exerciseOptions",
        "replaceFA",
    ):
        assert forbidden not in names, (
            f"BrokerAccountPort must stay read-only, found {forbidden}"
        )
    # The port surface is ``refresh`` and nothing else.
    port = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerAccountPort"
    )
    assert {
        node.name
        for node in port.body
        if isinstance(node, ast.FunctionDef)
    } == {"refresh"}
    assert "BrokerExecutionPort" in source, (
        "the port must document that execution is a separate, unmigrated port"
    )


def test_the_shared_ibkr_support_module_is_importable_by_both_channels() -> None:
    """``mask_account_id`` has exactly one definition.

    The Paper order service and the account adapter both need it, and two
    copies could drift -- the one that drifted being the one that leaked an
    account number.
    """

    definitions = _definitions("mask_account_id")
    assert len(definitions) == 1, [
        str(path.relative_to(_SRC)) for path in definitions
    ]
    assert definitions[0] == _TRADING / "adapters" / "ibkr" / "support.py"


# -- Strategy v2 ----------------------------------------------------------

# The v1 strategy governance modules, retired by the strategy migration.
# Nothing may import them and nothing may define them again: a module that is
# merely unreferenced can still be resurrected by a later change.
RETIRED_STRATEGY_MODULES = (
    "us_quant.strategy_registry",
    "us_quant.strategy_schema",
)

# The retired MainWindow strategy page and its controller handlers.  The two
# combo accessors (``_selected_shadow_strategy_record`` /
# ``_selected_auto_strategy_record``) are NOT listed: nine call sites still read
# them, so they were re-pointed at ``StrategySelectionService`` rather than
# deleted, and ``test_the_combo_accessors_read_the_selection_service`` below
# pins that they no longer reach for a widget.
RETIRED_STRATEGY_METHODS = (
    "_strategy_manager_tab",
    "_populate_strategy_registry",
    "_selected_strategy_record",
    "_strategy_registry_selection_changed",
    "_clone_strategy_version",
    "_transition_selected_strategy",
)

STRATEGY_DOMAIN = _TRADING / "domain" / "strategy.py"
STRATEGY_PARAMETERS = _TRADING / "domain" / "strategy_parameters.py"
STRATEGY_PORT = _TRADING / "ports" / "strategy_repository.py"
STRATEGY_SQLITE_REPOSITORY = (
    _TRADING / "adapters" / "sqlite" / "strategy_repository.py"
)
STRATEGY_APPLICATION = _TRADING / "application" / "strategies.py"
STRATEGY_DEFAULTS = _TRADING / "application" / "strategy_defaults.py"
STRATEGY_SELECTION = _TRADING / "application" / "strategy_selection.py"
STRATEGY_COMPOSITION = _TRADING / "composition" / "strategies.py"
STRATEGY_PAGE = _SRC / "desktop_v2" / "pages" / "strategy.py"

#: Every module the strategy migration creates or rewrites.
STRATEGY_V2_MODULES = (
    STRATEGY_DOMAIN,
    STRATEGY_PARAMETERS,
    STRATEGY_PORT,
    STRATEGY_SQLITE_REPOSITORY,
    STRATEGY_APPLICATION,
    STRATEGY_DEFAULTS,
    STRATEGY_SELECTION,
    STRATEGY_COMPOSITION,
    STRATEGY_PAGE,
)

#: Symbols that would couple strategy governance to order execution.  A
#: strategy that can build a ``PaperOrderIntent`` or hand one to an
#: ``order_sink`` has an execution path again, which is the coupling this
#: migration exists to confine.
EXECUTION_COUPLING_NAMES = (
    "BrokerExecutionPort",
    "IBKRPaperOrderService",
    "PaperOrderIntent",
    "PaperTradingService",
    "new_paper_order_intent",
    "order_sink",
)

#: Spec 90/91: the exact set of strategy-related modules still allowed to be
#: execution-coupled, as repository-relative POSIX paths.  Execution v2 emptied
#: it: the last strategy that could build an order or hand one to a sink now
#: produces a ``TradeProposal`` and asks the risk layer.  It must stay empty --
#: an exception here is a strategy with an execution path again, and the guard
#: below fails until someone writes the widening down on purpose.
TRANSITIONAL_EXECUTION_COUPLED_STRATEGY_FILES: set[str] = set()

#: The fields ``TradeProposal`` is allowed to have.  Order identity is
#: deliberately absent: a proposal that carried one could be submitted.
TRADE_PROPOSAL_FIELDS = {
    "strategy",
    "symbol",
    "action",
    "desired_quantity",
    "reference_price",
    "reason",
    "generated_at",
}

TRADE_PROPOSAL_FORBIDDEN_FIELDS = {
    "order_id",
    "broker_order_id",
    "client_order_id",
    "tif",
    "outsideRth",
    "transmit",
    "risk_approved",
}

#: The fields ``StrategyVersion`` is allowed to have.
STRATEGY_VERSION_FIELDS = {
    "definition",
    "identity",
    "semver",
    "status",
    "mode",
    "parameters",
    "universe_hash",
    "code_hash",
    "risk_budget_pct",
    "gate_passed",
    "gate_reason",
    "created_at",
    "updated_at",
}


def _class_fields(path: pathlib.Path, name: str) -> set[str]:
    """The annotated field names of one dataclass body."""

    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
    raise AssertionError(f"{name} is not defined in {path.name}")


def _class_methods(path: pathlib.Path, name: str) -> set[str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.name
                for statement in node.body
                if isinstance(statement, ast.FunctionDef)
            }
    raise AssertionError(f"{name} is not defined in {path.name}")


def _strategy_related_files() -> list[pathlib.Path]:
    """Modules the execution-coupling guard judges.

    Scoped to strategy-named source plus AutoQuant, which is the module the
    exception exists for.
    """

    judged: list[pathlib.Path] = []
    for path in _all_source_files():
        relative = path.relative_to(_SRC).as_posix()
        if (
            relative.startswith("strategy")
            or relative.startswith("trading/domain/strategy")
            or relative.startswith("trading/application/strateg")
            or relative.startswith("trading/composition/strateg")
            or relative.startswith("trading/adapters/sqlite/strateg")
            or relative == "trading/ports/strategy_repository.py"
            or relative == "desktop_v2/pages/strategy.py"
            or relative == "auto_quant.py"
            or relative == "auto_intraday.py"
            or relative == "targeted_intraday.py"
        ):
            judged.append(path)
    return judged


def test_the_retired_strategy_modules_are_gone() -> None:
    """The v1 modules are deleted, not merely unreferenced."""

    for module in RETIRED_STRATEGY_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the "
            "strategy migration moved its behaviour to trading/"
        )


def test_no_module_imports_a_retired_strategy_module() -> None:
    """No production module may import the retired strategy modules."""

    offenders: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        for retired in RETIRED_STRATEGY_MODULES:
            if retired in modules:
                offenders.append(
                    f"{path.relative_to(_SRC)} imports {retired}"
                )
    assert not offenders, offenders


def test_the_strategy_repository_port_is_the_only_governance_surface() -> None:
    """The port exposes exactly the four store operations and no policy."""

    assert _class_methods(STRATEGY_PORT, "StrategyRepositoryPort") == {
        "insert_version",
        "list_versions",
        "get_version",
        "update_deployment",
    }
    names = _identifier_names(STRATEGY_PORT)
    for forbidden in (
        "ALLOWED_TRANSITIONS",
        "gate_passed",
        "transition",
        "clone",
        "can_clone",
        "is_legal",
        "sqlite3",
    ):
        assert forbidden not in names, (
            f"StrategyRepositoryPort must not answer policy, found {forbidden}"
        )


def test_the_strategy_application_does_not_import_sqlite_or_an_adapter() -> None:
    """The load-bearing rule: the application is storage-blind."""

    offending = _matches(
        _imports(STRATEGY_APPLICATION),
        (
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.trading.runtime",
            "PySide6",
        ),
    )
    assert not offending, sorted(offending)


def test_the_strategy_selection_service_only_depends_on_the_application() -> None:
    """Selection is policy over the application, not over a store."""

    offending = _matches(
        _imports(STRATEGY_SELECTION) | _imports(STRATEGY_DEFAULTS),
        (
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "PySide6",
        ),
    )
    assert not offending, sorted(offending)


def test_the_strategy_domain_modules_are_pure() -> None:
    """Strategy governance types know nothing about storage or the UI."""

    for path in (STRATEGY_DOMAIN, STRATEGY_PARAMETERS):
        offending = _matches(
            _imports(path),
            (
                "sqlite3",
                "us_quant.sqlite_support",
                "us_quant.trading.adapters",
                "us_quant.trading.application",
                "us_quant.trading.ports",
                "PySide6",
            ),
        )
        assert not offending, f"{path.name} imports {sorted(offending)}"


def test_only_strategy_composition_wires_the_concrete_strategy_repository() -> (
    None
):
    """Exactly one module knows both the strategy application and its adapter."""

    wiring_modules: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        if _matches(
            modules, ("us_quant.trading.adapters.sqlite.strategy_repository",)
        ) and _matches(modules, ("us_quant.trading.application",)):
            wiring_modules.append(path.relative_to(_SRC).as_posix())
    # The sqlite package's own ``__init__`` re-exports the adapter, so it
    # names the concrete class too -- but it knows no application service.
    assert wiring_modules == ["trading/composition/strategies.py"], (
        wiring_modules
    )


def test_the_desktop_does_not_import_the_sqlite_strategy_adapter() -> None:
    """The UI composes the application and talks only to it.

    ``desktop.py`` does import ``sqlite3`` itself for an unrelated history-job
    error handler, so this guard is about the strategy store: the window must
    reach it through ``build_strategy_application`` and never name the
    concrete repository class.
    """

    desktop = _SRC / "desktop.py"
    offending = _matches(
        _imports(desktop),
        (
            "us_quant.trading.adapters",
            "us_quant.sqlite_support",
        ),
    )
    assert not offending, sorted(offending)
    assert "SQLiteStrategyRepository" not in _identifier_names(desktop)
    assert "build_strategy_application" in _identifier_names(desktop)


def test_the_strategy_page_imports_no_business_service() -> None:
    """The page renders; it does not reach for a runtime or a store."""

    offending = _matches(
        _imports(STRATEGY_PAGE),
        (
            "PySide6.QtNetwork",
            "ibapi",
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.application",
            "us_quant.trading.composition",
            "us_quant.ibkr",
            "us_quant.paper_trading_service",
            "us_quant.paper_session",
            "us_quant.paper_workflow",
            "us_quant.ibkr_paper_orders",
            "us_quant.ibkr_paper_gateway",
            "us_quant.auto_quant",
            "us_quant.risk",
            "us_quant.strategy_registry",
            "us_quant.strategy_schema",
        ),
    )
    assert not offending, sorted(offending)
    assert _matches(_imports(STRATEGY_PAGE), ("us_quant.trading.domain",)), (
        "the page must render domain types, not invent its own view models"
    )


def test_no_strategy_v2_module_imports_paper_execution() -> None:
    """Spec 89: the new strategy surface has no execution path."""

    offenders: list[str] = []
    for path in STRATEGY_V2_MODULES:
        used = _identifier_names(path) & set(EXECUTION_COUPLING_NAMES)
        if used:
            offenders.append(
                f"{path.relative_to(_SRC).as_posix()} uses {sorted(used)}"
            )
    assert not offenders, offenders


def test_auto_quant_is_the_only_execution_coupled_strategy_module() -> None:
    """Spec 90/91: the transitional exception is an exact, closed set.

    Any second strategy-related module that constructs a ``PaperOrderIntent``
    or holds an ``order_sink`` fails here.  Execution v2 emptied the
    exception, so any coupling at all now fails: there is no strategy with an
    execution path left, and adding one back means writing it down here first.
    """

    coupled: set[str] = set()
    for path in _strategy_related_files():
        used = _identifier_names(path) & set(EXECUTION_COUPLING_NAMES)
        if used:
            coupled.add(path.relative_to(_REPO_ROOT).as_posix())
    assert coupled == TRANSITIONAL_EXECUTION_COUPLED_STRATEGY_FILES, (
        "execution-coupled strategy modules changed",
        sorted(coupled),
        sorted(TRANSITIONAL_EXECUTION_COUPLED_STRATEGY_FILES),
    )


def test_trade_proposal_cannot_gain_order_identity() -> None:
    """Spec 14: no field may make a proposal submittable."""

    fields = _class_fields(STRATEGY_DOMAIN, "TradeProposal")
    assert fields == TRADE_PROPOSAL_FIELDS
    assert not (fields & TRADE_PROPOSAL_FORBIDDEN_FIELDS)


def test_strategy_version_keeps_its_governance_fields() -> None:
    """The version record carries governance state and nothing ordery."""

    fields = _class_fields(STRATEGY_DOMAIN, "StrategyVersion")
    assert fields == STRATEGY_VERSION_FIELDS
    assert not (fields & TRADE_PROPOSAL_FORBIDDEN_FIELDS)


def test_the_strategy_state_machine_is_domain_owned() -> None:
    """Spec 5: the transition table lives in the domain, not in a store."""

    source = STRATEGY_DOMAIN.read_text(encoding="utf-8")
    assert "ALLOWED_TRANSITIONS" in source
    names = _identifier_names(STRATEGY_DOMAIN)
    assert {"StrategyStatus", "StrategyMode"} <= names
    # The retired registry had a status called "paused" but no way back out
    # of "stopped"; both must survive the move.
    assert "STOPPED" in names and "LEGACY_INVALIDATED" in names


def test_the_desktop_has_no_legacy_strategy_handlers() -> None:
    """Spec 78: the old page, controller and combo accessors are deleted."""

    methods = {
        node.name
        for node in ast.walk(
            ast.parse((_SRC / "desktop.py").read_text(encoding="utf-8"))
        )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for removed in RETIRED_STRATEGY_METHODS:
        assert removed not in methods, (
            f"MainWindow.{removed} should be deleted"
        )


def test_the_combo_accessors_read_the_selection_service() -> None:
    """Spec 52: the combos are views; the service is the truth.

    The two accessors survived by name because nine call sites depend on them.
    What had to change is where they get their answer: reading
    ``QComboBox.currentData()`` made "which version runs?" depend on which tab
    was on screen, and made the question unanswerable from anywhere that is
    not that widget.
    """

    source = (_SRC / "desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in (
        "_selected_shadow_strategy_record",
        "_selected_auto_strategy_record",
    ):
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        # Only the code, not the docstring: these methods deliberately
        # document what they stopped doing, and a guard that failed on that
        # would push authors to stop documenting the change.
        code = "\n".join(
            segment
            for segment in (
                ast.get_source_segment(source, statement)
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
            )
            if segment
        )
        assert "self.strategy_selection.selected(" in code, (
            f"{name} must ask the selection service"
        )
        for forbidden in (
            "currentData(",
            "get_version(",
            "strategy_registry",
        ):
            assert forbidden not in code, (
                f"{name} still reads {forbidden} instead of the service"
            )


def test_the_desktop_routes_strategy_to_the_native_v2_page() -> None:
    """Spec 77/99: the strategy route is the v2 page, not a builder."""

    source = (_SRC / "desktop.py").read_text(encoding="utf-8")
    assert "StrategyPage" in source, (
        "MainWindow must build the native strategy page"
    )
    assert '"strategy": self.strategy_page' in source, (
        "the strategy route must resolve to the native v2 page"
    )


def test_the_strategy_page_builds_no_legacy_widget_stack() -> None:
    """It renders one page; it does not resurrect the old tab builder."""

    methods = _class_methods(STRATEGY_PAGE, "StrategyPage")
    for forbidden in ("_strategy_manager_tab", "register", "transition"):
        assert forbidden not in methods, (
            f"StrategyPage.{forbidden} would make the page an orchestrator"
        )
    names = _identifier_names(STRATEGY_PAGE)
    assert "Signal" in names, "the page talks back through Qt signals only"


# -- Risk v2 --------------------------------------------------------------

# The v1 risk module, retired by this migration.  Deleted outright: no
# compatibility re-export, no shim, no "deprecated" import path.  A module
# that is merely unreferenced can still be resurrected, so existence is
# checked directly.
RETIRED_RISK_MODULES = ("us_quant.risk",)

#: The retired engine.  Its behaviour moved into ``RiskApplication``; the
#: class itself must not come back under this name, because a second engine is
#: a second set of verdicts.
RETIRED_RISK_NAMES = ("PreTradeRiskEngine",)

RISK_DOMAIN = _TRADING / "domain" / "risk.py"
RISK_APPLICATION = _TRADING / "application" / "risk.py"
RISK_COMPOSITION = _TRADING / "composition" / "risk.py"
RISK_PAGE = _SRC / "desktop_v2" / "pages" / "risk.py"

#: Every module the risk migration creates or rewrites.
RISK_V2_MODULES = (
    RISK_DOMAIN,
    RISK_APPLICATION,
    RISK_COMPOSITION,
    RISK_PAGE,
)

#: Identifiers that would give the risk layer an order surface.  Risk decides
#: *whether* and *how many whole shares*; execution creates order identity,
#: submits, cancels and reconciles.  A risk module naming any of these has
#: taken over a job that is not its own.
RISK_ORDER_SURFACE_NAMES = (
    "BrokerExecutionPort",
    "OrderIntent",
    "OrderRepositoryPort",
    "PaperOrderIntent",
    "cancelOrder",
    "new_paper_order_intent",
    "order_sink",
    "placeOrder",
)

#: The account-risk identifiers AutoQuant is no longer allowed to own.  Each
#: one was a second implementation of a verdict that now has exactly one home.
AUTO_QUANT_RETIRED_RISK_NAMES = (
    "LayeredRiskLimits",
    "PreTradeRiskEngine",
    "SessionRiskOverrides",
    "SymbolRiskOverrides",
    "_effective_risk_multiplier",
    "allow_margin_borrowing",
    "current_risk_exposure",
    "daily_loss_halt_pct",
    "drawdown_halt_pct",
    "max_gross_exposure_pct",
    "max_position_exposure_pct",
    "remaining_risk_exposure",
    "resolve_session_risk_overrides",
    "resolve_symbol_risk_overrides",
    "risk_multipliers",
)

#: The two account-halt ratios, and the only modules whose *code* may name
#: them.  ``config.py`` reads them as TOML keys -- string literals, which this
#: scan does not see -- and the risk page displays them; every decision built
#: on them lives in the application.  A new file appearing in this set is what
#: a second pre-trade risk implementation would look like on its first commit.
RISK_HALT_RATIO_NAMES = ("daily_loss_halt_pct", "drawdown_halt_pct")
RISK_HALT_RATIO_MODULES = {
    "desktop_v2/pages/risk.py",
    "trading/application/risk.py",
    "trading/domain/risk.py",
}

AUTO_QUANT_PATH = _SRC / "auto_quant.py"


def test_the_retired_risk_module_is_gone() -> None:
    """The v1 module is deleted, not merely unreferenced."""

    for module in RETIRED_RISK_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the risk "
            "migration moved its behaviour to trading/"
        )


def test_no_module_imports_the_retired_risk_module() -> None:
    offenders: list[str] = []
    for path in _all_source_files():
        modules = _imports(path)
        for retired in RETIRED_RISK_MODULES:
            if retired in modules:
                offenders.append(
                    f"{path.relative_to(_SRC)} imports {retired}"
                )
    assert not offenders, offenders


def test_no_module_redefines_the_retired_risk_engine() -> None:
    offenders: list[str] = []
    for name in RETIRED_RISK_NAMES:
        for path in _definitions(name):
            offenders.append(f"{path.relative_to(_SRC)} defines {name}")
    assert not offenders, offenders


def test_the_risk_domain_is_pure() -> None:
    """Risk policy must be assertable without a running system."""

    offending = _matches(
        _imports(RISK_DOMAIN),
        (
            "PySide6",
            "ibapi",
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.application",
            "us_quant.trading.composition",
            "us_quant.paper",
            "us_quant.ibkr_paper_orders",
            "us_quant.auto_quant",
            "us_quant.desktop",
            "us_quant.risk",
        ),
    )
    assert not offending, sorted(offending)
    assert not _relative_imports(RISK_DOMAIN)


def test_the_risk_application_is_provider_and_execution_blind() -> None:
    """Spec 113: the application may not build, submit or store an order."""

    offending = _matches(
        _imports(RISK_APPLICATION),
        (
            "PySide6",
            "ibapi",
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.paper",
            "us_quant.ibkr_paper_orders",
            "us_quant.ibkr_paper_gateway",
            "us_quant.paper_trading_service",
            "us_quant.auto_quant",
            "us_quant.desktop",
            "us_quant.trading.ports",
        ),
    )
    assert not offending, sorted(offending)
    used = _identifier_names(RISK_APPLICATION) & set(
        RISK_ORDER_SURFACE_NAMES
    )
    assert not used, sorted(used)


def test_no_risk_v2_module_touches_paper_execution() -> None:
    """Spec 117: the risk layer has no execution path at all."""

    offenders: list[str] = []
    for path in RISK_V2_MODULES:
        used = _identifier_names(path) & set(RISK_ORDER_SURFACE_NAMES)
        if used:
            offenders.append(
                f"{path.relative_to(_SRC).as_posix()} uses {sorted(used)}"
            )
    assert not offenders, offenders


def test_only_composition_and_the_window_name_the_risk_builder() -> None:
    """Exactly one module assembles the risk authority, and the window asks it.

    The composition root is the only place that knows how a
    ``RiskApplication`` is put together; ``desktop.py`` is the only caller.
    A third module appearing here means a second assembly path.
    """

    wiring: list[str] = []
    for path in _all_source_files():
        if "build_risk_application" in _identifier_names(path):
            wiring.append(path.relative_to(_SRC).as_posix())
    assert wiring == [
        "desktop.py",
        "trading/composition/risk.py",
    ], wiring


def test_the_risk_page_imports_no_business_service() -> None:
    """Spec 114: the page renders, it does not enforce or configure."""

    offending = _matches(
        _imports(RISK_PAGE),
        (
            "PySide6.QtNetwork",
            "ibapi",
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.application",
            "us_quant.trading.composition",
            "us_quant.trading.ports",
            "us_quant.ibkr",
            "us_quant.paper_trading_service",
            "us_quant.paper_session",
            "us_quant.paper_workflow",
            "us_quant.ibkr_paper_orders",
            "us_quant.ibkr_paper_gateway",
            "us_quant.auto_quant",
            "us_quant.risk",
        ),
    )
    assert not offending, sorted(offending)
    assert _matches(_imports(RISK_PAGE), ("us_quant.trading.domain",)), (
        "the page must render domain types, not invent its own view models"
    )


def test_auto_quant_no_longer_owns_account_risk() -> None:
    """Spec 89: one authority, and it is not the trading runtime either.

    Each retired name below was a calculation performed inside the old
    ``AutoQuantEngine`` while the same question was also answered by
    ``RiskApplication`` -- two answers no unit test could tell apart, because
    the tests injected the risk argument directly.  Runtime v2A moved the
    session into the trading layer without moving those calculations back in.
    """

    for path in RUNTIME_MODULES:
        used = _identifier_names(path) & set(
            AUTO_QUANT_RETIRED_RISK_NAMES
        )
        assert not used, (path.name, sorted(used))
    # And the verdict really is routed through the risk layer, so the guard
    # above cannot pass by the whole path having been deleted.
    names = _identifier_names(RUNTIME_DISPATCH)
    assert "RiskApplication" in names
    assert "RiskEvaluationRequest" in names
    assert "ExecutionApplication" in names


def test_the_account_halt_ratios_have_one_implementation() -> None:
    """Spec 89/130: a second implementation would show up as a new file."""

    naming: set[str] = set()
    for path in _all_source_files():
        if _identifier_names(path) & set(RISK_HALT_RATIO_NAMES):
            naming.add(path.relative_to(_SRC).as_posix())
    assert naming == RISK_HALT_RATIO_MODULES, sorted(naming)


def test_the_risk_application_consumes_the_risk_snapshot_not_broker_truth() -> (
    None
):
    """The application evaluates estimates; the adapter reports facts."""

    names = _identifier_names(RISK_APPLICATION)
    assert "RiskAccountSnapshot" in names
    assert "BrokerAccountSnapshot" not in names
    assert "BrokerConnectionState" not in names
    assert "BrokerPositionSnapshot" not in names


# -- Execution v2 ---------------------------------------------------------

#: The v1 order modules, retired by this migration.  Deleted outright: no
#: compatibility re-export, no shim, no "deprecated" import path left behind.
#: A module that is merely unreferenced can be resurrected, so existence is
#: checked directly.
RETIRED_EXECUTION_MODULES = (
    "us_quant.ibkr_paper_orders",
    "us_quant.ibkr_paper_gateway",
    "us_quant.paper_order_journal",
)

#: The Paper order DTOs this round deleted.  Orders are domain types now, and
#: a second set of order shapes is how two readings of one order diverge.
RETIRED_EXECUTION_NAMES = (
    "PaperOrderIntent",
    "PaperOrderUpdate",
    "PaperExecution",
    "new_paper_order_intent",
    "IBKRPaperOrderService",
)

EXECUTION_DOMAIN = _TRADING / "domain" / "orders.py"
EXECUTION_APPLICATION = _TRADING / "application" / "execution.py"
EXECUTION_COMPOSITION = _TRADING / "composition" / "execution.py"
EXECUTION_ADAPTER = _TRADING / "adapters" / "ibkr" / "execution.py"
EXECUTION_GATEWAY = _TRADING / "adapters" / "ibkr" / "execution_gateway.py"
EXECUTION_STORE = _TRADING / "adapters" / "sqlite" / "order_repository.py"
EXECUTION_PORTS = (
    _TRADING / "ports" / "broker_execution.py",
    _TRADING / "ports" / "order_repository.py",
)

#: Every module the execution migration creates or rewrites in the trading
#: layers.
EXECUTION_V2_MODULES = (
    EXECUTION_APPLICATION,
    EXECUTION_COMPOSITION,
    EXECUTION_ADAPTER,
    EXECUTION_GATEWAY,
    EXECUTION_STORE,
    _TRADING / "adapters" / "clock.py",
    _TRADING / "adapters" / "order_status_mapping.py",
)

#: What the execution *application* may not reach for.  It orchestrates a
#: verdict, a store and a channel through their ports; naming any of these
#: means it has taken over a job that belongs to an adapter or a window.
EXECUTION_APPLICATION_FORBIDDEN_PREFIXES = (
    "ibapi",
    "sqlite3",
    "PySide6",
    "us_quant.desktop",
    "us_quant.trading.adapters",
    "us_quant.paper_order_models",
    "us_quant.paper_session",
    "us_quant.paper_workflow",
)

#: The identifiers that would let a strategy or a risk module reach the
#: broker channel directly.  ``sqlite3`` is deliberately absent: several
#: legacy root modules own their own stores, which is a different migration's
#: problem, and a guard that cannot pass is a guard that gets weakened.
STRATEGY_EXECUTION_SURFACE_NAMES = (
    "IBKRExecutionAdapter",
    "placeOrder",
    "cancelOrder",
)

EXECUTION_RISK_APPLICATION = _TRADING / "application" / "risk.py"
EXECUTION_RISK_DOMAIN = _TRADING / "domain" / "risk.py"


def test_the_retired_execution_modules_are_gone() -> None:
    for module in RETIRED_EXECUTION_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the "
            "execution migration moved its behaviour to trading/"
        )


def test_no_module_imports_a_retired_execution_module() -> None:
    offenders: list[str] = []
    for path in _all_source_files():
        if path == pathlib.Path(__file__):
            continue
        used = _matches(_imports(path), RETIRED_EXECUTION_MODULES)
        if used:
            offenders.append(
                f"{path.relative_to(_SRC).as_posix()} -> {sorted(used)}"
            )
    assert not offenders, offenders


def test_the_paper_order_dtos_are_gone() -> None:
    """One order vocabulary: the domain's, and nothing beside it."""

    defined: list[str] = []
    for path in _all_source_files():
        used = _identifier_names(path) & set(RETIRED_EXECUTION_NAMES)
        if used:
            defined.append(
                f"{path.relative_to(_SRC).as_posix()} -> {sorted(used)}"
            )
    assert not defined, defined


def test_the_execution_application_is_provider_and_storage_blind() -> None:
    """It may name the domain and the two ports, and nothing below them."""

    offending = _matches(
        _imports(EXECUTION_APPLICATION),
        EXECUTION_APPLICATION_FORBIDDEN_PREFIXES,
    )
    assert not offending, sorted(offending)

    names = _identifier_names(EXECUTION_APPLICATION)
    assert "BrokerExecutionPort" in names
    assert "OrderRepositoryPort" in names
    assert "RiskDecision" in names
    assert "TradeProposal" in names


def test_the_execution_ports_depend_on_the_domain_only() -> None:
    for path in EXECUTION_PORTS:
        modules = _imports(path)
        assert not _matches(
            modules, ("us_quant.trading.adapters",)
        ), path.name
        assert not _matches(modules, ("us_quant.trading.application",))
        assert not _matches(
            modules, ("ibapi", "sqlite3", "PySide6", "us_quant.desktop")
        ), path.name
        assert not _relative_imports(path), path.name


def test_only_the_execution_composition_wires_concrete_execution_pieces() -> (
    None
):
    """One assembly point, or the layers above it can pick their own adapter."""

    for path in (
        EXECUTION_APPLICATION,
        EXECUTION_DOMAIN,
        *RUNTIME_MODULES,
    ):
        modules = _imports(path)
        assert not _matches(modules, ("us_quant.trading.adapters",)), (
            f"{path.name} reaches a concrete adapter"
        )

    names = _identifier_names(EXECUTION_COMPOSITION)
    assert "ExecutionApplication" in names
    assert "SQLiteOrderRepository" in names
    assert "IBKRExecutionAdapter" in names


def test_the_desktop_does_not_import_a_concrete_execution_adapter() -> None:
    """The window composes through the composition root, never by name."""

    desktop = _SRC / "desktop.py"
    names = _identifier_names(desktop)
    for retired in (
        "IBKRExecutionAdapter",
        "SQLiteOrderRepository",
        "IBKRPaperOrderService",
        "PaperOrderJournal",
        "new_paper_order_intent",
    ):
        assert retired not in names, retired
    assert "build_execution_application" in names, (
        "the window must build the execution service through composition"
    )


def test_the_strategy_and_risk_layers_cannot_reach_the_broker_channel() -> None:
    """Strategy proposes, risk decides, execution submits -- and only it."""

    offenders: list[str] = []
    for path in _python_files(_SRC):
        relative = path.relative_to(_SRC).as_posix()
        # The adapters *are* the channel and the composition roots are the one
        # place allowed to name a concrete one.
        if relative.startswith("trading/adapters/"):
            continue
        if relative.startswith("trading/composition/"):
            continue
        if relative == "desktop.py":
            continue
        used = _identifier_names(path) & set(STRATEGY_EXECUTION_SURFACE_NAMES)
        if used:
            offenders.append(f"{relative} -> {sorted(used)}")
    assert not offenders, offenders

    for path in (EXECUTION_RISK_APPLICATION, EXECUTION_RISK_DOMAIN):
        names = _identifier_names(path)
        assert "ExecutionApplication" not in names, path.name
        assert "OrderIntent" not in names, path.name


def test_only_the_execution_application_creates_order_identity() -> None:
    """One creator.  A second one is a second place orders appear from."""

    creators: list[str] = []
    for path in _python_files(_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "OrderIntent"
            ):
                creators.append(path.relative_to(_SRC).as_posix())
                break
    assert creators == ["trading/application/execution.py"], creators


def test_the_execution_store_freezes_the_paper_schema() -> None:
    """Old order databases keep opening: the DDL is the frozen one."""

    source = EXECUTION_STORE.read_text(encoding="utf-8")
    for table in (
        "paper_order_intent",
        "paper_order_update",
        "paper_execution",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}(" in source, table
    for column in (
        "intent_id TEXT PRIMARY KEY",
        "broker_order_id INTEGER NOT NULL UNIQUE",
        "idempotency_key TEXT",
        "update_id INTEGER PRIMARY KEY AUTOINCREMENT",
        "execution_row_id INTEGER PRIMARY KEY AUTOINCREMENT",
    ):
        assert column in source, column
    for forbidden in ("DROP TABLE", "DELETE FROM paper_", "DROP COLUMN"):
        assert forbidden not in source, forbidden
    # A legacy database may be grown by an ``ADD COLUMN`` and by nothing else:
    # no stored value is ever rewritten, so the store's only ALTER statement is
    # that one.
    assert source.count("ALTER TABLE") == 1
    assert 'ALTER TABLE {table} ADD COLUMN {column}' in source
    assert '("paper_order_intent", "idempotency_key", "TEXT")' in source


def test_the_execution_adapter_keeps_its_paper_safety_gates() -> None:
    """The refusal surface is the contract, not an implementation detail."""

    names = _identifier_names(EXECUTION_ADAPTER)
    for required in (
        "validate_paper_order_intent",
        "ensure_paper_order_config",
        "ExecutionRefused",
        "ExecutionSubmissionUncertain",
    ):
        assert required in names, required

    source = EXECUTION_ADAPTER.read_text(encoding="utf-8")
    # No global cancel, no market orders, no short surface came back in.
    for forbidden in (
        "reqGlobalCancel",
        "cancelAllOrders",
        "orderType = \"MKT\"",
        "shortSaleSlot",
    ):
        assert forbidden not in source, forbidden
    # And the durable-before-send ordering is inside the two calls, so the
    # adapter itself must not write the intent: that write is the caller's.
    assert "record_intent(" not in source, (
        "the adapter must not write the correlation; the application does "
        "that between reserve and submit"
    )


# -- Runtime v2A ----------------------------------------------------------
#
# The strategy/trading split, and the guards that keep it a split.  The whole
# risk of this round is a "migration" that moves a 1,400-line engine into a
# differently named 1,400-line engine, so the guards below are about shape as
# much as about dependency direction: file size, one caller per authority, and
# the strategy being structurally unable to send anything.

#: The retired mixed engine.  Deleted, not renamed: a compatibility re-export
#: would keep a second entry point alive, and a second entry point is a second
#: place that can submit.
RETIRED_RUNTIME_MODULES = ("us_quant.auto_quant",)

#: Modules that would be either a god object or a hiding place for one.  Each
#: name is something a reader would have to open to find out what it does.
FORBIDDEN_RUNTIME_MODULES = (
    "utils.py",
    "helpers.py",
    "common.py",
    "manager.py",
    "runtime_manager.py",
    "context.py",
    "app_state.py",
    "service_locator.py",
)

FORBIDDEN_RUNTIME_TYPE_NAMES = (
    "TradingManager",
    "TradingGodService",
    "GlobalAppState",
    "ServiceLocator",
    "ApplicationContext",
)

RUNTIME_DIR = _TRADING / "runtime"
RUNTIME_MODELS = RUNTIME_DIR / "models.py"
RUNTIME_ARTIFACTS = RUNTIME_DIR / "artifacts.py"
RUNTIME_PREFLIGHT = RUNTIME_DIR / "preflight.py"
RUNTIME_SIGNALS = RUNTIME_DIR / "signals.py"
RUNTIME_STRATEGY = RUNTIME_DIR / "strategy.py"
RUNTIME_SESSION = RUNTIME_DIR / "session.py"
RUNTIME_PORTFOLIO = RUNTIME_DIR / "portfolio.py"
RUNTIME_DISPATCH = RUNTIME_DIR / "dispatch.py"
RUNTIME_TRADING = RUNTIME_DIR / "trading.py"
RUNTIME_COMPOSITION = _TRADING / "composition" / "runtime.py"

#: Runtime v2B: the Paper session, workflow and lease migration.
PAPER_CONTRACTS = RUNTIME_DIR / "paper_contracts.py"
PAPER_MODELS = RUNTIME_DIR / "paper_models.py"
PAPER_RECONCILIATION = RUNTIME_DIR / "reconciliation.py"
PAPER_RECOVERY = RUNTIME_DIR / "recovery.py"
PAPER_COORDINATOR = RUNTIME_DIR / "coordinator.py"
PAPER_WORKFLOW_STATE = RUNTIME_DIR / "workflow_state.py"
PAPER_WORKFLOW = RUNTIME_DIR / "workflow.py"

#: Every production module of the runtime package.  ``__init__`` is excluded:
#: it is a package marker, not a module with a responsibility.
RUNTIME_MODULES = (
    RUNTIME_MODELS,
    RUNTIME_ARTIFACTS,
    RUNTIME_PREFLIGHT,
    RUNTIME_SIGNALS,
    RUNTIME_STRATEGY,
    RUNTIME_SESSION,
    RUNTIME_PORTFOLIO,
    RUNTIME_DISPATCH,
    RUNTIME_TRADING,
    PAPER_CONTRACTS,
    PAPER_MODELS,
    PAPER_RECONCILIATION,
    PAPER_RECOVERY,
    PAPER_COORDINATOR,
    PAPER_WORKFLOW_STATE,
    PAPER_WORKFLOW,
)

#: The modules a *strategy* may import.  Kept separate from the list above
#: because the artifact module carries order identity -- a snapshot names the
#: orders a session is holding -- and the strategy must not be able to reach
#: that even transitively.
STRATEGY_FACING_MODULES = (RUNTIME_MODELS, RUNTIME_SIGNALS, RUNTIME_STRATEGY)

#: What the *strategy* runtime may not reach for.  Each is a capability that
#: belongs to the session or below it: a verdict, an order, a fill, a broker, a
#: store or a widget.  A strategy that names one has taken a job that is not
#: its own -- and a strategy that can submit cannot be reasoned about.
STRATEGY_RUNTIME_FORBIDDEN_IMPORTS = (
    "us_quant.trading.application.risk",
    "us_quant.trading.application.execution",
    "us_quant.trading.ports",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "us_quant.trading.runtime.dispatch",
    "us_quant.trading.runtime.portfolio",
    "us_quant.trading.runtime.session",
    "us_quant.trading.runtime.trading",
    "us_quant.paper_session",
    "us_quant.paper_workflow",
    "us_quant.paper_order_models",
    "ibapi",
    "sqlite3",
    "PySide6",
    "us_quant.desktop",
)

STRATEGY_RUNTIME_FORBIDDEN_NAMES = (
    "RiskApplication",
    "RiskDecision",
    "RiskEvaluationRequest",
    "ExecutionApplication",
    "ExecutionSubmissionUncertain",
    "BrokerExecutionPort",
    "OrderRepositoryPort",
    "OrderIntent",
    "OrderEvent",
    "ExecutionFill",
    "order_sink",
    "placeOrder",
    "cancelOrder",
)

#: What the *trading* runtime may reach for, and what it may not.  It is the
#: only place that sees the strategy, the risk authority and the execution
#: service together; everything below them stays out.
TRADING_RUNTIME_FORBIDDEN_PREFIXES = (
    "ibapi",
    "sqlite3",
    "PySide6",
    "us_quant.desktop",
    "us_quant.trading.adapters",
    "us_quant.paper_session",
    "us_quant.paper_workflow",
)

#: The one module allowed to name both authorities, and the trade it makes:
#: it sees risk and execution but never the strategy.
RUNTIME_AUTHORITY_CALLERS = (RUNTIME_DISPATCH, RUNTIME_TRADING)

#: Line budgets.  A soft target and a hard limit, because "tidy it later" is
#: how a 1,400-line engine happened the first time.
RUNTIME_MODULE_LINE_LIMIT = 500
RUNTIME_COMPOSITION_LINE_LIMIT = 200


def _line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_the_retired_mixed_engine_is_gone() -> None:
    """Guard A: deleted, not renamed and not re-exported."""

    for module in RETIRED_RUNTIME_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the runtime "
            "split moved its behaviour to trading/runtime/"
        )


def test_nothing_imports_the_retired_mixed_engine() -> None:
    """Guard A, second half: no production module and no script."""

    offenders: list[str] = []
    for path in (*_all_source_files(), *_python_files(SCRIPTS_DIR)):
        used = _matches(_imports(path), RETIRED_RUNTIME_MODULES)
        if used:
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()} -> "
                f"{sorted(used)}"
            )
    assert not offenders, offenders


def test_the_strategy_runtime_cannot_reach_risk_execution_or_a_broker() -> None:
    """Guard B: the strategy proposes; it cannot decide or send."""

    offending_imports = _matches(
        _imports(RUNTIME_STRATEGY),
        STRATEGY_RUNTIME_FORBIDDEN_IMPORTS,
    ) | _matches(_imports(RUNTIME_SIGNALS), STRATEGY_RUNTIME_FORBIDDEN_IMPORTS)
    assert not offending_imports, sorted(offending_imports)

    for path in STRATEGY_FACING_MODULES:
        used = _identifier_names(path) & set(
            STRATEGY_RUNTIME_FORBIDDEN_NAMES
        )
        assert not used, (path.name, sorted(used))

    # It really does produce proposals, so the guard cannot pass by the module
    # having stopped working.
    assert "TradeProposal" in _identifier_names(RUNTIME_STRATEGY)


def test_the_trading_runtime_owns_no_adapter_store_or_widget() -> None:
    """Guard C: the session orchestrates; it does not implement a layer."""

    for path in RUNTIME_MODULES:
        offending = _matches(
            _imports(path), TRADING_RUNTIME_FORBIDDEN_PREFIXES
        )
        assert not offending, (path.name, sorted(offending))

    names = _identifier_names(RUNTIME_TRADING)
    assert "StrategyRuntime" in names
    assert "OrderDispatch" in names
    assert "SessionBook" in names
    assert "RiskApplication" in names
    assert "ExecutionApplication" in names


def test_only_the_dispatch_and_the_session_call_risk_and_execution() -> None:
    """One caller per authority, and it can be checked by reading a list."""

    callers: list[str] = []
    for path in RUNTIME_MODULES:
        names = _identifier_names(path)
        if names & {"RiskApplication", "ExecutionApplication"}:
            callers.append(path.relative_to(_SRC).as_posix())
    assert callers == [
        path.relative_to(_SRC).as_posix()
        for path in RUNTIME_AUTHORITY_CALLERS
    ], callers

    # The dispatch must not learn why a proposal was made: it sees the verdict
    # path and nothing of the strategy.
    dispatch_names = _identifier_names(RUNTIME_DISPATCH)
    assert "StrategyRuntime" not in dispatch_names
    assert "TradeProposal" in dispatch_names


def test_the_desktop_builds_the_runtimes_through_composition() -> None:
    """Guard D: the window never assembles or constructs a session itself.

    Checked as *calls* rather than as names: importing ``TradingRuntime`` to
    annotate the attribute it holds is fine, and a name scan cannot tell that
    from a construction.  What must never appear is ``TradingRuntime(...)``.
    """

    desktop = _SRC / "desktop.py"
    source = desktop.read_text(encoding="utf-8")
    constructed: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {
                "StrategyRuntime",
                "TradingRuntime",
                "OrderDispatch",
                "SessionBook",
            }:
                constructed.append(node.func.id)
    assert not constructed, constructed

    names = _identifier_names(desktop)
    assert "build_trading_runtime" in names, (
        "the window must build its session through the runtime composition"
    )
    assert "StrategyRuntime" not in names, (
        "the strategy runtime is built by the composition root, not the window"
    )

    composition = _identifier_names(RUNTIME_COMPOSITION)
    assert "StrategyRuntime" in composition
    assert "TradingRuntime" in composition
    # And it assembles rather than connects: no broker, no store, no widget.
    offending = _matches(
        _imports(RUNTIME_COMPOSITION),
        (
            "ibapi",
            "sqlite3",
            "PySide6",
            "us_quant.desktop",
            "us_quant.trading.adapters",
        ),
    )
    assert not offending, sorted(offending)


@dataclass(frozen=True, slots=True)
class _RuntimeClass:
    """One class a runtime module defines, and the shape of its body.

    Recorded per class rather than per module because two of these classes are
    split across two files: the live half and the human-driven recovery half are
    one object, and the guards below have to see both halves at once.
    """

    path: pathlib.Path
    bases: tuple[str, ...]
    methods: tuple[str, ...]
    declared: tuple[str, ...]
    calls: frozenset[str]
    assigned: frozenset[str]


def _class_names(path: pathlib.Path) -> set[str]:
    """Every class name ``path`` itself defines.

    ``_identifier_names`` reads *uses* of a name, not its definition, so a
    class that is only defined and never referenced does not show up there.
    """

    return {
        node.name
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
    }


def _is_self_attribute(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _runtime_class_records() -> dict[str, _RuntimeClass]:
    """Every class the runtime package defines, keyed by class name."""

    records: dict[str, _RuntimeClass] = {}
    for path in RUNTIME_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            calls: set[str] = set()
            assigned: set[str] = set()
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "self"
                ):
                    calls.add(child.func.attr)
                elif isinstance(child, ast.Assign):
                    for target in child.targets:
                        if _is_self_attribute(target):
                            assigned.add(target.attr)  # type: ignore[union-attr]
                elif isinstance(child, ast.AnnAssign) and _is_self_attribute(
                    child.target
                ):
                    assigned.add(child.target.attr)  # type: ignore[union-attr]
            records[node.name] = _RuntimeClass(
                path=path,
                bases=tuple(
                    base.id for base in node.bases if isinstance(base, ast.Name)
                ),
                methods=tuple(
                    item.name
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                declared=tuple(
                    item.target.id
                    for item in node.body
                    if isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.value is None
                ),
                calls=frozenset(calls),
                assigned=frozenset(assigned),
            )
    return records


def test_no_runtime_class_defines_a_method_twice() -> None:
    """A second definition of the same method is silently the only one.

    Python keeps the last one, so a stale copy left behind by a rename is dead
    code that no linter here reports and no test can see -- it just sits there,
    calling helpers that may no longer exist, until the day the order of the two
    definitions changes.  That is what happened to ``TradingRuntime._flatten``.

    The same dead code appears one level up when a subclass redefines a method a
    runtime base already provides, which the Paper runtime now makes possible:
    two of its classes are split across two modules.
    """

    records = _runtime_class_records()
    duplicated: list[str] = []

    for name, record in sorted(records.items()):
        counts: dict[str, int] = {}
        for method in record.methods:
            counts[method] = counts.get(method, 0) + 1
        for method, count in sorted(counts.items()):
            if count > 1:
                duplicated.append(f"{record.path.name}: {name}.{method} x{count}")
        for base in record.bases:
            if base not in records:
                continue
            for method in sorted(set(record.methods) & set(records[base].methods)):
                duplicated.append(
                    f"{record.path.name}: {name}.{method} overrides "
                    f"{base}.{method}"
                )
    assert not duplicated, duplicated


def test_a_split_runtime_class_provides_what_its_other_half_relies_on() -> None:
    """A half split into another file must say what it needs, and must get it.

    ``SessionRecovery`` and ``ManualReconciliation`` are mixed into the classes
    that own the state, so their bodies read attributes and call primitives they
    do not define.  The contract is only real if the class that mixes them in
    actually provides it, so this checks both directions: every attribute the
    half declares is set by its owner, and every ``self.<name>()`` the half makes
    is defined there.
    """

    records = _runtime_class_records()
    problems: list[str] = []
    for name, record in sorted(records.items()):
        for base in record.bases:
            half = records.get(base)
            if half is None:
                continue
            provided = set(record.methods) | set(record.assigned)
            for method in sorted(half.calls - set(half.methods) - provided):
                problems.append(f"{half.path.name}: {base} calls self.{method}()")
            for attribute in sorted(set(half.declared) - set(record.assigned)):
                problems.append(f"{half.path.name}: {base} declares self.{attribute}")
    assert not problems, problems


def test_the_runtime_modules_stay_small() -> None:
    """Guard F: the split is a split, not a rename of one big class."""

    oversized = [
        f"{path.name}: {_line_count(path)} lines"
        for path in RUNTIME_MODULES
        if _line_count(path) > RUNTIME_MODULE_LINE_LIMIT
    ]
    assert not oversized, oversized

    composition_lines = _line_count(RUNTIME_COMPOSITION)
    assert composition_lines <= RUNTIME_COMPOSITION_LINE_LIMIT, (
        f"composition/runtime.py: {composition_lines} lines"
    )


def test_the_runtime_has_no_god_objects_or_junk_drawers() -> None:
    """Guard G: no grab-bag module and no grab-bag type."""

    for name in FORBIDDEN_RUNTIME_MODULES:
        assert not (RUNTIME_DIR / name).exists(), name

    offenders: list[str] = []
    for path in RUNTIME_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and (
                node.name in FORBIDDEN_RUNTIME_TYPE_NAMES
            ):
                offenders.append(f"{path.name}.{node.name}")
    assert not offenders, offenders


def test_every_runtime_module_is_covered_by_the_guards() -> None:
    """A new runtime module must be added to the lists above, not slipped past."""

    covered = {path.name for path in RUNTIME_MODULES}
    actual = {
        path.name
        for path in _python_files(RUNTIME_DIR)
        if path.name != "__init__.py"
    }
    assert actual == covered, sorted(actual ^ covered)


def test_the_adapters_record_time_in_one_place() -> None:
    """A second clock formatter would be a second meaning for the stored text."""

    formatters: set[str] = set()
    for path in _python_files(_TRADING / "adapters"):
        source = path.read_text(encoding="utf-8")
        if "def now_iso" in source:
            formatters.add(path.relative_to(_SRC).as_posix())
    assert formatters == {"trading/adapters/clock.py"}, sorted(formatters)


# -- Runtime v2B ----------------------------------------------------------
#
# The Paper session, workflow and lease migration.  The risk of a migration
# round is the same one v2A had -- a 700-line module renamed into a differently
# named 700-line module -- so the guards are again about size and direction, plus
# the shape this round introduces: two classes whose human-driven half lives in
# another module, and a coordinator that must stay a sequencer.

#: The transitional root modules this round retired.  Deleted, not re-exported:
#: a compatibility shim would keep a second entry point alive, and a second
#: entry point is a second place that can halt, resume or finalize a session.
RETIRED_PAPER_ROOT_MODULES = (
    "us_quant.paper_session",
    "us_quant.paper_workflow",
    "us_quant.workflow_state",
)

#: What a migrated Paper module may not reach for: a provider, a store or a
#: widget.  The coordinator sequences a port; the workflow renders a snapshot.
PAPER_FORBIDDEN_BELOW = (
    "ibapi",
    "sqlite3",
    "PySide6",
    "us_quant.desktop",
    "us_quant.trading.adapters",
)

#: Line budgets for the migrated Paper modules.  Each is well under the general
#: 500-line ceiling on purpose: "tidy it up later" is how the first 700-line
#: module happened, and a budget that only bites at 500 would not have caught it.
PAPER_MODULE_LINE_LIMITS = {
    "paper_contracts.py": 150,
    "paper_models.py": 150,
    "reconciliation.py": 300,
    "recovery.py": 400,
    "coordinator.py": 420,
    "workflow_state.py": 250,
    "workflow.py": 400,
}


def test_the_retired_paper_root_modules_are_gone() -> None:
    """Guard H: deleted, not renamed and not re-exported."""

    for module in RETIRED_PAPER_ROOT_MODULES:
        path = _SRC / f"{module.removeprefix('us_quant.')}.py"
        assert not path.exists(), (
            f"{path.relative_to(_SRC).as_posix()} must not exist; the Paper "
            "runtime migration moved its behaviour to trading/runtime/"
        )


def test_nothing_imports_a_retired_paper_root_module() -> None:
    """Guard H, second half: production, scripts and tests alike."""

    offenders: list[str] = []
    for path in (
        *_all_source_files(),
        *_python_files(SCRIPTS_DIR),
        *_python_files(_REPO_ROOT / "tests"),
    ):
        used = _matches(_imports(path), RETIRED_PAPER_ROOT_MODULES)
        if used:
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()} -> {sorted(used)}"
            )
    assert not offenders, offenders


def test_the_migrated_paper_modules_stay_small() -> None:
    """Guard I: the migration is a split, not a move of one big class."""

    oversized = [
        f"{name}: {_line_count(RUNTIME_DIR / name)} lines (limit {limit})"
        for name, limit in PAPER_MODULE_LINE_LIMITS.items()
        if _line_count(RUNTIME_DIR / name) > limit
    ]
    assert not oversized, oversized

    # And the modules really are the ones the Paper runtime uses, so the guard
    # cannot pass by the files having been emptied.
    for path in (PAPER_COORDINATOR, PAPER_WORKFLOW, PAPER_RECOVERY):
        assert "PaperSession" in path.read_text(encoding="utf-8"), path.name


def test_the_paper_contracts_and_models_depend_on_nothing_below_them() -> None:
    """Guard J: the contracts name no provider, the models name no port call."""

    for path in (PAPER_CONTRACTS, PAPER_MODELS):
        offending = _matches(_imports(path), PAPER_FORBIDDEN_BELOW)
        assert not offending, (path.name, sorted(offending))

    assert "PaperEngine" in _class_names(PAPER_CONTRACTS)
    assert "PaperSessionResult" in _class_names(PAPER_MODELS)


def test_reconciliation_is_a_pure_proof() -> None:
    """Guard K: it may read facts and raise; it may not act or decide."""

    offending = _matches(
        _imports(PAPER_RECONCILIATION),
        PAPER_FORBIDDEN_BELOW
        + (
            "us_quant.trading.application",
            "us_quant.trading.runtime.strategy",
            "us_quant.trading.runtime.trading",
            "us_quant.trading.runtime.coordinator",
            "us_quant.trading.runtime.workflow",
            "us_quant.trading.runtime.recovery",
        ),
    )
    assert not offending, sorted(offending)

    names = _identifier_names(PAPER_RECONCILIATION)
    for forbidden in (
        "PaperWorkflowPhase",
        "ExecutionApplication",
        "RiskApplication",
        "StrategyRuntime",
        "placeOrder",
        "cancelOrder",
    ):
        assert forbidden not in names, forbidden

    # It really does build proofs, so the guard cannot pass by the module
    # having stopped working.
    proofs = _class_names(PAPER_RECONCILIATION)
    assert "CoordinatorReconciliationEvidence" in proofs
    assert "CoordinatorFinalizationEvidence" in proofs


def test_the_coordinator_sequences_and_does_not_decide() -> None:
    """Guard L: it drains, times out and halts; it never submits or prices."""

    offending = _matches(_imports(PAPER_COORDINATOR), PAPER_FORBIDDEN_BELOW)
    assert not offending, sorted(offending)

    names = _identifier_names(PAPER_COORDINATOR)
    assert "PaperSessionCoordinator" in _class_names(PAPER_COORDINATOR)
    assert "SessionRecovery" in names
    for forbidden in (
        "RiskApplication",
        "ExecutionApplication",
        "StrategyRuntime",
        "OrderIntent",
        "OrderRepositoryPort",
        "TradeProposal",
        "placeOrder",
        "cancelOrder",
    ):
        assert forbidden not in names, forbidden

    # The port it sequences is described by name only: the coordinator must not
    # name a concrete order service or gateway either.
    assert "IBKRPaperOrderService" not in names


def test_the_paper_workflow_owns_lifecycle_and_no_broker() -> None:
    """Guard M: the controller drives phases and hands out ports, nothing more."""

    offending = _matches(
        _imports(PAPER_WORKFLOW),
        PAPER_FORBIDDEN_BELOW + ("us_quant.paper_trading_service",),
    )
    assert not offending, sorted(offending)

    names = _identifier_names(PAPER_WORKFLOW)
    assert "PaperWorkflowController" in _class_names(PAPER_WORKFLOW)
    assert "ManualReconciliation" in names
    assert "PaperSessionCoordinator" in names
    for forbidden in (
        "PaperTradingService",
        "IBKRPaperOrderService",
        "placeOrder",
        "cancelOrder",
        "submit_approved",
    ):
        assert forbidden not in names, forbidden

    # The coordinator is constructed in exactly one place: publication.
    source = PAPER_WORKFLOW.read_text(encoding="utf-8")
    assert source.count("PaperSessionCoordinator(") == 1, source.count(
        "PaperSessionCoordinator("
    )


def test_the_desktop_does_not_drive_the_paper_lifecycle_by_hand() -> None:
    """Guard N: the window asks the workflow; it does not become one."""

    desktop = _SRC / "desktop.py"
    constructed: list[str] = []
    for node in ast.walk(ast.parse(desktop.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {
                "PaperSessionCoordinator",
                "SessionRecovery",
                "ManualReconciliation",
            }:
                constructed.append(node.func.id)
    assert not constructed, constructed

    source = desktop.read_text(encoding="utf-8")
    for forbidden in (
        "CoordinatorReconciliationEvidence(",
        "CoordinatorFinalizationEvidence(",
        "validate_paper_transition(",
        "ExecutionLeaseManager(",
        "release_paper(",
    ):
        assert forbidden not in source, forbidden

    # It still drives the workflow by its documented surface, so the guard
    # cannot pass by the window having stopped launching Paper sessions.
    for required in (
        "paper_workflow.begin_connecting(",
        "paper_workflow.publish_armed(",
        "paper_workflow.begin_manual_reconciliation()",
        "paper_workflow.finalize_if_safe()",
    ):
        assert required in source, required


# -- scripts/ -------------------------------------------------------------
#
# The scripts are the one directory pytest never imports, and that is exactly
# why a stale import can live there through a whole migration:
# ``scripts/check_paper_order_channel.py`` kept importing the Paper order
# journal after the journal was deleted, so ``--help`` raised
# ``ModuleNotFoundError`` and nothing noticed.  These guards scan that
# directory directly.

SCRIPTS_DIR = _REPO_ROOT / "scripts"

#: Union of every retired-module tuple above.  A script naming any of them is
#: broken at import time, because none of these modules exists any more.
ALL_RETIRED_MODULES = (
    *RETIRED_MARKET_DATA_MODULES,
    *RETIRED_ACCOUNT_MODULES,
    *RETIRED_STRATEGY_MODULES,
    *RETIRED_EXECUTION_MODULES,
)


def _us_quant_module_exists(module: str) -> bool:
    """Whether ``us_quant.<...>`` resolves to a file in ``src/us_quant``."""

    head, *rest = module.split(".")
    if head != "us_quant":
        return True
    base = _SRC.joinpath(*rest)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def test_no_script_imports_a_retired_module() -> None:
    """Deleting a module must not leave an import behind under ``scripts/``."""

    offenders: list[str] = []
    for path in _python_files(SCRIPTS_DIR):
        used = _matches(_imports(path), ALL_RETIRED_MODULES)
        if used:
            offenders.append(
                f"{path.relative_to(_REPO_ROOT).as_posix()} -> "
                f"{sorted(used)}"
            )
    assert not offenders, offenders


def test_every_module_a_script_imports_exists() -> None:
    """The general form of the guard above, so the next deletion is caught too.

    A named retired module is caught by the list; this test catches the rest,
    including a module deleted in a migration whose list nobody extended.
    """

    missing: list[str] = []
    for path in _python_files(SCRIPTS_DIR):
        for module in sorted(_imports(path)):
            if not _us_quant_module_exists(module):
                missing.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()} imports "
                    f"{module}, which does not exist"
                )
    assert not missing, missing


def test_the_paper_order_channel_diagnostic_still_runs() -> None:
    """The script's own entry point must work: ``--help`` exits 0.

    A diagnostic that cannot print its help text is not a diagnostic, and the
    failure it had was invisible to a suite that only ever imports ``src``.
    """

    script = SCRIPTS_DIR / "check_paper_order_channel.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Read-only diagnostic" in completed.stdout


def test_the_paper_order_channel_diagnostic_cannot_submit() -> None:
    """Diagnostic only: it never arms the session, so it can never send.

    ``arm`` is the gate every send path sits behind -- ``reserve`` refuses for
    an unarmed session, and ``submit`` re-refuses -- so a script that does not
    arm cannot place an order even by accident.
    """

    source = (SCRIPTS_DIR / "check_paper_order_channel.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "arm",
                "reserve",
                "submit",
                "placeOrder",
                "cancel",
                "cancelOrder",
            }, f"the diagnostic must not call {node.func.attr}"
    assert '"orders_submitted": 0' in source
    assert "build_execution_candidate" in source
    assert "build_order_repository" in source
    # The real order store must not be touched: the run writes to a temporary
    # directory and opens nothing else.
    assert "TemporaryDirectory" in source


# -- Desktop Execution v2 -------------------------------------------------
#
# The execution route became a native v2 page.  The risk of a UI migration is
# that it becomes a move of widgets rather than a move of the boundary: the
# window keeps reaching into the page's buttons, or the page learns a service so
# it can decide for itself.  These guards are about the boundary, not the
# layout.

EXECUTION_PAGE_DIR = _SRC / "desktop_v2" / "pages" / "execution"
EXECUTION_PAGE = EXECUTION_PAGE_DIR / "page.py"
EXECUTION_CONTROLS = EXECUTION_PAGE_DIR / "controls.py"
EXECUTION_MODELS = EXECUTION_PAGE_DIR / "models.py"
EXECUTION_PRESENTER = EXECUTION_PAGE_DIR / "presenter.py"
EXECUTION_ROWS = EXECUTION_PAGE_DIR / "rows.py"
EXECUTION_TABLES = EXECUTION_PAGE_DIR / "tables.py"

EXECUTION_PAGE_MODULES = (
    EXECUTION_PAGE_DIR / "__init__.py",
    EXECUTION_MODELS,
    EXECUTION_PRESENTER,
    EXECUTION_ROWS,
    EXECUTION_TABLES,
    EXECUTION_CONTROLS,
    EXECUTION_PAGE,
)

#: Line budgets.  A page that only bites at the shared 500-line ceiling would
#: not have caught the 416-line builder this round replaced, so each file has
#: its own, well under it.
EXECUTION_MODULE_LINE_LIMITS = {
    "__init__.py": 40,
    "models.py": 220,
    "presenter.py": 320,
    "rows.py": 350,
    "tables.py": 320,
    "controls.py": 360,
    "page.py": 400,
}

#: What no module of the page package may name.  Each is a capability the
#: window owns: an application service, a workflow controller, a runtime, a
#: store, a broker, a widget toolkit's network stack, or the legacy window.
EXECUTION_PAGE_FORBIDDEN_IMPORTS = (
    "us_quant.trading.application",
    "us_quant.trading.composition",
    "us_quant.trading.adapters",
    "us_quant.trading.runtime",
    "us_quant.trading.ports",
    "us_quant.paper_trading_service",
    "us_quant.paper_execution_health",
    "us_quant.workflow_controller",
    "us_quant.ibkr",
    "us_quant.risk",
    "us_quant.sqlite_support",
    "us_quant.desktop",
    "ibapi",
    "sqlite3",
    "PySide6.QtNetwork",
)

#: The widget names the window used to reach for by attribute.  Every one of
#: them was a way for the window to become the page's renderer.
RETIRED_EXECUTION_WIDGETS = (
    "auto_prepare_button",
    "auto_start_button",
    "auto_pause_button",
    "auto_resume_button",
    "auto_stop_button",
    "auto_stop_stream_button",
    "auto_reconcile_button",
    "auto_resume_from_reconciliation_button",
    "auto_channel_check_button",
    "auto_arm_confirm",
    "auto_strategy_combo",
    "auto_candidate_limit",
    "auto_capital_limit",
    "auto_summary_label",
    "auto_scope_label",
    "auto_session_label",
    "auto_preflight_label",
    "auto_pipeline_label",
    "auto_status_card",
    "auto_equity_card",
    "auto_realized_card",
    "auto_unrealized_card",
    "auto_position_card",
    "auto_health_status_card",
    "auto_health_broker_card",
    "auto_health_pending_card",
    "auto_health_unreconciled_card",
    "auto_health_latency_card",
    "auto_detail_tabs",
    "auto_position_table",
    "auto_position_model",
    "auto_recent_fill_table",
    "auto_recent_fill_model",
    "auto_shadow_table",
    "auto_latency_table",
    "auto_candidate_table",
    "auto_order_table",
    "auto_execution_health_label",
)


def test_the_execution_route_has_one_entry_point() -> None:
    """Guard A and B: no legacy builder, and the route serves the page.

    The window must hold the page and nothing inside it.  A compatibility
    ``_auto_quant_tab`` that returned the page would keep a second control path
    alive, which is the arrangement this round removes.
    """

    desktop = _SRC / "desktop.py"
    names = _identifier_names(desktop)
    assert "_auto_quant_tab" not in names, (
        "the legacy execution builder must be deleted, not re-exported"
    )

    source = desktop.read_text(encoding="utf-8")
    assert '"execution": self.execution_page,' in source
    assert '"execution": self._auto_quant_tab()' not in source
    assert "self.execution_page = ExecutionPage(" in source


def test_the_window_no_longer_names_an_execution_widget() -> None:
    """Guard E: one attribute -- the page -- is the whole of the window's view.

    Checked as attribute *uses* rather than as text, so a comment explaining the
    retirement does not trip it.
    """

    desktop = _SRC / "desktop.py"
    tree = ast.parse(desktop.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "self":
            continue
        if node.attr in RETIRED_EXECUTION_WIDGETS:
            offenders.append(node.attr)
    assert not offenders, sorted(set(offenders))

    # And the window still drives the page, so the guard cannot pass by the
    # route having stopped working.
    source = desktop.read_text(encoding="utf-8")
    for required in (
        "self.execution_page.render(",
        "self.execution_page.set_control_state(",
        "self.execution_page.set_strategy_options(",
        "self.execution_page.render_preflight(",
    ):
        assert required in source, required


def test_the_execution_page_package_knows_no_business_service() -> None:
    """Guard C: the page renders and reports intent; it decides nothing."""

    for path in EXECUTION_PAGE_MODULES:
        offending = _matches(
            _imports(path), EXECUTION_PAGE_FORBIDDEN_IMPORTS
        )
        assert not offending, (path.name, sorted(offending))

    # And it is a real page, so the guard cannot pass by the package having been
    # emptied.
    assert "ExecutionPage" in _class_names(EXECUTION_PAGE)
    assert "ExecutionDetailTabs" in _class_names(EXECUTION_TABLES)
    assert "ExecutionControls" in _class_names(EXECUTION_CONTROLS)


def test_the_execution_page_cannot_control_the_workflow() -> None:
    """Guard C, second half: no lifecycle vocabulary, no lifecycle action."""

    for path in EXECUTION_PAGE_MODULES:
        names = _identifier_names(path)
        for forbidden in (
            "PaperWorkflowPhase",
            "ExecutionLease",
            "CoordinatorReconciliationEvidence",
            "CoordinatorFinalizationEvidence",
            "PaperSessionCoordinator",
            "PaperWorkflowController",
            "PaperTradingService",
            "TradingRuntime",
            "begin_connecting",
            "publish_armed",
            "request_stop",
            "finalize_if_safe",
            "release_paper",
            "placeOrder",
            "cancelOrder",
            "submit_approved",
        ):
            assert forbidden not in names, (path.name, forbidden)


def test_the_execution_presenter_is_qt_free() -> None:
    """Guard D: a presenter that needed Qt could not be tested without it."""

    for path in (EXECUTION_PRESENTER, EXECUTION_ROWS, EXECUTION_MODELS):
        offending = {
            module
            for module in _imports(path)
            if module == "PySide6" or module.startswith("PySide6.")
        }
        assert not offending, (path.name, sorted(offending))

    # It really does project the view, so the guard cannot pass by the module
    # having stopped working.
    assert "build_runtime_view" in _identifier_names(EXECUTION_PRESENTER)
    assert "control_state" in _identifier_names(EXECUTION_PRESENTER)


def test_the_execution_page_modules_stay_small() -> None:
    """Guard F: the split is a split, not a 400-line page in five parts."""

    oversized = [
        f"{name}: {_line_count(EXECUTION_PAGE_DIR / name)} lines (limit {limit})"
        for name, limit in EXECUTION_MODULE_LINE_LIMITS.items()
        if _line_count(EXECUTION_PAGE_DIR / name) > limit
    ]
    assert not oversized, oversized

    covered = {path.name for path in EXECUTION_PAGE_MODULES}
    actual = {
        path.name
        for path in _python_files(EXECUTION_PAGE_DIR)
    }
    assert actual == covered, sorted(actual ^ covered)


def test_the_execution_page_adds_no_trading_capability() -> None:
    """The Paper posture is unchanged by a UI migration."""

    for path in EXECUTION_PAGE_MODULES:
        names = _identifier_names(path)
        for forbidden in (
            "placeOrder",
            "cancelOrder",
            "reqMktData",
            "cancelAllOrders",
            "market_order",
            "short_sell",
            "allow_margin_borrowing",
        ):
            assert forbidden not in names, (path.name, forbidden)
