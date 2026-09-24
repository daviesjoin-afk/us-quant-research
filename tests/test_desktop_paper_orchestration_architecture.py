"""Architecture guards for the v2O-E1 Paper launch orchestration extraction.

Structural, not behavioural: they read the source tree and assert that the ownership
this round moved really moved, and that the new capability cannot reach back into the
window, sideways into another capability, or down into the broker.

Seven guards matter most, and each fails for the right reason:

* **Guard A -- the window left the launch.**  ``_start_auto_quant`` and
  ``_auto_order_service_connected`` are gone, along with the retired plan mirror and
  the three helpers that only they called.  No shim and no forwarding property, which
  is why the check is for *declared* names rather than merely called ones;
* **Guard B -- the dependency boundary.**  ``PaperOrchestrator`` imports no other
  capability orchestrator, no IBKR adapter, no desktop composition module and no
  widget.  ``QtCore`` is allowed, because every sibling orchestrator exposes signals
  that way;
* **Guard C -- no second truth.**  The capability stores no phase, no plan mirror, no
  runtime, no risk verdict, no order intent, no candidate handle and no broker
  connection state;
* **Guard D -- no broker bypass.**  No ``placeOrder`` / ``cancelOrder`` /
  ``reqGlobalCancel`` / ``submit`` / ``cancel`` appears anywhere in it: submission is
  the execution application's, reached only through the runtime;
* **Guard E -- the candidate borrow.**  ``candidate_service(`` has exactly one call
  site, stores into a local, and never reaches an attribute;
* **Guard F -- the exact public surface.**  ``start()`` plus the four published
  signals, asserted in both directions so a convenience method fails here;
* **Guard G -- the exact public surface.**  ``start()`` plus the four published signals,
  asserted in both directions so a convenience method fails here.

There is deliberately **no line-count gate of any kind**, not even a renamed "navigability
threshold": a file's length is a symptom, and a cap pinned at whatever a file happens to be
fails on a good change while passing on a bad one.  Architecture is asserted by guards that
name a property -- single responsibility, unambiguous ownership, a stable dependency
direction, no second truth, no service bag, and a locked safety order -- not by a number.

The import set is closed by an **allowlist** as well as a denylist.  A denylist only
forbids the couplings someone thought to name; the allowlist means a new dependency
has to be declared here on purpose, where a reviewer sees it.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_PAPER_DIR = _SRC / "desktop_v2" / "orchestration" / "paper"
_ORCHESTRATOR_PATH = _PAPER_DIR / "orchestrator.py"
_QUERIES_PATH = _PAPER_DIR / "queries.py"
_MODELS_PATH = _PAPER_DIR / "models.py"

#: The launch state and helpers the extraction deleted rather than shimmed.
RETIRED_WINDOW_LAUNCH_STATE = (
    "_active_auto_launch_plan",
    "_next_auto_launch_attempt",
)

#: The window methods the extraction deleted.  Declared as an exact set so a
#: re-added one -- or a compatibility alias with the same name -- fails here.
RETIRED_WINDOW_LAUNCH_METHODS = (
    "_start_auto_quant",
    "_auto_order_service_connected",
    "_reject_unpublished_auto_candidate",
    "_reject_auto_launch_without_service",
    "_current_auto_launch_matches",
    "_reset_auto_launch_controls",
)

#: The launch method that legitimately stays: the operator confirmation is
#: presentation, and the capability may not import ``QMessageBox``.
RETAINED_WINDOW_LAUNCH_METHODS = (
    "_confirm_and_start_auto_quant",
    "_auto_quant_order_channel",
    "_build_paper_session",
    "_on_paper_session_published",
    "_report_paper_launch_refusal",
    "_record_paper_launch_event",
)

#: What the Paper package may import.  Each entry is a capability, a shared
#: boundary or a declared port -- not merely a module it happens not to use yet.
ALLOWED_IMPORTS = (
    "__future__",
    "collections.abc",
    # ``copy.deepcopy`` for the detached parameter snapshot.  Stdlib, no I/O.
    "copy",
    "dataclasses",
    "decimal",
    "typing",
    # Qt, for the signals every orchestrator in this layer exposes.  Only
    # ``QtCore``: see the widget guard below.
    "PySide6.QtCore",
    "us_quant.auto_launch",
    "us_quant.paper_order_models",
    "us_quant.desktop_v2.orchestration.paper",
    "us_quant.desktop_v2.orchestration.tasking",
    # The Paper lifecycle's own modules.  Naming the workflow controller and the
    # order-service owner is legitimate here, unlike in Shadow: Paper *is* their
    # capability.  The two that must never appear are the concrete IBKR adapter and
    # the desktop composition root.
    "us_quant.trading.application.paper",
    # The governed strategy domain: ``StrategyIdentity`` and ``parameter_hash_for``,
    # used to detach and verify the frozen parameter snapshot.  A pure value type and
    # a pure hash function -- importing them names no adapter and no service.
    "us_quant.trading.domain.strategy",
    "us_quant.trading.runtime.models",
    "us_quant.trading.runtime.paper_contracts",
    "us_quant.trading.runtime.paper_models",
    "us_quant.trading.runtime.preflight",
    "us_quant.trading.runtime.trading",
    "us_quant.trading.runtime.workflow",
    "us_quant.trading.runtime.workflow_state",
)

#: The same rule stated the other way, kept because it names the couplings the spec
#: calls out and gives a more legible failure message.  Both run.
FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.research",
    "us_quant.desktop_v2.orchestration.shadow",
    "us_quant.desktop_v2.workflows",
    "us_quant.workflow_controller",
    "us_quant.components",
    "us_quant.runtime_supervisor",
    "us_quant.runtime_events",
    "us_quant.artifact_state",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "ibapi",
)

#: Names the package must not *call*, even via an allowed module.
FORBIDDEN_CALLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "ScannerOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "TargetedSessionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "ShadowOrchestrator",
    "WorkflowController",
    "PaperSessionCoordinator",
    "SessionRecovery",
    "ManualReconciliation",
    "ExecutionLeaseManager",
    "RiskApplication",
    "ExecutionApplication",
    "RuntimeSupervisor",
    "RuntimeEventStore",
    "TaskThread",
    "DesktopTaskController",
    "IBKRExecutionAdapter",
    "IBKRPaperOrderService",
    "IBKRConnectionConfig",
)

#: Broker methods that would bypass the execution application entirely.
FORBIDDEN_BROKER_CALLS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "submit_approved",
    "submit",
    "cancel",
    "reserve",
)

#: Qt classes the orchestrator may not touch.  A capability publishes facts and
#: signals; the window paints.  ``QMessageBox`` is the sharpest of these: the
#: operator confirmation deliberately stayed on the window for exactly this reason.
FORBIDDEN_QT_NAMES = (
    "QWidget",
    "QLabel",
    "QPushButton",
    "QComboBox",
    "QLineEdit",
    "QTableWidget",
    "QTableView",
    "QMessageBox",
    "QTimer",
    "QThread",
)

#: The orchestrator's public surface, asserted exactly in both directions.
PUBLIC_SURFACE = ("start",)

#: The Qt signals the window relies on.  ``session_published`` carries the
#: publication value object; ``runtime_event_requested`` and ``log_requested`` are
#: the same request-not-own pattern every sibling capability uses.
PUBLIC_SIGNALS = (
    "refused",
    "log_requested",
    "session_published",
    "runtime_event_requested",
)

#: Every ``self.paper_orchestrator.<name>`` the window may reach for.
WINDOW_ALLOWED_ORCHESTRATOR_MEMBERS = set(PUBLIC_SURFACE) | set(PUBLIC_SIGNALS)

#: The page intent wiring: the start signal reaches the window's confirmation gate,
#: which forwards to the capability.  The gate is presentation; the orchestration is
#: the capability's.
START_INTENT_WIRING = (
    ("start_requested", "self._confirm_and_start_auto_quant"),
)


# -- helpers ------------------------------------------------------------


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


def _declared_names(path: pathlib.Path) -> set[str]:
    """Every name the window declares, including aliases and properties.

    Wider than "methods" on purpose: a compatibility property or a forwarding method
    under a retired name would restore the old surface, so the guard has to fail on
    those too.
    """

    names: set[str] = set()
    for node in _main_window(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


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


def _public_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _tree(path).body:
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not member.name.startswith("_"):
                    members.add(member.name)
    return members


def _signal_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _tree(path).body:
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        for member in node.body:
            value = None
            targets: list[ast.expr] = []
            if isinstance(member, ast.Assign):
                targets, value = list(member.targets), member.value
            elif isinstance(member, ast.AnnAssign):
                targets, value = [member.target], member.value
            if value is None:
                continue
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == "Signal"
            ):
                members.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
    return members


def _function_source(path: pathlib.Path, name: str) -> str:
    """The source of one ``PaperOrchestrator`` method, for ordering assertions."""

    source = path.read_text(encoding="utf-8")
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ClassDef) and node.name == "PaperOrchestrator":
            for member in node.body:
                if (
                    isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == name
                ):
                    return ast.get_source_segment(source, member) or ""
    raise AssertionError(f"PaperOrchestrator.{name} not found")


def _stored_self_attrs(path: pathlib.Path) -> set[str]:
    return _assigned_self_attrs(path)


def _called_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name:
                names.add(str(name))
    return names


# -- Guard A: the window no longer runs the launch -----------------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_LAUNCH_STATE)
def test_the_window_assigns_no_launch_state(attribute: str) -> None:
    """The plan mirror and the attempt counter are gone, with no property either."""

    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute
    assert attribute not in _declared_names(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("name", RETIRED_WINDOW_LAUNCH_METHODS)
def test_the_retired_launch_methods_are_gone(name: str) -> None:
    assert name not in _declared_names(_DESKTOP_PATH), name


@pytest.mark.parametrize("name", RETAINED_WINDOW_LAUNCH_METHODS)
def test_the_composition_and_presentation_seams_remain(name: str) -> None:
    """What legitimately stays: composition, and the operator confirmation.

    Asserted so the guard cannot pass by the window having dropped the launch
    entirely -- something has to build the runtime and something has to ask the
    operator.
    """

    assert name in _declared_names(_DESKTOP_PATH), name


def test_the_window_composes_the_orchestrator_once() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert source.count("PaperOrchestrator(") == 1


def test_the_window_never_reaches_through_the_orchestrator() -> None:
    """Every ``self.paper_orchestrator.<name>`` must be a declared member."""

    offending: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "paper_orchestrator"
        ):
            if node.attr not in WINDOW_ALLOWED_ORCHESTRATOR_MEMBERS:
                offending.append((node.attr, node.lineno))
    assert not offending, offending


def test_the_start_intent_still_reaches_its_handler() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for signal, target in START_INTENT_WIRING:
        assert f"{signal}.connect({target})" in source, signal


def test_the_confirmation_gate_forwards_to_the_capability() -> None:
    """The page reaches the orchestrator *through* the confirmation, not instead.

    §12 of the round wants the page intent to end at ``PaperOrchestrator.start``
    rather than at a launch handler.  The one hop through the confirmation is the
    presentation step the capability may not perform, so it is pinned here: the gate
    must actually forward, and must not itself launch.
    """

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    start = source.index("def _confirm_and_start_auto_quant")
    end = source.index("def ", start + 10)
    gate = source[start:end]
    assert "self.paper_orchestrator.start()" in gate
    # It decides nothing itself: no preflight, no workflow transition.
    assert "begin_connecting" not in gate
    assert "connect_candidate" not in gate
    assert "publish_armed" not in gate


def test_the_window_no_longer_owns_a_launch_plan_or_attempt_counter() -> None:
    """Neither the plan fingerprint nor the counter is built on the window."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "build_auto_launch_plan(" not in source
    assert "auto_launch_plan_matches(" not in source
    assert "AutoLaunchPlan" not in source


def test_the_window_no_longer_submits_the_connect_task() -> None:
    """The broker task body is the capability's; the window only admits tasks."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "self.paper_trading.connect_candidate(" not in source
    assert "CONNECT_PROGRESS" not in source
    assert "connect_task" not in source


def test_the_window_no_longer_decides_staleness() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "reject_connecting" not in source
    assert "discard_candidate" not in source


def test_the_launch_gate_reads_the_workflow_phase() -> None:
    """``_launch_locked`` reads the canonical phase, not a mirrored plan."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    start = source.index("def _launch_locked")
    end = source.index("def ", start + 10)
    gate = source[start:end]
    assert "self.paper_trading.phase() is PaperWorkflowPhase.CONNECTING" in gate


# -- Guard B: dependency boundary ----------------------------------------


def test_the_package_imports_no_other_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for module in _matches(_imports(path), FORBIDDEN_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_package_imports_nothing_outside_the_allowlist() -> None:
    """The import set is closed, not merely denylisted."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for module in sorted(_imports(path)):
            allowed = any(
                module == entry or module.startswith(f"{entry}.")
                for entry in ALLOWED_IMPORTS
            )
            if not allowed:
                offending.append((path.name, module))
    assert not offending, offending


def test_the_package_never_imports_a_forbidden_symbol() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in FORBIDDEN_CALLS:
                    offending.append((path.name, alias.name))
                if alias.asname in FORBIDDEN_CALLS:
                    offending.append((path.name, str(alias.asname)))
    assert not offending, offending


def test_the_package_never_names_another_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_CALLS:
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
        "page",
    }
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr in forbidden
            ):
                offending.append((path.name, node.attr))
    assert not offending, offending


def test_the_package_does_not_import_the_composition_root() -> None:
    """The build seam is injected, so the root's builders stay the root's."""

    source = "\n".join(
        path.read_text(encoding="utf-8") for path in _python_files(_PAPER_DIR)
    )
    for builder in (
        "build_trading_runtime",
        "build_execution_application",
        "build_execution_candidate",
        "build_auto_rotation_config",
        "build_risk_application",
        "resolve_paper_session_capital",
    ):
        assert builder not in source, builder


# -- Guard C: Qt boundary ------------------------------------------------


def test_the_orchestrator_touches_no_widget_and_no_timer() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        names = _called_names(path)
        imported: set[str] = set()
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
        for name in FORBIDDEN_QT_NAMES:
            if name in names or name in imported:
                offending.append((path.name, name))
    assert not offending, offending


def test_the_orchestrator_imports_only_qtcore() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        for module in _imports(path):
            if module.startswith("PySide6") and module != "PySide6.QtCore":
                offending.append((path.name, module))
    assert not offending, offending


def test_the_queries_and_models_modules_are_qt_free() -> None:
    """These two carry rules, facts and wording; neither has business importing Qt."""

    for path in (_QUERIES_PATH, _MODELS_PATH):
        modules = _imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name


@pytest.mark.parametrize("name", ("queries.py", "models.py"))
def test_the_qt_free_modules_load_in_a_fresh_interpreter(name: str) -> None:
    """Loaded in a fresh interpreter with the package's ``__init__`` bypassed.

    Both modules import siblings by package path, so loading the file alone would fail
    on the import rather than telling us anything.  A bare parent package is planted
    first, and ``__init__`` is deliberately *not* executed: it imports the
    orchestrator, which is allowed to import QtCore, so going through the real package
    would make this test pass for the wrong reason.
    """

    snippet = (
        "import importlib.util, sys, types; "
        "pkg = types.ModuleType('us_quant.desktop_v2.orchestration.paper'); "
        f"pkg.__path__ = [r'{_PAPER_DIR}']; "
        "sys.modules['us_quant.desktop_v2.orchestration.paper'] = pkg; "
        f"spec = importlib.util.spec_from_file_location('probe', r'{_PAPER_DIR / name}'); "
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
    assert completed.returncode == 0, (
        f"{name} must load without Qt\n{completed.stderr}"
    )


# -- Guard D: no second truth --------------------------------------------


def test_the_orchestrator_stores_no_second_truth() -> None:
    """No phase, plan mirror, lease flag, runtime, verdict or broker handle.

    Checked as *assignments* rather than as text: the module docstring legitimately
    names ``_active_plan`` while explaining that it is deliberately not stored, so a
    substring search would fire on its own explanation.
    """

    stored = _stored_self_attrs(_ORCHESTRATOR_PATH)
    banned = {
        "_active_plan",
        "_plan",
        "_phase",
        "_paper_phase",
        "_lease",
        "_holds_lease",
        "_result",
        "_workflow_result",
        "_runtime",
        "_trading_runtime",
        "_candidate",
        "_candidate_service",
        "_candidate_id",
        "_broker_state",
        "_active_service",
        "_order_service",
        "_positions",
        "_pending_orders",
        "_risk",
        "_risk_verdict",
        "_intent",
        "_order_intent",
        "_snapshot",
        "_auto_quant_snapshot",
    }
    assert not (stored & banned), sorted(stored & banned)


def test_the_orchestrator_stores_only_its_injected_collaborators() -> None:
    """What it *does* store is the injected providers plus its own attempt counter."""

    stored = _stored_self_attrs(_ORCHESTRATOR_PATH)
    # The attempt sequence is desktop orchestration bookkeeping, not session truth:
    # it only gives each attempt a distinct identity.
    assert "_next_attempt" in stored
    assert stored <= {
        "_workflow_getter",
        "_paper_trading_getter",
        "_build_session",
        "_submit_task",
        "_health_evaluator",
        "_preflight_provider",
        "_strategy_provider",
        "_candidates_provider",
        "_capital_limit_provider",
        "_order_channel_provider",
        "_shadow_is_active",
        "_clear_arm_confirmation",
        "_render_launch_state",
        "_render_launch_context",
        "_next_attempt",
    }, sorted(stored)


def test_the_duplicate_gate_reads_the_workflow_phase() -> None:
    """The canonical answer, rather than a mirrored plan."""

    start = _function_source(_ORCHESTRATOR_PATH, "start")
    assert "queries.launch_attempt_in_flight(self._workflow.phase)" in start
    # The gate must come first: a second start may read no fact before it returns.
    assert start.index("launch_attempt_in_flight") < start.index(
        "self._preflight_provider()"
    )
    assert start.index("launch_attempt_in_flight") < start.index(
        "self._shadow_is_active()"
    )


def test_the_duplicate_refusal_is_the_operator_sentence() -> None:
    start = _function_source(_ORCHESTRATOR_PATH, "start")
    assert "DUPLICATE_TITLE, DUPLICATE_MESSAGE" in start


def test_the_orchestrator_does_not_re_implement_the_plan_fingerprint() -> None:
    """``us_quant.auto_launch`` owns the plan; the queries module delegates to it."""

    source = _QUERIES_PATH.read_text(encoding="utf-8")
    assert "build_auto_launch_plan(" in source
    assert "auto_launch_plan_matches(" in source


# -- Guard E: the candidate borrow ---------------------------------------


def test_the_candidate_borrow_has_exactly_one_call_site() -> None:
    """``candidate_service(`` is a borrowed reference with one legal caller."""

    calls: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_ORCHESTRATOR_PATH)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "candidate_service"
        ):
            calls.append(("candidate_service", node.lineno))
    assert len(calls) == 1, calls


def test_the_borrowed_candidate_is_never_stored() -> None:
    """It goes into a local, and never reaches an attribute.

    Checked as *stored attributes* rather than as a substring: ``_reject_without_candidate``
    is a method *name* that legitimately contains the word, so matching on the word
    would fire on the method itself rather than on a stored handle.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    for line in source.splitlines():
        if "candidate_service(" in line:
            assert line.lstrip().startswith("service = "), line

    stored = _stored_self_attrs(_ORCHESTRATOR_PATH)
    handles = {
        name
        for name in stored
        if "candidate" in name or "service" in name or "broker" in name
    }
    # The one legitimate entry is the *provider* for the operator's shortlist, which
    # is an injected callable onto a fact -- not a broker handle.
    assert handles <= {"_candidates_provider"}, sorted(handles)


def test_no_candidate_handle_escapes_the_callback_stack() -> None:
    """Nothing stores the service, and no signal carries it."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "self._candidate_service" not in source
    assert "self._order_service" not in source
    assert "self._active_service" not in source


# -- Guard F: no broker bypass -------------------------------------------


def test_the_orchestrator_never_talks_to_the_broker_directly() -> None:
    """Submission is the execution application's, reached through the runtime."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_PAPER_DIR):
        names = _called_names(path)
        for forbidden in FORBIDDEN_BROKER_CALLS:
            if forbidden in names:
                offending.append((path.name, forbidden))
    assert not offending, offending


def test_the_orchestrator_never_computes_a_risk_verdict_or_an_intent() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in _python_files(_PAPER_DIR)
    )
    for forbidden in (
        "OrderIntent(",
        "risk_verdict",
        "evaluate_risk",
        "TradeProposal(",
    ):
        assert forbidden not in source, forbidden


# -- Guard G: the public surface is exactly the declared one -------------


def test_the_orchestrator_exposes_exactly_the_declared_surface() -> None:
    public = _public_members(_ORCHESTRATOR_PATH, "PaperOrchestrator")
    assert public == set(PUBLIC_SURFACE), sorted(public ^ set(PUBLIC_SURFACE))


def test_the_orchestrator_exposes_the_declared_signals() -> None:
    signals = _signal_members(_ORCHESTRATOR_PATH, "PaperOrchestrator")
    assert signals == set(PUBLIC_SIGNALS), sorted(
        signals ^ set(PUBLIC_SIGNALS)
    )


def test_the_package_exports_only_the_capability() -> None:
    """``__init__`` is a stable entry point, not a re-export swamp."""

    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - a tiny, self-contained source read
        (_PAPER_DIR / "__init__.py").read_text(encoding="utf-8"),
        namespace,
    )
    assert sorted(namespace["__all__"]) == ["PaperOrchestrator"]


def test_no_new_desktop_manager_or_context_was_introduced() -> None:
    for banned in (
        "DesktopManager",
        "ApplicationContext",
        "PaperManager",
        "PaperCoordinator",
        "ServiceRegistry",
        "DependencyContainer",
    ):
        for path in _python_files(_PAPER_DIR):
            assert banned not in path.read_text(encoding="utf-8"), (
                f"{banned} in {path.name}"
            )


# -- the arm/publish/promote order is a hard constraint ------------------


def test_the_irreversible_order_is_locked() -> None:
    """``ensure_candidate_can_promote`` < ``publish_armed`` < ``promote_candidate``.

    A broker connect proves reachability, not permission: promotions that happened
    before publication would leave the session owned by the window while the workflow
    still believes it is only connecting.  The ensure step must come first so the
    published session can never be left without an owner for an already-knowable
    reason.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    check = source.index("self._paper_trading.ensure_candidate_can_promote(")
    publish = source.index("self._workflow.publish_armed(")
    promote = source.index("self._paper_trading.promote_candidate(")
    assert check < publish < promote


def test_the_arm_happens_before_the_ensure_check() -> None:
    """``arm`` precedes the promotability check, and both precede publication.

    The build seam only composes and starts the runtime, so ``arm`` is this module's
    own call and the full ordering is assertable here rather than approximated by
    "the build call came first".
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    armed = source.index("service.arm(")
    check = source.index("self._paper_trading.ensure_candidate_can_promote(")
    publish = source.index("self._workflow.publish_armed(")
    assert armed < check < publish


def test_the_broker_gate_precedes_the_build() -> None:
    """Validation is decided before a runtime is constructed."""

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    gate = source.index("queries.validate_broker_state(")
    built = source.index("self._build_session(")
    assert gate < built


def _called_and_attributed(path: pathlib.Path, name: str) -> set[str]:
    """The names one method *uses*, ignoring its docstring prose.

    Needed because the post-publication handler's docstring legitimately *names* the
    calls it must not make (``reject_connecting`` is a no-op there), so a substring
    search would fire on the explanation rather than on a call.
    """

    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ClassDef) or node.name != "PaperOrchestrator":
            continue
        for member in node.body:
            if (
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name == name
            ):
                body = member.body[1:] if ast.get_docstring(member) else member.body
                found: set[str] = set()
                for stmt in body:
                    for inner in ast.walk(stmt):
                        if isinstance(inner, ast.Call):
                            found.add(
                                getattr(inner.func, "id", None)
                                or getattr(inner.func, "attr", None)
                                or ""
                            )
                        elif isinstance(inner, ast.Attribute):
                            found.add(inner.attr)
                return found
    raise AssertionError(f"PaperOrchestrator.{name} not found")


