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
import pathlib
import subprocess

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
    "trading/composition",
)

# Modules a pure domain must never reach for.
FORBIDDEN_DOMAIN_PREFIXES = (
    "PySide6",
    "ibapi",
    "sqlite3",
    "us_quant.desktop",
    "us_quant.desktop_v2",
    "us_quant.market_data_service",
    "us_quant.paper",
    "us_quant.ibkr",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
    "us_quant.auto_quant",
    "us_quant.strategy_registry",
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
    "us_quant.market_data_service",
    "us_quant.paper",
    "us_quant.ibkr",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
    "us_quant.auto_quant",
    "us_quant.strategy_registry",
    "us_quant.trading.application",
    "us_quant.trading.runtime",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
)

# The canonical types, and the module each must be defined in *within the
# domain package*.
CANONICAL_TYPES = {
    "AccountSnapshot": "account.py",
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
    "TradeAction": "strategy.py",
    "StrategyIdentity": "strategy.py",
    "TradeProposal": "strategy.py",
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
    "AccountSnapshot",
    "OrderIntent",
    "RiskDecision",
    "Position",
)

# Transitional overlap, recorded rather than hidden.
#
# ``MarketQuote`` is defined twice on purpose for now: once as the new
# provider-neutral domain type, and once as the pre-existing IBKR-specific
# ``ibkr_readonly.MarketQuote`` (which carries ``request_id`` and
# ``market_data_type`` -- vendor fields the domain type must not have).
# ``ibkr_readonly.py`` is not migrated by this change; it is scheduled to
# become ``trading/adapters/ibkr/account.py``, at which point its quote type
# retires into the adapter and the overlap disappears.  This is the same
# deliberate coexistence as ``TradingSessionPhase`` and
# ``PaperWorkflowPhase``.
KNOWN_TRANSITIONAL_OVERLAPS = {
    "MarketQuote": ("market.py", "ibkr_readonly.py"),
}


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
    desktop_v2 = _SRC / "desktop_v2"
    assert {path.name for path in _python_files(desktop_v2)} == {
        "__init__.py",
        "navigation.py",
        "shell.py",
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
