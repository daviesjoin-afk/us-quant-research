"""Architecture guards for the v2O-C5B targeted session extraction.

Structural, not behavioural: these read the current source tree and assert that the
ownership the extraction moved really moved, and that the new capability cannot
reach sideways into another workflow.  The behaviour lives in
``tests/test_desktop_targeted_session_orchestrator.py``; the real wiring in
``tests/test_desktop_v2_targeted_wiring.py``; the service boundary in
``tests/test_desktop_targeted_session_service.py``.

The guards that matter most:

* the window holds **no** session state -- not the target status, not the minute
  status, not the preflight -- and declares no compatibility property or forwarding
  method for any of them.  A forwarding method is the tempting way to keep a small
  diff, and it is a trap: every consumer keeps working without naming the owner, so
  "who owns the target status?" stops being one grep;
* ``render_session`` has exactly one production caller -- the session capability.
  Before this round the window was that caller, which is the asymmetry the C5A
  round recorded and this one closes;
* the capability imports no other orchestrator, no shell, no Shadow engine, no
  Shadow store and no market worker.  Its seven external facts arrive as
  ``Callable`` providers and its one cross-workflow write arrives as a narrow
  injected command;
* the Shadow snapshot is read, never stored.  The session snapshot must have no
  Shadow field at all, which is what keeps v2O-D's runtime out of this round.

The dependency guard is an **allowlist**, not a list of bans, for the reason the
C5A round established: a ban list is written in capability vocabulary and is blind
to the symbol that needs forbidding living outside it.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import fields

import pytest

from us_quant.desktop_v2.orchestration.research.targeted.session import (
    TargetedSessionSnapshot,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_RESEARCH = _SRC / "desktop_v2" / "orchestration" / "research"
_SESSION_DIR = _RESEARCH / "targeted" / "session"
_SERVICE_PATH = _SRC / "desktop_targeted_session_service.py"
_PAGE_PATH = (
    _SRC / "desktop_v2" / "pages" / "research" / "targeted" / "page.py"
)
_CONTROLS_PATH = (
    _SRC / "desktop_v2" / "pages" / "research" / "targeted" / "controls.py"
)

#: The per-file line budgets.  Exceeding one means a responsibility leaked in
#: (Shadow, Market, Account, the evidence); raise the *code's* separation, not
#: these numbers.
LINE_BUDGETS = {
    "orchestrator.py": 320,
    "models.py": 140,
    "queries.py": 200,
    "__init__.py": 60,
}
SERVICE_LINE_BUDGET = 180

#: Exactly which modules each file may import.  Compared for **equality**, so an
#: undeclared dependency fails rather than passing silently.
ALLOWED_IMPORTS = {
    "orchestrator.py": {
        "__future__",
        "collections.abc",
        "dataclasses",
        "decimal",
        "PySide6.QtCore",
        "us_quant.desktop_targeted_session_service",
        "us_quant.desktop_v2.orchestration.research.targeted.session",
        "us_quant.desktop_v2.orchestration.research.targeted.session.models",
        "us_quant.desktop_v2.pages.research.targeted.models",
        "us_quant.desktop_v2.pages.research.targeted.session_presenter",
        "us_quant.shadow.models",
        "us_quant.trading.domain.account",
        "us_quant.trading.domain.market",
        "us_quant.trading.domain.strategy",
        "us_quant.universe",
    },
    "models.py": {
        "__future__",
        "dataclasses",
        "us_quant.targeted_preflight",
    },
    "queries.py": {
        "__future__",
        "re",
        "us_quant.desktop_v2.pages.research.targeted.models",
        "us_quant.minute_data",
        "us_quant.trading.domain.market",
        "us_quant.universe",
    },
}

#: Symbols the capability may never import, by name.  A module-path guard cannot
#: see ``from <allowed_module> import <forbidden>``, so both directions are checked.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "ScannerOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "ShadowPaperEngine",
    "ShadowPaperStore",
    "ShadowWorkflowController",
    "PaperWorkflow",
    "PaperTradingService",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "BrokerAccountApplication",
    "RuntimeSupervisor",
    "TaskThread",
    "DesktopTaskController",
    "DesktopShellV2",
    "QWidget",
    "QMessageBox",
)

#: The same ban as module paths.  ``Shadow*`` / ``Paper*`` / ``Execution*`` are
#: prefixes rather than exact names, and a symbol guard would miss
#: ``import us_quant.shadow.engine`` entirely.  ``us_quant.shadow.models`` is
#: deliberately *not* banned: ``ShadowSnapshot`` is the type of the fact this
#: capability renders, and importing the plain-data model is not owning the engine.
FORBIDDEN_MODULE_PREFIXES = (
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.runtime_supervisor",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.research.universe",
    "us_quant.desktop_v2.orchestration.research.history",
    "us_quant.desktop_v2.orchestration.research.scanner",
    "us_quant.desktop_v2.orchestration.research.backtest",
    "us_quant.desktop_v2.orchestration.research.cross_section",
    "us_quant.desktop_v2.orchestration.research.targeted.evidence",
    "us_quant.desktop_v2.pages.dashboard",
    "us_quant.desktop_v2.pages.system",
    "us_quant.trading.runtime",
    "us_quant.trading.application",
    "us_quant.shadow.config",
    "us_quant.shadow.engine",
    "us_quant.shadow.store",
    "us_quant.shadow.trade_logic",
    "us_quant.paper",
    "us_quant.execution",
)

#: ``us_quant.desktop`` is the window module, so the capability may not reach it
#: at all.
FORBIDDEN_EXACT_MODULES = ("us_quant.desktop",)

#: The two ``us_quant.desktop*`` modules the capability and its service may reach:
#: the service, and nothing else.
ALLOWED_DESKTOP_MODULES = ("us_quant.desktop_targeted_session_service",)

#: These must never be declared.  One object owning the session *and* the evidence
#: *and* the Shadow runtime is the bag this stage forbids -- it would be a second
#: ``MainWindow`` rather than a decomposition of the first.
FORBIDDEN_AGGREGATES = (
    "TargetedOrchestrator",
    "TargetedManager",
    "TargetedContext",
    "TargetedState",
    "TargetedServices",
    "TargetedSessionManager",
    "TargetedSessionContext",
    "TargetedSessionServices",
    "ShadowSessionOrchestrator",
    "TargetedShadowOrchestrator",
    "ResearchOrchestrator",
    "ResearchManager",
    "ResearchContext",
    "DesktopContext",
    "ResearchDependencies",
    "DesktopServices",
    "SessionEnvironment",
)

#: The files that must never exist: a single targeted orchestrator at the
#: capability root, which is the shape this split exists to prevent.
FORBIDDEN_PATHS = (
    _RESEARCH / "targeted" / "orchestrator.py",
    _RESEARCH / "targeted" / "models.py",
    _RESEARCH / "targeted" / "manager.py",
)

#: Spec: the window state the extraction deleted rather than shimmed.
RETIRED_WINDOW_STATE = (
    "self._target_status",
    "self._minute_status",
    "self.target_preflight_result",
)

#: The window methods the session extraction deleted rather than forwarded.
RETIRED_WINDOW_METHODS = (
    "_current_target_symbol",
    "_target_symbol_requested",
    "_target_subscribe_requested",
    "_apply_target_symbol",
    "_sync_targeted_symbol_to_stream",
    "_refresh_minute_data_status",
    "_refresh_target_preflight",
    "_targeted_controls",
    "_publish_targeted_session_view",
    "_shadow_strategy_selection_changed",
)

#: Every window method whose name starts with a targeted word, after this round.
#: The four composition helpers answer "what is the current value of a fact this
#: window can see?" and decide nothing; the Shadow pair stays because the Shadow
#: runtime is v2O-D.
ALLOWED_WINDOW_TARGETED_METHODS = (
    "_connect_targeted_validation_page",
    "_report_targeted_session_refusal",
    "_report_targeted_evidence_refusal",
    "_record_targeted_evidence_runtime_event",
    "_focus_targeted_evidence",
    "_start_shadow",
    "_stop_shadow",
    "_selected_shadow_strategy_record",
    "_targeted_account_snapshot",
    "_targeted_displayed_strategy",
    "_targeted_strategy_options",
    "_targeted_strategy_selected",
)

#: The composition helpers that must exist for the capability to read its inputs.
REQUIRED_WINDOW_HELPERS = (
    "_report_targeted_session_refusal",
    "_targeted_account_snapshot",
    "_targeted_displayed_strategy",
    "_targeted_strategy_options",
    "_targeted_strategy_selected",
)

#: The capability's own signals.  Two, and neither is a runtime event: apply and
#: subscribe have never recorded one, and inventing one would be a product change.
REQUIRED_SESSION_SIGNALS = ("refused = Signal(str, str, str)", "log_requested = Signal(str)")

#: The public surface, pinned.  Anything not listed is private by construction.
SESSION_PUBLIC_API = (
    "snapshot",
    "refresh_strategy_options",
    "request_strategy_selection",
    "adopt_target_draft",
    "request_target_apply",
    "request_target_subscribe",
    "refresh_minute_status",
    "refresh_preflight",
    "render_current",
)

#: The four accessors that must not exist.  The snapshot is one immutable value
#: precisely so these four cannot drift apart.
FORBIDDEN_ACCESSORS = (
    "target_draft",
    "target_status",
    "minute_status",
    "preflight",
)

#: A refresh-everything method would reintroduce the implicit fan-out that the
#: per-change refreshes exist to avoid.
FORBIDDEN_REFRESH_AGGREGATES = (
    "refresh_all",
    "refresh_session",
    "reload",
    "refresh",
)


# -- helpers --------------------------------------------------------------


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _declared_names(path: pathlib.Path) -> set[str]:
    """Every name ``MainWindow`` declares: methods, properties and assignments."""

    declared: set[str] = set()
    for node in _main_window(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared.add(node.name)
    for node in ast.walk(_main_window(path)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self":
                declared.add(node.attr)
    return declared


def _called_pieces(path: pathlib.Path) -> set[tuple[str, str]]:
    """Every ``<expr>.<attr>(...)`` in ``path`` as ``(owner, attr)``."""

    pieces: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            pieces.add((ast.unparse(func.value), func.attr))
    return pieces


# -- Guard A: the window holds no session state ---------------------------


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE)
def test_the_window_holds_no_session_state(needle: str) -> None:
    """Checked against the parsed code, not the file text."""

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    accessed = {
        f"self.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert needle not in accessed, needle


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_declares_no_retired_session_method(name: str) -> None:
    """No compatibility alias, property or forwarding method restores the surface."""

    assert name not in _declared_names(_DESKTOP), name


def test_the_window_declares_no_targeted_method_outside_the_declared_set() -> None:
    """One grep answers "what does the window still do for Targeted?"."""

    declared = {
        node.name
        for node in _main_window(_DESKTOP).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    targeted = {name for name in declared if "target" in name or "evidence" in name}
    assert targeted <= set(ALLOWED_WINDOW_TARGETED_METHODS), sorted(
        targeted - set(ALLOWED_WINDOW_TARGETED_METHODS)
    )


@pytest.mark.parametrize("name", REQUIRED_WINDOW_HELPERS)
def test_the_window_declares_the_composition_helpers_it_needs(name: str) -> None:
    assert name in _declared_names(_DESKTOP), name


def test_the_window_keeps_the_shadow_runtime_it_still_owns() -> None:
    """v2O-D is the next round; this one must not have taken Shadow with it."""

    declared = _declared_names(_DESKTOP)
    for name in (
        "_start_shadow",
        "_stop_shadow",
        "_selected_shadow_strategy_record",
        "shadow_engine",
        "shadow_snapshot",
    ):
        assert name in declared, name


# -- Guard B: exactly one owner of the session render ----------------------


def _render_callers(attr: str) -> list[tuple[str, str]]:
    callers: list[tuple[str, str]] = []
    for path in _python_files(_SRC):
        if path == _PAGE_PATH:
            continue
        for owner, name in _called_pieces(path):
            if name == attr:
                callers.append((path.name, owner))
    return callers


def test_the_session_capability_is_the_only_render_session_caller() -> None:
    callers = _render_callers("render_session")
    assert callers == [("orchestrator.py", "self._page")], callers


def test_the_window_paints_neither_half_of_the_targeted_page() -> None:
    pieces = {attr for _owner, attr in _called_pieces(_DESKTOP)}
    assert "render_session" not in pieces
    assert "render_evidence" not in pieces


def test_the_session_capability_never_paints_the_evidence_half() -> None:
    source = (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "render_evidence" not in source


def test_the_session_capability_is_the_only_targeted_set_strategy_options_caller() -> None:
    """MainWindow no longer paints the targeted combo; the capability does.

    Scoped to the *targeted* page: the execution page has a combo of its own with
    its own owner, and a blanket rule would forbid a second capability's
    legitimate paint.
    """

    window_calls = {
        owner
        for owner, attr in _called_pieces(_DESKTOP)
        if attr == "set_strategy_options"
    }
    assert window_calls == {"self.execution_page"}, sorted(window_calls)

    callers = {
        owner
        for owner, attr in _called_pieces(_SESSION_DIR / "orchestrator.py")
        if attr == "set_strategy_options"
    }
    assert callers == {"self._page"}, sorted(callers)


def test_the_session_capability_is_the_only_set_target_symbol_caller() -> None:
    """The capability owns the editor writes, so nothing else can fight it."""

    callers = [
        (path.name, owner)
        for path in _python_files(_SRC)
        if path != _PAGE_PATH and path != _CONTROLS_PATH
        for owner, attr in _called_pieces(path)
        if attr == "set_target_symbol"
    ]
    assert callers == [("orchestrator.py", "self._page")], callers


# -- Guard C: the page's draft intent -------------------------------------


def test_the_page_publishes_the_draft_intent() -> None:
    assert "target_draft_changed = Signal(str)" in _PAGE_PATH.read_text(
        encoding="utf-8"
    )
    controls = _CONTROLS_PATH.read_text(encoding="utf-8")
    assert "target_draft_changed = Signal(str)" in controls
    assert "textChanged.connect(self._target_text_changed)" in controls


def test_the_programmatic_target_setter_blocks_the_edit_signal() -> None:
    """The loop guard: an orchestrator -> page -> orchestrator write is silent."""

    source = _CONTROLS_PATH.read_text(encoding="utf-8")
    method = source.split("def set_target_symbol(")[1].split("def ")[0]
    assert "blockSignals(True)" in method
    assert "blockSignals(blocked)" in method


def test_the_draft_signal_reaches_only_the_draft_adoption() -> None:
    """One keystroke is one assignment: the wiring has exactly one receiver."""

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    receivers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "connect":
            continue
        if "target_draft_changed" in ast.unparse(func.value):
            receivers.append(ast.unparse(node.args[0]))
    assert receivers == ["session.adopt_target_draft"], receivers


def test_the_window_never_reaches_through_the_page_for_session_truth() -> None:
    """The page's editor is not a truth the window may read.

    The retired ``_current_target_symbol`` read the ``QLineEdit``; the target now
    comes from the session snapshot, so a surviving window-side read of
    ``target_symbol()`` would be the second owner coming back -- and it would make
    "which symbol does the engine actually trade?" ambiguous again.
    """

    page_reads = {
        attr
        for owner, attr in _called_pieces(_DESKTOP)
        if owner == "self.targeted_validation_page"
    }
    assert "target_symbol" not in page_reads, sorted(page_reads)
    assert "selected_strategy_version_id" not in page_reads, sorted(page_reads)


def test_the_shadow_snapshot_bridge_repaints_the_session() -> None:
    """Shadow truth reaches the page by asking for a repaint, not by a copy."""

    method = _method_source(_DESKTOP, "_on_market_snapshot_changed")
    assert "self.shadow_snapshot = self.shadow_engine.on_stream(snapshot)" in method
    assert (
        "self.targeted_session_orchestrator.render_current()" in method
    ), "the engine produced a new snapshot, so the session panel is stale"


def _method_source(path: pathlib.Path, name: str) -> str:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_the_capability_owns_no_shadow_lifecycle() -> None:
    """Starting and stopping the engine is v2O-D's, not this round's.

    The capability may read a snapshot through a provider; a start or stop method
    here would mean the session had taken the runtime with it.
    """

    declared = {
        node.name
        for node in ast.walk(
            ast.parse((_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8"))
        )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "start_shadow",
        "stop_shadow",
        "on_stream",
        "start_engine",
        "shadow_session",
    ):
        assert forbidden not in declared, forbidden


def test_the_evidence_target_provider_reads_the_session_snapshot() -> None:
    """Not the page editor: the session snapshot is the one canonical draft.

    Asserted on the *wiring* because that is where the choice is made -- the
    capability's own signature still accepts any callable.
    """

    source = _DESKTOP.read_text(encoding="utf-8")
    assert (
        "lambda: self.targeted_session_orchestrator.snapshot.target_draft" in source
    ), "the evidence run's symbol must come from the session snapshot"
    assert (
        "target_symbol_provider=(\n                self.targeted_validation_page.target_symbol"
        not in source
    ), "the page editor must not be the evidence run's symbol source"


# -- Guard D: the dependency direction ------------------------------------


def test_each_session_file_imports_only_what_it_declares() -> None:
    for name, allowed in sorted(ALLOWED_IMPORTS.items()):
        actual = _imports(_SESSION_DIR / name)
        assert actual == allowed, (
            name,
            sorted(actual - allowed),
            sorted(allowed - actual),
        )


def test_the_capability_imports_no_forbidden_symbol() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SESSION_DIR):
        for name in _imported_names(path):
            if name in FORBIDDEN_SYMBOLS:
                offending.append((path.name, name))
    assert not offending, offending


def test_the_capability_imports_no_forbidden_module() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SESSION_DIR):
        for module in _imports(path):
            if module in FORBIDDEN_EXACT_MODULES:
                offending.append((path.name, module))
                continue
            if (
                module.startswith("us_quant.desktop")
                and module not in ALLOWED_DESKTOP_MODULES
                and not module.startswith("us_quant.desktop_v2")
            ):
                offending.append((path.name, module))
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(f"{prefix}."):
                    offending.append((path.name, module))
    assert not offending, offending


def test_the_service_imports_no_qt_and_no_orchestration() -> None:
    """The arrow points one way.  A reverse import would let the service reach back."""

    modules = _imports(_SERVICE_PATH)
    for module in sorted(modules):
        assert not module.startswith("PySide6"), module
        assert not module.startswith("us_quant.desktop_v2"), module
        assert module != "us_quant.desktop", module


def test_the_service_stays_qt_free_and_widget_free() -> None:
    tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
    identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in (
        "QMessageBox",
        "QWidget",
        "MainWindow",
        "RuntimeEventStore",
        "MarketOrchestrator",
        "AccountOrchestrator",
        "UniverseOrchestrator",
        "ShadowPaperEngine",
    ):
        assert forbidden not in identifiers, forbidden


def test_the_service_hard_disables_the_broker_route() -> None:
    """Research Targeted cannot acquire broker execution authority."""

    source = _SERVICE_PATH.read_text(encoding="utf-8")
    assert "broker_orders_available=False" in source
    assert "broker_orders_available: bool" not in source


def test_the_service_keeps_the_decimal_types_it_is_handed() -> None:
    """Whole-share sizing is the domain evaluator's, on ``Decimal`` end to end."""

    source = _SERVICE_PATH.read_text(encoding="utf-8")
    for forbidden in ("float(", "Decimal(str(", "int("):
        assert forbidden not in source, forbidden


