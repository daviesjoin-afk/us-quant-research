"""Architecture guards for the v2O-B Account orchestration extraction.

These are structural, not behavioural.  They read the source tree and assert
that the ownership the extraction moved really moved -- that ``MainWindow`` no
longer holds ``account_portfolio`` or the refresh/ledger/render handlers, and
that ``AccountOrchestrator`` cannot reach back into the window or sideways into
Market, Paper, Shadow, Research, Strategy, Risk, System, or any page but its
own, and holds no second copy of the account truth.

The guards are written as *exact* deltas where a set is involved: a later round
that adds an account method back must declare it here, so the boundary cannot
erode one convenient accessor at a time.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_ACCOUNT_DIR = _SRC / "desktop_v2" / "orchestration" / "account"
_ORCHESTRATOR_PATH = _ACCOUNT_DIR / "orchestrator.py"
_MODELS_PATH = _ACCOUNT_DIR / "models.py"
_QUERIES_PATH = _ACCOUNT_DIR / "queries.py"
_PAGE_PATH = _SRC / "desktop_v2" / "pages" / "account.py"

#: The account state the window must no longer hold.  ``account_portfolio`` is
#: the second-truth this round removes; the three retired methods each touched
#: one half of the account lifecycle.
RETIRED_WINDOW_ACCOUNT_STATE = ("account_portfolio",)

#: The window methods the extraction deleted rather than shimmed.  Declared as
#: an exact set so a re-added one fails here.
RETIRED_WINDOW_ACCOUNT_METHODS = (
    "_refresh_account_snapshot",
    "_account_snapshot_finished",
    "_refresh_account_surfaces",
    "_paper_simulation_capital",
)

#: A compatibility property that re-exposes the account truth would leave the
#: old call sites -- and the habit of reaching for them -- in place.
FORBIDDEN_COMPATIBILITY_PROPERTIES = ("account_portfolio",)

#: What the orchestrator may import.  Each entry is a capability, not merely a
#: module it happens not to use: an orchestrator that could import the Paper
#: workflow or the research route could decide their fan-out itself.
#:
#: An *allowlist* rather than a denylist, because a denylist only forbids the
#: couplings someone thought of: ``PaperWorkflowPhase`` lives in
#: ``trading.runtime.workflow_state``, which no denylist of *capability* names
#: would have listed, and the mutation sweep found exactly that hole.  A new
#: dependency now has to be declared here on purpose.
ALLOWED_ORCHESTRATOR_IMPORTS = (
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "PySide6.QtCore",
    "us_quant.account_ledger",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.tasking",
    "us_quant.trading.application.accounts",
    "us_quant.trading.domain.account",
    "us_quant.trading.domain.common",
)

#: The same rule stated the other way, kept because it names the capabilities
#: the spec calls out and gives a more legible failure message.  Both run: the
#: denylist documents intent, the allowlist closes the set.
FORBIDDEN_ORCHESTRATOR_IMPORTS = (
    "us_quant.desktop",
    "us_quant.market_data_service",
    "us_quant.trading.application.market_data",
    "us_quant.paper",
    "us_quant.paper_workflow",
    "us_quant.paper_trading_service",
    "us_quant.paper_order_journal",
    "us_quant.paper_order_models",
    "us_quant.paper_session",
    "us_quant.shadow",
    "us_quant.auto_quant",
    "us_quant.workflow_controller",
    "us_quant.runtime_supervisor",
    "us_quant.runtime_events",
    "us_quant.artifact_state",
    "us_quant.desktop_workers",
    "us_quant.desktop_task_controller",
    "us_quant.desktop_v2.workflows",
    "us_quant.desktop_v2.pages.dashboard",
    "us_quant.desktop_v2.pages.execution",
    "us_quant.desktop_v2.pages.system",
    "us_quant.desktop_v2.pages.research",
    "us_quant.desktop_v2.pages.risk",
    "us_quant.desktop_v2.pages.strategy",
    "us_quant.trading.runtime.trading",
    "us_quant.trading.runtime.artifacts",
    "us_quant.trading.runtime.models",
    "us_quant.trading.runtime.workflow_state",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "us_quant.strategy",
    "us_quant.risk",
    "ibapi",
)

#: Names the orchestrator must not *call* even if the import is indirect.
FORBIDDEN_ORCHESTRATOR_CALLS = (
    "MarketOrchestrator",
    "MarketDataApplication",
    "PaperWorkflowPhase",
    "PaperWorkflowController",
    "PaperTradingService",
    "ShadowPaperEngine",
    "ShadowWorkflowController",
    "TradingRuntime",
    "ExecutionApplication",
    "RuntimeSupervisor",
    "RuntimeEventStore",
    "TaskThread",
    "DesktopTaskController",
    "MainWindow",
)

#: The orchestrator's public surface, asserted exactly in both directions.
#: ``set_notice`` is the G2-A seam: the strategy governance owner publishes the
#: finished notice text and the account capability paints it.
PUBLIC_READ_SURFACE = (
    "portfolio",
    "fresh_paper_net_liquidation",
    "request_refresh",
    "render_current",
    "set_presentation_inputs",
    "set_notice",
)

#: The Qt signals the window relies on.
PUBLIC_SIGNALS = (
    "portfolio_changed",
    "shell_health_changed",
    "runtime_event_requested",
    "log_requested",
)

#: Per-file line caps, per the spec: orchestrator.py <= 320, models.py <= 140,
#: queries.py <= 140.
LINE_BUDGETS = {
    "__init__.py": 80,
    "models.py": 140,
    "queries.py": 140,
    "orchestrator.py": 320,
}

#: The page's render entry points only the orchestrator may call from the
#: desktop layer.  Declared as a constant so the guard below reads the list
#: rather than restating it: an unused name here means the guard is not
#: actually checking what it claims.
PAGE_RENDER_ENTRY_POINTS = ("render",)

#: The only direct cross-capability presentation APIs ``MainWindow`` may still
#: call on ``AccountPage``.  ``set_notice`` was on this list until G2-A: the
#: strategy notice now travels as finished text through
#: ``AccountOrchestrator.set_notice``, so the window's direct page surface
#: shrank to the refresh intent alone.
#:
#: ``set_research_capital`` was on this list until v2O-C4.  That round gave the
#: research scenario scalar a canonical owner and folded its presentation into
#: the existing ``AccountPresentationInputs`` -> ``render_current`` ->
#: ``AccountPage.render`` path, so the second paint route is gone: the
#: orchestrator is now the Account page's only render owner, which is the
#: property ``test_the_window_never_sets_the_research_capital_card`` pins.
ACCOUNT_PAGE_WINDOW_WHITELIST = (
    "refresh_requested",
)


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _matches(modules: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in modules
        for prefix in prefixes
        if module == prefix or module.startswith(f"{prefix}.")
    }


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_names(path: pathlib.Path) -> set[str]:
    return {
        node.name
        for node in _main_window(path).body
        if isinstance(node, ast.FunctionDef)
    }


def _assigned_self_attrs(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def _property_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in _main_window(path).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(
            (isinstance(d, ast.Name) and d.id == "property")
            or (isinstance(d, ast.Attribute) and d.attr == "setter")
            for d in node.decorator_list
        ):
            names.add(node.name)
    return names


def _class_of(path: pathlib.Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in {path.name}")


def _public_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _class_of(path, name).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        members.add(node.name)
    return members


def _signal_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _class_of(path, name).body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        if (
            isinstance(value, ast.Call)
            and getattr(value.func, "id", None) == "Signal"
        ):
            for target in targets:
                if isinstance(target, ast.Name):
                    members.add(target.id)
    return members


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


# -- Guard A: the window no longer owns the account truth ----------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_ACCOUNT_STATE)
def test_the_window_assigns_no_account_state(attribute: str) -> None:
    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("attribute", FORBIDDEN_COMPATIBILITY_PROPERTIES)
def test_the_window_has_no_account_compatibility_property(
    attribute: str,
) -> None:
    assert attribute not in _property_names(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("name", RETIRED_WINDOW_ACCOUNT_METHODS)
def test_the_retired_account_methods_are_gone(name: str) -> None:
    assert name not in _method_names(_DESKTOP_PATH), name


def test_the_window_still_owns_the_page_and_the_orchestrator() -> None:
    names = _assigned_self_attrs(_DESKTOP_PATH)
    assert "account_page" in names
    assert "account_orchestrator" in names


def test_the_window_composes_the_account_orchestrator_once() -> None:
    """The window builds the orchestrator and stays out; it is not a page."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert source.count("AccountOrchestrator(") == 1


def test_the_window_never_reaches_through_the_orchestrator() -> None:
    """Every ``self.account_orchestrator.<name>`` must be a declared member."""

    allowed = set(PUBLIC_READ_SURFACE) | set(PUBLIC_SIGNALS)
    offending: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "account_orchestrator"
        ):
            if node.attr not in allowed:
                offending.append((node.attr, node.lineno))
    assert not offending, offending