def test_the_publish_failure_path_cannot_touch_the_active_service() -> None:
    """Publication splits the failure handling, and only the pre-publication side rolls back.

    ``publish_armed`` raises *before* the workflow reaches ``RUNNING``, so promotion
    must sit **outside** the rollback ``try``: it is the only step that can fail once
    the session is published, and the rollback handler must not be reachable from it.
    Asserted against the methods' actual *calls* rather than their source text, because
    the post-publication handler's docstring names the calls it must not make.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    rollback_handler = source.index("except Exception")
    promote = source.index("self._paper_trading.promote_candidate(")
    # Promotion comes *after* the rollback handler, i.e. in its own block.
    assert rollback_handler < promote
    assert "self._discard_candidate(" in source

    # The rollback path never clears or disconnects the active service.
    rollback = _called_and_attributed(_ORCHESTRATOR_PATH, "_discard_candidate")
    assert "clear_active" not in rollback, rollback
    assert "disconnect" not in rollback, rollback
    assert "reject_connecting" in rollback

    # And the post-publication path rolls nothing back.
    after = _called_and_attributed(_ORCHESTRATOR_PATH, "_fail_after_publication")
    assert "discard_candidate" not in after, after
    assert "reject_connecting" not in after, after
    assert "clear_active" not in after, after
    assert "disconnect" not in after, after


def test_the_arm_call_belongs_to_the_orchestrator() -> None:
    """``arm`` is an explicit orchestrator step, not a hidden step in the build seam.

    The round is "launch / arm orchestration", so the ordering constraint has to be
    visible in this module for a guard to lock it.  When arming lived inside the
    injected seam, only ``build < ensure < publish`` could be asserted.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    armed = source.index("service.arm(")
    built = source.index("self._build_session(")
    check = source.index("self._paper_trading.ensure_candidate_can_promote(")
    publish = source.index("self._workflow.publish_armed(")
    assert built < armed < check < publish
    # The seam returns the sizing rather than arming itself.
    assert "max_order_notional=built.max_order_notional" in source