def test_the_capability_reads_its_dependencies_through_callables() -> None:
    source = (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
    for provider in (
        "universe_provider: Callable",
        "market_snapshot_provider: Callable",
        "account_provider: Callable",
        "selected_strategy_provider: Callable",
        "shadow_snapshot_provider: Callable",
        "market_set_subscription: Callable",
        "market_start: Callable",
    ):
        assert provider in source, provider
    # And never another orchestrator as an object.
    for forbidden in ("market_orchestrator", "account_orchestrator", "universe_orchestrator"):
        assert forbidden not in source, forbidden


def test_the_capability_does_not_own_the_market_lifecycle() -> None:
    """Subscribing and starting are the whole cross-workflow surface.

    ``_market_is_live`` is a *read* the command needs in order to refuse; polling,
    stopping, switching the provider and inspecting the worker are what ownership
    would look like, and none of them is here.
    """

    source = (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
    for forbidden in (
        "worker_running",
        "request_switch",
        "stop_polling",
        "market_orchestrator",
        "stop(",
        "active_source_id",
        "selected_provider",
    ):
        assert forbidden not in source, forbidden


# -- Guard E: the Shadow snapshot is read, never stored --------------------


def test_the_session_snapshot_has_no_shadow_field() -> None:
    """Shadow state is external truth; a copy here would be a second owner."""

    names = {field.name for field in fields(TargetedSessionSnapshot)}
    assert names == {
        "target_draft",
        "target_status",
        "minute_status",
        "preflight",
    }, names
    for forbidden in ("shadow_snapshot", "shadow_active", "positions", "fills"):
        assert forbidden not in names, forbidden


def test_the_capability_never_writes_shadow_state_into_its_snapshot() -> None:
    source = (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
    for forbidden in (
        "set_shadow_snapshot",
        "shadow_snapshot=",
        "shadow_engine",
    ):
        assert forbidden not in source, forbidden


# -- Guard F: the public surface is one snapshot, not four accessors -------


@pytest.mark.parametrize("name", FORBIDDEN_ACCESSORS)
def test_the_capability_exposes_no_separate_accessor(name: str) -> None:
    from us_quant.desktop_v2.orchestration.research.targeted.session import (
        TargetedSessionOrchestrator,
    )

    assert not hasattr(TargetedSessionOrchestrator, name), name


def test_the_capability_publishes_the_snapshot() -> None:
    from us_quant.desktop_v2.orchestration.research.targeted.session import (
        TargetedSessionOrchestrator,
    )

    assert isinstance(
        TargetedSessionOrchestrator.snapshot, property
    ), "the snapshot is one immutable value, published as a property"


@pytest.mark.parametrize("name", SESSION_PUBLIC_API)
def test_the_capability_keeps_its_declared_public_method(name: str) -> None:
    from us_quant.desktop_v2.orchestration.research.targeted.session import (
        TargetedSessionOrchestrator,
    )

    if name == "snapshot":
        assert hasattr(TargetedSessionOrchestrator, name), name
        return
    assert callable(getattr(TargetedSessionOrchestrator, name, None)), name


@pytest.mark.parametrize("name", REQUIRED_SESSION_SIGNALS)
def test_the_capability_declares_its_signals(name: str) -> None:
    source = (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert name in source, name


@pytest.mark.parametrize("name", FORBIDDEN_REFRESH_AGGREGATES)
def test_the_capability_has_no_refresh_everything_method(name: str) -> None:
    """Each change refreshes what it actually affects -- no implicit fan-out."""

    declared = {
        node.name
        for node in ast.walk(ast.parse(
            (_SESSION_DIR / "orchestrator.py").read_text(encoding="utf-8")
        ))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert name not in declared, name


# -- Guard G: no aggregate, no forbidden file -----------------------------


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_the_forbidden_targeted_file_does_not_exist(path: pathlib.Path) -> None:
    assert not path.exists(), path


def test_no_forbidden_aggregate_is_declared() -> None:
    defined: set[str] = set()
    for path in _python_files(_SRC):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                defined.add(node.name)
    for name in FORBIDDEN_AGGREGATES:
        assert name not in defined, name


def test_the_targeted_root_still_holds_no_shared_models() -> None:
    children = {
        path.name
        for path in (_RESEARCH / "targeted").iterdir()
        if path.name != "__pycache__"
    }
    assert children == {"__init__.py", "evidence", "session"}, children


# -- Guard H: line budgets ------------------------------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_session_file_stays_inside_its_budget(name: str, budget: int) -> None:
    lines = len((_SESSION_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


def test_the_service_stays_inside_its_budget() -> None:
    lines = len(_SERVICE_PATH.read_text(encoding="utf-8").splitlines())
    assert lines <= SERVICE_LINE_BUDGET, lines