def test_the_window_never_calls_a_page_render_entry_point() -> None:
    """``account_page.render`` must not appear on the window."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for entry_point in PAGE_RENDER_ENTRY_POINTS:
        assert f"account_page.{entry_point}" not in source, entry_point


def test_the_account_page_render_has_exactly_one_desktop_caller() -> None:
    """``AccountPage.render`` is reached from one place: the orchestrator.

    The name check above only proves the *window* stopped rendering.  This one
    proves nobody else picked the job up -- a second caller would be a second
    render path, which is the thing "one capability, one entry point" forbids.

    Two shapes are counted, because both would be a real second caller: a
    caller that names the page (``...account_page.render(...)``, anywhere) and
    one inside the account capability that uses its own injected page handle.
    """

    account_package = _ACCOUNT_DIR.relative_to(_SRC).as_posix()
    callers: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        where = path.relative_to(_SRC).as_posix()
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in PAGE_RENDER_ENTRY_POINTS
            ):
                continue
            receiver = ast.unparse(func.value)
            if "account_page" in receiver:
                callers.append(f"{where}:{receiver}")
            elif where.startswith(account_package) and receiver == "self._page":
                callers.append(f"{where}:{receiver}")

    assert callers == [
        f"{account_package}/orchestrator.py:self._page"
    ], callers


def test_the_window_never_touches_the_ledger() -> None:
    """``account_ledger.append`` / ``.list_points`` moved off the window."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "account_ledger.append" not in source
    assert "account_ledger.list_points" not in source