# -- the stale path ------------------------------------------------------


def test_the_stale_check_is_against_the_workflow_plan() -> None:
    """Staleness is the workflow's answer, not a mirrored plan comparison."""

    source = _function_source(_ORCHESTRATOR_PATH, "_connect_finished")
    assert "self._workflow.active_plan != request.plan" in source


def test_the_second_preflight_and_identity_revalidation_are_both_kept() -> None:
    """The first pass is not evidence about the present."""

    source = _function_source(_ORCHESTRATOR_PATH, "_connect_finished")
    assert "queries.preflight_failed(self._preflight_provider())" in source
    assert "queries.current_inputs_match(" in source


def test_the_rejection_runs_in_a_finally() -> None:
    """A failed disconnect must never strand ``CONNECTING`` holding the lease."""

    source = _function_source(_ORCHESTRATOR_PATH, "_discard_candidate")
    assert source.index("finally:") < source.index(
        "self._workflow.reject_connecting(request.plan)"
    )
    # And it never forces the service's own candidate map.
    assert "_candidates" not in source


def test_the_malformed_callback_is_raised_not_swallowed() -> None:
    """``except Exception: return`` is how a launch silently stops."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "raise TypeError(BAD_CALLBACK_MESSAGE)" in source


def test_the_lease_is_never_touched_directly() -> None:
    """The capability drives the workflow; it never acquires or releases PAPER."""

    source = "\n".join(
        path.read_text(encoding="utf-8") for path in _python_files(_PAPER_DIR)
    )
    for forbidden in (
        "acquire_paper",
        "release_paper",
        "_leases",
        "ExecutionLease",
    ):
        assert forbidden not in source, forbidden