def test_the_window_never_starts_the_account_read() -> None:
    """The refresh belongs to the orchestrator, not the composition root.

    Composition is allowed to *construct* ``BrokerAccountApplication`` and to
    hand it to the settings transaction and the market composition getter; it
    is not allowed to call ``refresh`` itself, which would be the second
    refresh path the extraction removed.
    """

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "broker_account.refresh" not in source


def test_the_orchestrator_never_reaches_into_the_application() -> None:
    """Only the delegating property may read ``_application``.

    ``_application.config`` / ``_application.refresh`` from inside the window
    would be a reach-through that bypasses the capability boundary; the
    orchestrator's own private use of ``self._application`` is its business.
    """

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "account_orchestrator._application" not in source
    assert "account_orchestrator._portfolio" not in source
    assert "account_orchestrator._ledger" not in source
    assert "account_orchestrator._page" not in source


def test_the_window_composes_the_account_application_exactly_once() -> None:
    """``broker_account`` is constructed once and shared, not rebuilt."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert source.count("build_broker_account_application(") == 1


# -- Guard B: the page boundary -----------------------------------------


def test_the_window_uses_only_the_whitelisted_account_page_surface() -> None:
    """The window's only direct AccountPage calls are the declared temporary
    cross-capability presentation APIs."""

    offending: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "account_page"
        ):
            if node.attr not in ACCOUNT_PAGE_WINDOW_WHITELIST:
                offending.append((node.attr, node.lineno))
    assert not offending, offending


def test_the_window_never_reaches_into_account_page_widgets() -> None:
    """``account_page.<widget>`` is forbidden, whatever the widget is named."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "net_liquidation_card",
        "research_capital_card",
        "daily_pnl_card",
        "positions_table",
        "ledger_table",
        "status_label",
    ):
        assert f"account_page.{forbidden}" not in source, forbidden


def test_the_window_never_sets_the_research_capital_card() -> None:
    """v2O-C4: the research scenario figure has one render path, not two.

    ``AccountPage.set_research_capital`` is retired, so the window cannot paint
    the card directly at all.  The scenario number now travels with the other
    composed presentation facts through ``AccountPresentationInputs`` and is
    drawn by ``AccountPage.render``, which only ``AccountOrchestrator`` calls --
    that is what makes "the orchestrator is the page's only render owner" true
    rather than aspirational.  The behavioural half lives in
    ``test_desktop_v2_cross_section_wiring.py``.
    """

    page_source = _PAGE_PATH.read_text(encoding="utf-8")
    desktop_source = _DESKTOP_PATH.read_text(encoding="utf-8")

    assert "def set_research_capital" not in page_source
    assert "account_page.set_research_capital" not in desktop_source
    # And the card is still the page's own widget: the window reaches into
    # neither the card nor a named writer.
    assert "account_page.research_capital_card" not in desktop_source


# -- Guard C: the orchestrator knows only the account capability ---------


def test_the_orchestrator_imports_no_other_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_ACCOUNT_DIR):
        for module in _matches(_imports(path), FORBIDDEN_ORCHESTRATOR_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_orchestrator_imports_nothing_outside_the_allowlist() -> None:
    """The import set is closed, not merely denylisted.

    A denylist only forbids the couplings someone remembered to name.  The
    mutation sweep proved the hole: importing ``PaperWorkflowPhase`` from
    ``trading.runtime.workflow_state`` passed every forbidden-module check,
    because no *capability* name covers that module.  Closing the set means a
    new dependency must be added to ``ALLOWED_ORCHESTRATOR_IMPORTS`` on
    purpose, where a reviewer will see it.
    """

    offending: list[tuple[str, str]] = []
    for path in _python_files(_ACCOUNT_DIR):
        for module in sorted(_imports(path)):
            allowed = any(
                module == entry or module.startswith(f"{entry}.")
                for entry in ALLOWED_ORCHESTRATOR_IMPORTS
            )
            if not allowed:
                offending.append((path.name, module))
    assert not offending, offending


def test_the_orchestrator_never_imports_a_forbidden_symbol() -> None:
    """Even an allowed module must not supply a forbidden *name*.

    ``from us_quant.trading.runtime import workflow_state`` is covered by the
    allowlist above, but ``from ... import PaperWorkflowPhase`` would still be
    the Paper capability reaching into the account route.  This checks the
    imported names, which module-path guards cannot see.
    """

    offending: list[tuple[str, str]] = []
    for path in _python_files(_ACCOUNT_DIR):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in FORBIDDEN_ORCHESTRATOR_CALLS:
                    offending.append((path.name, alias.name))
            for alias in node.names:
                if alias.asname in FORBIDDEN_ORCHESTRATOR_CALLS:
                    offending.append((path.name, str(alias.asname)))
    assert not offending, offending


def test_the_orchestrator_never_names_another_workflow() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_ACCOUNT_DIR):
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_ORCHESTRATOR_CALLS:
                offending.append((path.name, str(name)))
    assert not offending, offending


def test_the_orchestrator_never_reaches_a_window_attribute() -> None:
    forbidden = {
        "window",
        "main_window",
        "desktop",
        "context",
        "services",
        "pages",
        "runtime",
    }
    offending: list[tuple[str, str]] = []
    for path in _python_files(_ACCOUNT_DIR):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr in forbidden
            ):
                offending.append((path.name, node.attr))
    assert not offending, offending


def test_the_models_and_queries_modules_are_qt_free() -> None:
    for path in (_MODELS_PATH, _QUERIES_PATH):
        modules = _imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name


def test_the_models_module_is_importable_without_qt() -> None:
    """Loaded by *file path* in a fresh interpreter on purpose."""

    import subprocess
    import sys

    snippet = (
        "import importlib.util, sys; "
        f"spec = importlib.util.spec_from_file_location('probe', r'{_MODELS_PATH}'); "
        "module = importlib.util.module_from_spec(spec); "
        "sys.modules['probe'] = module; "
        "spec.loader.exec_module(module); "
        "raise SystemExit(1 if 'PySide6' in sys.modules else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# -- Guard D: the public surface is exactly the declared one -------------


def test_the_orchestrator_exposes_exactly_the_declared_surface() -> None:
    public = _public_members(_ORCHESTRATOR_PATH, "AccountOrchestrator")
    declared = set(PUBLIC_READ_SURFACE)
    assert public == declared, sorted(public ^ declared)


def test_the_orchestrator_exposes_the_declared_signals() -> None:
    signals = _signal_members(_ORCHESTRATOR_PATH, "AccountOrchestrator")
    assert signals == set(PUBLIC_SIGNALS), sorted(signals ^ set(PUBLIC_SIGNALS))


def test_the_orchestrator_holds_no_second_portfolio() -> None:
    """No ``self._portfolio`` and no ``self.portfolio`` assignment anywhere."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    tree = _tree(_ORCHESTRATOR_PATH)
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr in {"_portfolio", "portfolio"}
            ):
                pytest.fail(f"orchestrator stores {target.attr} at line {node.lineno}")
    # The only ``portfolio`` is the read-only delegating property.
    tree2 = _tree(_ORCHESTRATOR_PATH)
    properties = {
        node.name
        for node in _class_of(_ORCHESTRATOR_PATH, "AccountOrchestrator").body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(d, ast.Name) and d.id == "property"
            for d in node.decorator_list
        )
    }
    assert "portfolio" in properties


def test_the_orchestrator_portfolio_is_a_delegation() -> None:
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "return self._application.portfolio" in source


# -- Guard E: line budgets ----------------------------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_account_file_stays_inside_its_budget(name: str, budget: int) -> None:
    lines = len((_ACCOUNT_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


def test_the_orchestrator_does_not_become_the_new_god_file() -> None:
    lines = len(_ORCHESTRATOR_PATH.read_text(encoding="utf-8").splitlines())
    assert lines <= 320, f"orchestrator.py is {lines} lines"
