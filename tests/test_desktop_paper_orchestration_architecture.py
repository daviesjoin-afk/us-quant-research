"""Architecture guards for the v2O Paper orchestration extraction.

Structural, not behavioural: they read the source tree and assert that the ownership
these rounds moved really moved, and that the new capability cannot reach back into the
window, sideways into another capability, or down into the broker.

Seven guards matter most, and each fails for the right reason:

* **Guard A -- the window left the launch *and* the active session.**  ``_start_auto_quant``
  and ``_auto_order_service_connected`` are gone, along with the retired plan mirror and
  the three helpers that only they called; since v2O-E2 so are ``_poll_auto_quant_orders``,
  ``_pause_auto_quant_entries``, ``_resume_auto_quant_entries``, ``_stop_auto_quant`` and
  the ``trading_runtime`` handle.  No shim and no forwarding property, which is why the
  check is for *declared* names rather than merely called ones;
* **Guard B -- the dependency boundary.**  ``PaperOrchestrator`` imports no other
  capability orchestrator, no IBKR adapter, no desktop composition module and no
  widget.  ``QtCore`` is allowed, because every sibling orchestrator exposes signals
  that way;
* **Guard C -- no second truth.**  The capability stores no phase, no plan mirror, no
  runtime, no risk verdict, no order intent, no candidate handle and no broker
  connection state.  What it may keep is bookkeeping about its own calls -- the attempt
  number and the last-ingress stamp -- which is why those are asserted *present* in the
  same breath as the ban;
* **Guard D -- no broker bypass.**  No ``placeOrder`` / ``cancelOrder`` /
  ``reqGlobalCancel`` / ``submit`` / ``cancel`` appears anywhere in it: submission is
  the execution application's, reached only through the runtime;
* **Guard E -- the candidate borrow.**  ``candidate_service(`` has exactly one call
  site, stores into a local, and never reaches an attribute;
* **Guard F -- the exact public surface.**  The declared methods and signals, asserted
  in both directions so a convenience method fails here;
* **Guard G -- the E2 sequencing ownership.**  The intent wiring and the market fan-out
  reach the capability directly, and the window neither checks a Paper phase nor reaches
  the workflow on its behalf.

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
import re
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
_SERVICE_PATH = _SRC / "trading" / "application" / "paper" / "service.py"

#: The launch state and helpers the extraction deleted rather than shimmed.
RETIRED_WINDOW_LAUNCH_STATE = (
    "_active_auto_launch_plan",
    "_next_auto_launch_attempt",
)

#: The window state v2O-E2 deleted: the runtime handle, the health cache and the
#: ingress stamp.  Declared as an exact set so a re-added one -- or a forwarding
#: property under the same name -- fails here rather than quietly restoring the old
#: ownership.
RETIRED_WINDOW_ACTIVE_STATE = (
    "trading_runtime",
    "paper_execution_health",
    "_last_stream_ingress_monotonic",
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

#: The active-session methods v2O-E2 deleted.  Every one of them decided *when* a
#: live session polls, consumes the market, pauses, resumes or stops, which is the
#: sequencing this round moved.  ``_stop_auto_quant``'s only surviving caller is
#: ``closeEvent``, whose shutdown decision is E4's -- and it now calls the capability.
RETIRED_WINDOW_ACTIVE_METHODS = (
    "_poll_auto_quant_orders",
    "_pause_auto_quant_entries",
    "_resume_auto_quant_entries",
    "_stop_auto_quant",
)

#: The recovery and finalization sequencing v2O-E3 moved.  Every one of these decided
#: *when* a halt was reconciled, whether a proof was current, whether a zero-state proof
#: should start, or whether ownership could be given up -- and each of those is a
#: decision about a Paper session, which only one owner may make.
RETIRED_WINDOW_E3_METHODS = (
    "_reconnect_auto_order_service",
    "_auto_order_service_reconnected",
    "_resume_auto_quant_from_reconciliation",
    "_auto_order_resume_failed",
    "_auto_order_reconciliation_failed",
    "_schedule_paper_finalization_refresh",
    "_start_paper_finalization_refresh",
    "_paper_finalization_completed",
    "_paper_finalization_failed",
    "_finish_auto_quant_session_if_safe",
    "_handle_paper_e3_result_bridge",
    "_publish_window_paper_result",
)

#: The window state v2O-E3 deleted.  ``_paper_finalization_inflight`` was the temporary
#: E2 seam -- the proof was the window's and active ingress had to read the flag across
#: the boundary -- and ``_last_paper_finalization_started`` was its backoff stamp.  Both
#: are the orchestrator's own task bookkeeping now, so neither may reappear here, as an
#: attribute or as a forwarding property.
RETIRED_WINDOW_E3_STATE = (
    "_paper_finalization_inflight",
    "_last_paper_finalization_started",
)

#: The workflow calls the window may no longer make on the recovery or finalization
#: paths' behalf.  Each is a *decision* about a session rather than a presentation step,
#: and v2O-E3 moved all of them: the halt protocol, the proof of broker zero-state, and
#: the single gate that may release PAPER.
RETIRED_WINDOW_E3_CALLS = (
    "paper_workflow.begin_manual_reconciliation",
    "paper_workflow.complete_manual_reconciliation",
    "paper_workflow.fail_manual_reconciliation",
    "paper_workflow.confirm_manual_resume",
    "paper_workflow.capture_finalization_evidence",
    "paper_workflow.confirm_finalization_after_disconnect",
    "paper_workflow.fail_finalization_refresh",
    "paper_workflow.finalize_if_safe",
    "paper_trading.connect_active",
    "paper_trading.disconnect",
    "paper_trading.clear_active",
)

#: The capability's own sequencing calls the window may no longer drive.  Checked as
#: `self.paper_orchestrator.<name>(` so a re-added handler that merely asks the
#: capability to do the work is still caught: the decision would have moved back, not
#: the code.  ``confirm_reconciliation_resume`` is deliberately absent -- the operator's
#: confirmation is presentation, so the window is the one that has to call it.
RETIRED_WINDOW_E3_ORCHESTRATOR_CALLS = (
    "self.paper_orchestrator.reconcile(",
)

#: The workflow calls the window may no longer make on the active session's behalf.
RETIRED_WINDOW_ACTIVE_CALLS = (
    "paper_workflow.on_stream",
    "paper_workflow.poll",
    "paper_workflow.set_entries_paused",
    "paper_workflow.request_stop",
)

#: The launch method that legitimately stays: the operator confirmation is
#: presentation, and the capability may not import ``QMessageBox``.
RETAINED_WINDOW_LAUNCH_METHODS = (
    "_confirm_and_start_auto_quant",
    "_auto_quant_order_channel",
    "_build_paper_session",
    "_report_paper_launch_refusal",
    "_record_paper_runtime_event",
)

#: What legitimately stays on the window after v2O-E3: the one result render path, the
#: two operator confirmations (they are dialogs, and the capability may not import one),
#: the three presentation handlers for the capability's payload-free publications, and
#: the Shadow gate that asks the capability instead of holding a runtime handle.
RETAINED_WINDOW_E2_METHODS = (
    "_on_paper_result_changed",
    "_confirm_and_start_auto_quant",
    "_confirm_paper_reconciliation_resume",
    "_on_paper_manual_recovery_required",
    "_on_paper_session_finalized",
    "_render_auto_quant_snapshot",
    "_paper_runtime_is_active",
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
    # ``Enum`` for the shutdown disposition.  A value type, not a behaviour: it exists
    # so "what should this close do?" has one answerable spelling.
    "enum",
    # ``monotonic``, the default clock for the poll-suppression window.  Stdlib, and
    # the only reading this layer takes of the outside world that is not a result.
    "time",
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

#: The orchestrator's public surface, asserted exactly in both directions.  Methods
#: and properties are both *intents or delegated questions*: the five operations a
#: session's run is driven by, the three recovery/finalization operations v2O-E3 added,
#: and the three questions other capabilities ask about it.
PUBLIC_SURFACE = (
    "start",
    "on_market_snapshot",
    "poll",
    "pause",
    "resume",
    "stop",
    "reconcile",
    "confirm_reconciliation_resume",
    "prepare_shutdown",
    "result",
    "runtime_active",
    "has_runtime_obligations",
)

#: The Qt signals the window relies on.  ``result_changed`` carries the workflow's own
#: ``PaperSessionResult`` -- one emission per operation, launch included --
#: ``runtime_event_requested`` and ``log_requested`` are the same request-not-own
#: pattern every sibling capability uses, and ``refused`` is the launch's dialog.
#:
#: v2O-E3 added three payload-free ones.  ``presentation_refresh_requested`` covers the
#: transitions that produce no result at all; ``session_finalized`` and
#: ``manual_recovery_required`` are facts the window used to derive from the Paper phase
#: itself.  None of them carries a phase, a boolean or a state dict, because each of
#: those would be the second truth this round removed.
PUBLIC_SIGNALS = (
    "refused",
    "log_requested",
    "result_changed",
    "runtime_event_requested",
    "presentation_refresh_requested",
    "session_finalized",
    "manual_recovery_required",
)

#: Every ``self.paper_orchestrator.<name>`` the window may reach for.
WINDOW_ALLOWED_ORCHESTRATOR_MEMBERS = set(PUBLIC_SURFACE) | set(PUBLIC_SIGNALS)

#: The page intent wiring: the start signal reaches the window's confirmation gate,
#: which forwards to the capability.  The gate is presentation; the orchestration is
#: the capability's.
START_INTENT_WIRING = (
    ("start_requested", "self._confirm_and_start_auto_quant"),
)

#: The session intents, which reach the capability with no window handler in
#: between: there is nothing for presentation to add, and a hop through the window is
#: how a second owner starts.  ``reconcile`` joined them in v2O-E3 for the same reason:
#: whether a halt may be reconciled, and what the broker reading means, is not a
#: presentation question.
SESSION_INTENT_WIRING = (
    ("pause_requested", "self.paper_orchestrator.pause"),
    ("resume_requested", "self.paper_orchestrator.resume"),
    ("stop_requested", "self.paper_orchestrator.stop"),
    ("reconcile_requested", "self.paper_orchestrator.reconcile"),
)

#: The one recovery intent that *does* pass through the window: the operator's
#: confirmation.  It is a ``QMessageBox`` and the capability may not import one, so the
#: hop is the point -- the window asks, the capability decides.
RESUME_CONFIRMATION_WIRING = (
    "resume_reconciliation_requested",
    "self._confirm_paper_reconciliation_resume",
)

#: The three payload-free publications v2O-E3 added, and the presentation handler each
#: reaches.  Each handler repaints from canonical truth; none of them decides anything.
E3_SIGNAL_WIRING = (
    ("presentation_refresh_requested", "self._apply_paper_workflow_button_state"),
    ("manual_recovery_required", "self._on_paper_manual_recovery_required"),
    ("session_finalized", "self._on_paper_session_finalized"),
)

#: The watchdog heartbeat enters the capability directly, for the same reason.
HEARTBEAT_WIRING = ("timeout", "self.paper_orchestrator.poll")


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

    return _method_source(path, name, class_name="PaperOrchestrator")


def _method_source(
    path: pathlib.Path, name: str, *, class_name: str = "MainWindow"
) -> str:
    """The source of one method of one class, for scope-scoped assertions.

    Scoped to the class rather than searched as text, because these names appear in
    several places in a 4,500-line window: "which method reads this?" has to be
    answerable, and a window-wide substring search cannot answer it.
    """

    source = path.read_text(encoding="utf-8")
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if (
                    isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == name
                ):
                    return ast.get_source_segment(source, member) or ""
    raise AssertionError(f"{class_name}.{name} not found")


def _dense(path: pathlib.Path) -> str:
    """The file's source with every whitespace run collapsed, for wiring checks.

    A wiring assertion is about which signal reaches which target.  Matching the
    formatted call would also assert how long the target's name is -- and fail on a
    rename that changed nothing about the wiring.
    """

    return re.sub(r"\s+", "", path.read_text(encoding="utf-8"))


def _code_only(path: pathlib.Path, name: str, *, class_name: str) -> str:
    """One method's statements, with its docstring removed.

    Needed where an assertion is about *what a branch does* rather than about the words
    around it: ``prepare_shutdown``'s docstring names every disposition while explaining
    the order it considers them in, which is not the order the branches are written in.
    """

    source = path.read_text(encoding="utf-8")
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if member.name != name:
                continue
            body = list(member.body)
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{class_name}.{name} not found")


def _raises(path: pathlib.Path, name: str) -> bool:
    """Whether one function contains a ``raise`` *statement*.

    Structural rather than textual, because these methods' docstrings routinely
    explain the very thing they must not do -- "it does not raise" would match a
    substring search on the explanation.
    """

    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return any(isinstance(inner, ast.Raise) for inner in ast.walk(node))
    raise AssertionError(f"{name} not found in {path.name}")


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


# -- Guard A2: the window left the active session ------------------------
#
# v2O-E2's half of the same property Guard A pins for the launch.  The window used to
# decide when a live session consumed the market, when the watchdog polled, and what
# pause/resume/stop meant; it kept the runtime handle those decisions were read off.
# All of that is the capability's, so each retired name is asserted absent *and* each
# surviving seam is asserted present -- a guard that only checked the deletions would
# pass on a window that had dropped the wiring entirely.


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_ACTIVE_STATE)
def test_the_window_keeps_no_active_paper_state(attribute: str) -> None:
    """The runtime handle, the health cache and the ingress stamp are gone.

    Checked as assignments *and* as declared names, so neither an attribute nor a
    property under a retired name can restore the old ownership.
    """

    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute
    assert attribute not in _declared_names(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("name", RETIRED_WINDOW_ACTIVE_METHODS)
def test_the_retired_active_methods_are_gone(name: str) -> None:
    assert name not in _declared_names(_DESKTOP_PATH), name


@pytest.mark.parametrize("call", RETIRED_WINDOW_ACTIVE_CALLS)
def test_the_window_never_drives_the_active_session(call: str) -> None:
    """No window path may reach the workflow for the session's own run."""

    assert call not in _DESKTOP_PATH.read_text(encoding="utf-8"), call


@pytest.mark.parametrize("name", RETAINED_WINDOW_E2_METHODS)
def test_the_e2_composition_and_bridge_seams_remain(name: str) -> None:
    """What legitimately stays: one render path, and the E3 bridge inside it.

    Asserted so the guard cannot pass because the window dropped the result handling
    altogether -- something still has to render a result, and E3's two remaining
    decisions still have to run somewhere until E3 moves them.
    """

    assert name in _declared_names(_DESKTOP_PATH), name


@pytest.mark.parametrize("signal,target", SESSION_INTENT_WIRING)
def test_the_session_intents_reach_the_capability(signal: str, target: str) -> None:
    """Pause, resume and stop are wired straight to the capability.

    Whitespace is stripped before matching so the assertion is about the wiring rather
    than about how black decided to wrap the call.
    """

    assert f"{signal}.connect({target})" in _dense(_DESKTOP_PATH), signal


def test_the_watchdog_heartbeat_reaches_the_capability() -> None:
    """The timer drives the capability, not a window handler."""

    assert (
        f"{HEARTBEAT_WIRING[0]}.connect({HEARTBEAT_WIRING[1]})"
        in _dense(_DESKTOP_PATH)
    )


def test_the_market_fanout_only_hands_the_paper_fact_over() -> None:
    """The fan-out stops at the capability.

    It may not check the Paper phase, stamp an ingress, consult the finalization seam
    or reach the workflow: each of those is a decision about a live Paper session, and
    the window is no longer a party to any of them.  A second answer to "does this
    session want the market right now?" is exactly how a halted session gets re-entered.
    """

    fanout = _method_source(_DESKTOP_PATH, "_on_market_snapshot_changed")
    assert "self.paper_orchestrator.on_market_snapshot(snapshot)" in fanout
    for forbidden in (
        "paper_workflow",
        "PaperWorkflowPhase",
        "_paper_finalization_inflight",
        "monotonic",
    ):
        assert forbidden not in fanout, forbidden


def test_the_result_handler_is_presentation_only() -> None:
    """The one result handler renders, and reaches nothing it could drive.

    Asserted on the method's actual *calls* rather than its text, because its docstring
    deliberately names the calls it must not make -- a substring search would fire on
    the explanation instead of on a call.

    v2O-E3 removed the bridge this guard used to require, so the assertion is now the
    stronger one in both directions: the handler still has to do its rendering work, and
    it must no longer call *any* method that decides something about the session.
    """

    used = _called_and_attributed(
        _DESKTOP_PATH, "_on_paper_result_changed", class_name="MainWindow"
    )
    for forbidden in (
        "on_stream",
        "poll",
        "set_entries_paused",
        "request_stop",
        "connect_candidate",
        "disconnect",
        "clear_active",
        "finalize_if_safe",
        "placeOrder",
    ):
        assert forbidden not in used, (forbidden, sorted(used))
    # And it does the work it exists for, so it cannot pass by doing nothing.
    assert "_render_auto_quant_snapshot" in used, sorted(used)
    assert "_apply_paper_workflow_button_state" in used, sorted(used)


def test_the_one_result_path_is_the_only_emitter() -> None:
    """Every operation publishes through ``_publish_result``; no operation emits itself.

    This is the property that makes "how is a Paper result published?" answerable in one
    place.  An operation that emitted ``result_changed`` directly would work, and would
    silently skip the event requests that live beside it.
    """

    for method in (
        "start",
        "on_market_snapshot",
        "poll",
        "pause",
        "resume",
        "stop",
        "reconcile",
        "confirm_reconciliation_resume",
        "prepare_shutdown",
        "_arm_and_publish",
        "_reconciliation_finished",
        "_finalization_completed",
    ):
        source = _function_source(_ORCHESTRATOR_PATH, method)
        assert "result_changed.emit(" not in source, method

    publish = _function_source(_ORCHESTRATOR_PATH, "_publish_result")
    assert "self.result_changed.emit(result)" in publish
    assert "for event in result.events:" in publish
    assert "PaperRuntimeEventRequest(" in publish
    # And the consequences of a result are decided in exactly one place.
    assert "self._after_result(result)" in publish


@pytest.mark.parametrize(
    "method",
    (
        "on_market_snapshot",
        "poll",
        "pause",
        "resume",
        "stop",
        "_arm_and_publish",
        "_reconciliation_finished",
        "_finalization_completed",
    ),
)
def test_every_operation_publishes_through_the_one_path(method: str) -> None:
    source = _function_source(_ORCHESTRATOR_PATH, method)
    assert "self._publish_result(" in source, method


@pytest.mark.parametrize(
    "caller,attribute",
    (
        ("reconcile", "on_success"),
        ("confirm_reconciliation_resume", "on_success"),
        ("_start_finalization", "on_success"),
    ),
)
def test_every_task_success_handler_routes_to_the_one_path(
    caller: str, attribute: str
) -> None:
    """A task that produces a result publishes it through the one path.

    The two paths that cannot call it *inline* are the asynchronous ones: their result
    arrives on a worker, so the routing is what has to be asserted.  ``reconcile`` and
    ``_start_finalization`` name a wrapper, ``confirm_reconciliation_resume`` names the
    path itself -- and each wrapper is checked to end there, so a handler that quietly
    emitted ``result_changed`` itself would fail here.
    """

    source = _function_source(_ORCHESTRATOR_PATH, caller)
    line = next(
        row for row in source.splitlines() if f"{attribute}=" in row and "self._" in row
    )
    target = line.split(f"{attribute}=")[1].strip().rstrip(",")
    if target == "self._publish_result":
        return
    handler = target.removeprefix("self.")
    assert "_publish_result" in _function_source(_ORCHESTRATOR_PATH, handler), target


def test_the_finalization_seam_is_gone_and_the_flags_are_the_capabilitys() -> None:
    """v2O-E3 deleted the E2 provider: the proof, and its flag, are both here now.

    The E2 guard's failure mode was a *copied* flag -- a second owner of a lifecycle
    fact.  The inverse failure mode is just as bad and is what this pins: the window
    keeping the flag and reading it back across the boundary, which would keep the
    owner of a Paper lifecycle decision outside the capability.
    """

    stored = _stored_self_attrs(_ORCHESTRATOR_PATH)
    assert "_finalization_inflight_provider" not in stored
    assert "_finalization_inflight" in stored
    assert "_last_finalization_started" in stored
    # And the window keeps neither flag, nor a provider standing in for one.
    assert "finalization_inflight_provider" not in _DESKTOP_PATH.read_text(
        encoding="utf-8"
    )


def test_the_market_fact_arrives_as_a_provider() -> None:
    """Paper does not import, hold or name Market -- not even its orchestrator."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "market_orchestrator" not in source
    assert "market_snapshot_provider" in source
    # Read at call time, so a stop cannot be judged against a stale quote.
    stop = _function_source(_ORCHESTRATOR_PATH, "stop")
    assert "self._market_snapshot_provider()" in stop


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
        # v2O-E3's four.  The two evidence records are the ones a "helpful" migration is
        # most likely to copy -- saving a proof on the way past looks harmless and is
        # exactly how a consumed proof becomes resumable twice -- and the session id and
        # evidence id are its companions.
        "_reconciliation_evidence",
        "_finalization_evidence",
        "_evidence_id",
        "_session_id",
    }
    assert not (stored & banned), sorted(stored & banned)


def test_the_orchestrator_stores_only_its_injected_collaborators() -> None:
    """What it *does* store is the injected providers plus its own bookkeeping.

    Four entries are not providers and are named deliberately: the attempt sequence,
    which gives each attempt a distinct identity; the last-ingress stamp, which only
    decides whether the next poll would repeat work this object just did; and v2O-E3's
    two zero-state-proof flags, which decide whether *this object* should start another
    proof.  All four are about this object's own calls; none is a fact about the session.
    """

    stored = _stored_self_attrs(_ORCHESTRATOR_PATH)
    assert "_next_attempt" in stored
    assert "_last_stream_ingress_monotonic" in stored
    assert "_finalization_inflight" in stored
    assert "_last_finalization_started" in stored
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
        "_market_snapshot_provider",
        "_reconciliation_rows_provider",
        "_clear_arm_confirmation",
        "_render_launch_state",
        "_render_launch_context",
        "_clock",
        "_next_attempt",
        "_last_stream_ingress_monotonic",
        "_finalization_inflight",
        "_last_finalization_started",
    }, sorted(stored)


def test_the_ingress_stamp_is_bookkeeping_and_not_a_session_fact() -> None:
    """The one mutable field allowed in is touched by exactly three methods.

    A stamp that other code read as "is the session live?" would be a second truth in
    disguise, so the guard pins its readers by name: the field is initialised, written
    by the ingress, and read by the poll's suppression window -- and by nothing else.
    """

    touching = [
        name
        for name in (
            "__init__",
            "start",
            "on_market_snapshot",
            "poll",
            "pause",
            "resume",
            "stop",
        )
        if "_last_stream_ingress_monotonic" in _function_source(_ORCHESTRATOR_PATH, name)
    ]
    assert touching == ["__init__", "on_market_snapshot", "poll"], touching


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


# -- the arm/reserve/publish/commit order is a hard constraint -----------
#
# The promotion is *taken* before publication rather than checked before it.  The
# difference is what these guards now pin: with a pure check the slot was still empty
# while a session that expects an owner came into being, so a promotion refused after
# ``publish_armed`` stranded a running workflow whose order port -- already the
# coordinator's -- belonged to nobody, and every recovery path (all of which start at
# ``has_order_service``) returned early.


def test_the_irreversible_order_is_locked() -> None:
    """``reserve_candidate_promotion`` < ``publish_armed`` < ``commit_candidate_promotion``.

    A broker connect proves reachability, not permission: a promotion that happened
    with nothing published would leave the session owned while the workflow still
    believed it was only connecting.  Conversely the reservation *must* precede
    publication, because publication is the step that creates something needing an
    owner -- that ordering is the safety property, not a preference.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    reserve = source.index("self._paper_trading.reserve_candidate_promotion(")
    publish = source.index("self._workflow.publish_armed(")
    commit = source.index("self._paper_trading.commit_candidate_promotion(")
    assert reserve < publish < commit


def test_the_arm_happens_before_the_reservation() -> None:
    """``arm`` precedes the reservation, and both precede publication.

    The build seam only composes and starts the runtime, so ``arm`` is this module's
    own call and the full ordering is assertable here rather than approximated by
    "the build call came first".
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    armed = source.index("service.arm(")
    reserve = source.index("self._paper_trading.reserve_candidate_promotion(")
    publish = source.index("self._workflow.publish_armed(")
    assert armed < reserve < publish


def test_the_broker_gate_precedes_the_build() -> None:
    """Validation is decided before a runtime is constructed."""

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    gate = source.index("queries.validate_broker_state(")
    built = source.index("self._build_session(")
    assert gate < built


def _called_and_attributed(
    path: pathlib.Path, name: str, *, class_name: str = "PaperOrchestrator"
) -> set[str]:
    """The names one method *uses*, ignoring its docstring prose.

    Needed because the post-publication handler's docstring legitimately *names* the
    calls it must not make (``reject_connecting`` is a no-op there), so a substring
    search would fire on the explanation rather than on a call.
    """

    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
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
    raise AssertionError(f"{class_name}.{name} not found")


def test_the_publish_failure_path_cannot_touch_the_active_service() -> None:
    """Publication splits the failure handling, and only the pre-publication side rolls back.

    ``publish_armed`` raises *before* the workflow reaches ``RUNNING``, so ending the
    claim must sit **outside** the rollback ``try``: it is the only step left once the
    session is published, and the rollback handler must not be reachable from it.
    Asserted against the methods' actual *calls* rather than their source text, because
    the post-publication handler's docstring names the calls it must not make.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    rollback_handler = source.index("except Exception")
    commit = source.index("self._paper_trading.commit_candidate_promotion(")
    # The commit comes *after* the rollback handler, i.e. in its own block.
    assert rollback_handler < commit
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


def test_the_rollback_gives_the_reservation_back_before_disposing() -> None:
    """Cancel first, dispose second -- and the cancel must be unable to raise.

    Reserving *installs* the service, so until the rollback cancels, the candidate is
    the active one: ``discard_candidate`` would find nothing to dispose and the slot
    would stay occupied after a rejected launch.  Ordering is therefore load-bearing,
    and the cancel is the one call in the sequence that must not raise -- an exception
    thrown there would skip the rejection and strand ``CONNECTING`` holding PAPER.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    cancel = source.index("cancel_candidate_promotion(reservation)")
    discard = source.index("self._discard_candidate(")
    assert cancel < discard
    # It is guarded by the reservation actually having been taken...
    assert "if reservation is not None:" in source

    # ...and the service's own cancel reports rather than raises, for that reason.
    # Checked on the method's own syntax tree, not its text: its docstring *explains*
    # the no-raise rule, so a substring search would fire on the explanation.
    assert not _raises(_SERVICE_PATH, "cancel_candidate_promotion")


def test_the_rollback_stops_when_the_promotion_cannot_be_released() -> None:
    """The cancel's answer is a control signal, not a log line.

    ``cancel_candidate_promotion`` reports whether it gave the slot back, and the
    orchestrator has to act on a ``False``.  Completing the rollback on an unproven
    cancel would dispose of a service that may still own the slot, reject the plan and
    release PAPER -- the lease shared with Shadow -- so an armed channel could survive
    while the workflow reports ``READY`` and nothing excludes Shadow.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    assert "if not self._paper_trading.cancel_candidate_promotion(reservation):" in source
    # The gate must precede the disposal, or the completion it forbids has already run.
    gate = source.index("if not self._paper_trading.cancel_candidate_promotion(")
    discard = source.index("self._discard_candidate(")
    assert gate < discard

    # And the handler it routes to unwinds nothing.
    handler = _called_and_attributed(_ORCHESTRATOR_PATH, "_fail_to_release_promotion")
    for forbidden in (
        "discard_candidate",
        "reject_connecting",
        "clear_active",
        "disconnect",
    ):
        assert forbidden not in handler, (forbidden, sorted(handler))


def test_every_other_way_into_the_slot_honours_the_reservation() -> None:
    """A claim is a lock only if *every* other mutation consults it, and consults it first.

    Three things can otherwise reach the active slot while a launch is mid-promotion,
    each with its own failure: ``clear_active`` empties the owner the session about to be
    published relies on; ``connect_candidate`` lets the reserved id be registered again,
    so the rollback cancels *into* the map it had already been replaced in and orphans
    the newer broker connection; and a ``cancel`` that trusts its own map puts the
    reserved service back over whatever is there.

    Asserted on the service module because the property is "the check precedes the
    mutation", which behaviour tests can only sample at the cases someone thought of.
    """

    source = _SERVICE_PATH.read_text(encoding="utf-8")

    def method(name: str) -> str:
        text = source[source.index(f"def {name}(") :]
        return text[: text.index("\n    def ", 1)]

    clears = method("clear_active")
    # ``clear_active`` no longer carries the checks itself: it *is* the reservation,
    # reserved and committed, so the promotion claim is refused by the one place that
    # installs a claim on the slot rather than by a check of its own that a re-open could
    # slip past.
    assert "self.reserve_active_release(expected_service=expected_service)" in clears
    assert "self.commit_active_release(" in clears

    # And the check still precedes the mutation, at its new home.
    release_source = (_SERVICE_PATH.parent / "active_release.py").read_text(
        encoding="utf-8"
    )
    reserve = release_source[release_source.index("def reserve_active_release(") :]
    reserve = reserve[: reserve.index("\n    def ", 1)]
    assert reserve.index("_promotion_reservation") < reserve.index(
        "self._active_release_reservation = reservation"
    )

    connects = method("connect_candidate")
    assert connects.index("_promotion_reservation") < connects.index(
        "self._candidates[key] = None"
    )

    cancels = method("cancel_candidate_promotion")
    assert cancels.index("reservation.candidate_id in self._candidates") < cancels.index(
        "self._order_service = None"
    )


def test_nothing_between_publication_and_the_commit_can_yield_control() -> None:
    """The published-but-unclaimed instant must not contain an observer.

    Ownership is taken before publication, so no ownerless session can exist.  There
    is still an instant in which the phase reads ``RUNNING`` while the launch's claim
    is outstanding, and that instant is safe only because it grants control to nobody:
    the workflow controller is not a ``QObject`` and its phase transitions emit
    nothing, and this module emits nothing in between.  Pinned shut rather than argued
    closed, because "no signal is emitted" is exactly the kind of assumption a later
    edit can invalidate silently.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    publish = source.index("self._workflow.publish_armed(")
    commit = source.index("self._paper_trading.commit_candidate_promotion(")
    assert ".emit(" not in source[publish:commit], source[publish:commit]

    # And the controller cannot re-enter a slot of its own: no Qt at all on that path,
    # including the reconciliation mixin it inherits.
    for path in (
        _SRC / "trading" / "runtime" / "workflow.py",
        _SRC / "trading" / "runtime" / "recovery.py",
    ):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("QObject", "Signal(", ".emit("):
            assert forbidden not in text, (path.name, forbidden)


def test_the_commit_refusal_is_caught_by_name() -> None:
    """A missing commit cannot escape a Qt slot, so it is caught as its own type.

    Catching ``Exception`` here would be a different claim: the point is that this is
    the order-service owner refusing, not an arbitrary failure being absorbed.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    assert "except PaperTradingLifecycleError as error:" in source
    # And the type is imported at runtime from the port's own models module, not from
    # the service implementation, so catching it needs no service import.
    assert (
        "from us_quant.trading.application.paper.models import"
        " PaperTradingLifecycleError"
    ) in _ORCHESTRATOR_PATH.read_text(encoding="utf-8")


def test_the_arm_call_belongs_to_the_orchestrator() -> None:
    """``arm`` is an explicit orchestrator step, not a hidden step in the build seam.

    The round is "launch / arm orchestration", so the ordering constraint has to be
    visible in this module for a guard to lock it.  When arming lived inside the
    injected seam, only ``build < check < publish`` could be asserted.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_arm_and_publish")
    armed = source.index("service.arm(")
    built = source.index("self._build_session(")
    reserve = source.index("self._paper_trading.reserve_candidate_promotion(")
    publish = source.index("self._workflow.publish_armed(")
    assert built < armed < reserve < publish
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
    """The capability may ask whether *Paper's* lease is held, and nothing else.

    Two claims, and the second is the sharper one.  It never acquires or releases PAPER:
    those are the workflow's transitions, and a second writer of the lease is a second
    owner of the Shadow/Paper mutex.

    And the only lease member it may name is ``PAPER`` -- deliberately *not* ``NONE`` and
    not ``SHADOW``.  The manager is *shared* with Shadow, so ``workflow.lease`` answers
    with whichever of the two holds it; asking "is the shared lease free?" (``is not
    NONE``) would read Shadow's ownership as Paper's and make a healthy Shadow session
    block every Paper close.  ``PAPER`` is the one answer that belongs to Paper's gate.

    The calls are matched with their parentheses: the release helper is named
    ``_release_paper_ownership_if_proven`` because that is what it does, and a bare
    substring would fire on the name instead of on a lease call.
    """

    source = "\n".join(
        path.read_text(encoding="utf-8") for path in _python_files(_PAPER_DIR)
    )
    for forbidden in (
        "acquire_paper(",
        "release_paper(",
        "_leases.",
        "ExecutionLeaseManager",
    ):
        assert forbidden not in source, forbidden
    named = set(re.findall(r"ExecutionLease\.(\w+)", source))
    assert named == {"PAPER"}, sorted(named)


# -- Guard H: the window left recovery and finalization ------------------
#
# v2O-E3's half of the same property Guards A and A2 pin for the launch and the active
# session.  The window used to decide when a halt was reconciled, whether a proof was
# current, whether the zero-state proof should start, and whether a finished session's
# ownership could be given up; it also published results of its own, because three of
# those paths reached the workflow directly.  All of it is the capability's now, so each
# retired name is asserted absent *and* each surviving seam is asserted present -- a
# guard that only checked the deletions would pass on a window that had dropped the
# wiring entirely, and a window that dropped it would leave Paper unclosable.


@pytest.mark.parametrize("name", RETIRED_WINDOW_E3_METHODS)
def test_the_retired_recovery_methods_are_gone(name: str) -> None:
    """No E3 sequencing method survives, and no alias under the same name."""

    assert name not in _declared_names(_DESKTOP_PATH), name


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_E3_STATE)
def test_the_window_keeps_no_finalization_state(attribute: str) -> None:
    """The proof's flags belong to the capability; neither may be mirrored here.

    Checked as assignments *and* as declared names, so neither an attribute nor a
    property under a retired name can restore the old ownership -- and the exact-set
    assertion in ``test_the_orchestrator_stores_only_its_injected_collaborators`` stops
    the same thing appearing on the other side.
    """

    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute
    assert attribute not in _declared_names(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("call", RETIRED_WINDOW_E3_CALLS)
def test_the_window_never_drives_recovery_or_finalization(call: str) -> None:
    """No window path may reach the workflow or the service for a recovery decision."""

    assert call not in _DESKTOP_PATH.read_text(encoding="utf-8"), call


@pytest.mark.parametrize("call", RETIRED_WINDOW_E3_ORCHESTRATOR_CALLS)
def test_the_window_never_calls_the_capabilitys_own_sequencing(call: str) -> None:
    """The reconciliation is not routed through a window handler any more.

    A re-added ``_reconcile`` that merely called ``paper_orchestrator.reconcile()``
    would pass every "the decision is the capability's" check while restoring exactly
    the hop this round removed, so the *call* is forbidden rather than only the name.
    """

    assert call not in _DESKTOP_PATH.read_text(encoding="utf-8"), call


def test_the_window_publishes_no_paper_result_of_its_own() -> None:
    """One result path, and it is the capability's.

    The window used to request each event and hand the result to the render path itself
    on the three E3-only routes, which meant two places that knew how a Paper result is
    published.  The events loop is what made that second publication possible, so its
    absence is the claim -- checked on the window's own source, where the loop used to
    be, rather than on a docstring that explains the removal.
    """

    desktop = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "for event in result.events" not in desktop
    handler = _method_source(_DESKTOP_PATH, "_on_paper_result_changed")
    assert "_record_runtime_event" not in handler
    # The capability still has exactly one, and the window's one writer is the
    # capability's event publication.
    assert "for event in result.events:" in _function_source(
        _ORCHESTRATOR_PATH, "_publish_result"
    )
    assert "_record_paper_runtime_event" in desktop


@pytest.mark.parametrize("signal,target", E3_SIGNAL_WIRING)
def test_the_payload_free_publications_reach_their_handlers(
    signal: str, target: str
) -> None:
    """Each E3 publication is wired, so the round cannot pass by publishing into nothing."""

    assert f"{signal}.connect({target})" in _dense(_DESKTOP_PATH), signal


def test_the_e3_signals_carry_no_state() -> None:
    """The three E3 signals are payload-free, pinned at the declaration.

    A signal that carried the phase, a boolean or a state dict would be the second truth
    this round exists to remove -- and a handler that simply ignored the argument would
    hide it.  ``Signal()`` with no argument list is the shape; the exact-set assertion
    over the whole signal surface is in
    ``test_the_orchestrator_exposes_the_declared_signals``.
    """

    source = _method_source(
        _ORCHESTRATOR_PATH, "_publish_result", class_name="PaperOrchestrator"
    )
    assert "result_changed.emit(result)" in source
    declared = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for signal in (
        "presentation_refresh_requested",
        "session_finalized",
        "manual_recovery_required",
    ):
        assert f"{signal} = Signal()" in declared, signal


def test_the_resume_confirmation_reaches_the_capability_through_the_window() -> None:
    """The one recovery intent that legitimately hops through the window.

    It is a ``QMessageBox``, and the capability may not import one -- so the hop is the
    point rather than a leftover.  The window asks; the capability decides whether the
    proof it re-reads afterwards is still current.
    """

    assert (
        f"{RESUME_CONFIRMATION_WIRING[0]}.connect({RESUME_CONFIRMATION_WIRING[1]})"
        in _dense(_DESKTOP_PATH)
    )


def test_the_finalization_gate_is_locked() -> None:
    """The four clauses, in order, and none of them optional.

    A missing phase clause starts a proof for a trading session; a missing finalized
    clause re-proves what is already proven; a missing in-flight clause interleaves two
    readers of one broker connection; a missing backoff re-reads the whole broker once
    per tick while the exits are still working.  The backoff is *conditional on the
    engine still running*, which is the half a tidy-up would drop.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_maybe_schedule_finalization")
    phase = source.index("PaperWorkflowPhase.STOPPING")
    finalized = source.index("result.state.finalized")
    inflight = source.index("self._finalization_inflight")
    engine = source.index('getattr(result.engine_snapshot, "active", True)')
    backoff = source.index("FINALIZATION_REFRESH_BACKOFF_SECONDS")
    start = source.index("self._start_finalization()")
    assert phase < finalized < inflight < engine < backoff < start
    assert "engine_active" in source
    assert "and last_started is not None" in source


def test_the_release_is_two_phase_and_the_gate_is_the_workflows() -> None:
    """Reserve the slot, *then* ask the workflow, then commit -- in that order.

    The invariant the whole round turns on: a successful disconnect is not a finalization.
    Asserted as an ordering *and* as a guard, because the ordering alone would still be
    satisfied by a version that ignored the answer.

    And the order is not a preference.  ``finalize_if_safe`` is a check-and-commit call on
    a canonical owner that releases PAPER, its result, its coordinator and both evidence
    records when it answers ``True`` -- so the slot's releasability has to be proved and
    locked *before* it is asked.  Asking first leaves a refusal from the service arriving
    after the lease is already gone, which is E1's ownerless-session state from the other
    end.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_release_paper_ownership_if_proven")
    reserve = source.index("reservation = self._paper_trading.reserve_active_release()")
    gate = source.index("if not self._workflow.finalize_if_safe():", reserve)
    commit = source.index(
        "self._paper_trading.commit_active_release(reservation)", reserve
    )
    cancel = source.index(
        "self._paper_trading.cancel_active_release(reservation)", reserve
    )
    assert reserve < gate < cancel < commit
    # The one shortcut is the branch where nothing is held, and it is gated on exactly
    # that: with no slot there is no lock to take, so the workflow's own gate is asked
    # directly.  A direct gate *outside* that branch would be the bug this round fixes.
    shortcut = source.index("if not self._workflow.finalize_if_safe():")
    assert source.index("if not self._paper_trading.has_order_service():") < shortcut
    assert shortcut < reserve
    # And an unaccountable slot is reported rather than swallowed: the platform's own
    # "lifecycle refused" type is caught by name and turned into a reason -- for the
    # reservation, before the workflow is asked, and for the commit, as an invariant.
    assert "except PaperTradingLifecycleError as error:" in source
    assert "return str(error)" in source
    assert "self._report_release_invariant(error)" in source
    # Nothing here forces anything: no bare except, no lease access, no retry.
    for forbidden in ("except Exception", "_leases", "release_paper("):
        assert forbidden not in source, forbidden


def test_the_reservation_refusal_comes_before_the_workflow_is_asked() -> None:
    """The transaction boundary, pinned as source order rather than only as behaviour.

    Behaviour covers it too (the release tests assert the workflow was never reached), and
    this is the structural half: a future edit that moved the reservation after the gate --
    the "obvious" tidy-up, since the gate is what decides whether anything happens -- would
    have to change this ordering, and that is exactly when a reviewer should be reading it.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_release_paper_ownership_if_proven")
    reserved = source.index("reservation = self._paper_trading.reserve_active_release()")
    refused = source.index("return str(error)", reserved)
    gate = source.index("if not self._workflow.finalize_if_safe():", reserved)
    assert reserved < refused < gate


def test_the_proof_captures_before_it_disconnects_and_confirms_after() -> None:
    """The task's order, as one assertion on one method.

    Capture asks the broker through the connection the session still holds; confirmation
    consumes the proof only once the callback thread has joined.  Reversing either half
    produces a proof of nothing -- and both reversals are exactly what a reordering
    "cleanup" would do, which is why the order is pinned here rather than left to the
    task's prose.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_start_finalization")
    capture = source.index("capture_finalization_evidence()")
    disconnect = source.index("self._paper_trading.disconnect()")
    confirm = source.index("confirm_finalization_after_disconnect")
    assert capture < disconnect < confirm
    # A missing proof returns *before* the disconnect, so nothing is closed on a proof
    # that was never taken.
    assert source.index("if evidence_id is None:") < disconnect


def test_a_refused_proof_start_is_not_a_failure() -> None:
    """A busy resource group defers; only a task that ran and failed halts.

    The two are different events and the tasking protocol says so.  Failing the refresh
    on ``started is False`` would halt a session over a scheduling collision, so the
    refusal path must clear its own flag and touch the workflow not at all.
    """

    start = _function_source(_ORCHESTRATOR_PATH, "_start_finalization")
    refusal = start.index("if not started:")
    tail = start[refusal:]
    assert "self._finalization_inflight = False" in tail
    assert "fail_finalization_refresh" not in tail
    assert "presentation_refresh_requested.emit()" in tail

    failed = _function_source(_ORCHESTRATOR_PATH, "_finalization_failed")
    assert "self._workflow.fail_finalization_refresh()" in failed
    assert "self.presentation_refresh_requested.emit()" in failed


def test_the_completed_proof_clears_its_flag_after_publishing() -> None:
    """The ordering that stops a proof from starting a second one inside itself.

    Clearing the flag before publishing would re-enter ``_maybe_schedule_finalization``
    from the very result being published -- and for a session that has just gone dormant
    the backoff no longer applies, so the re-entry would loop.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "_finalization_completed")
    assert source.index("self._publish_result(") < source.index("finally:")
    assert "self._finalization_inflight = False" in source[source.index("finally:") :]


def test_shutdown_reads_the_phase_and_never_moves_it_by_hand() -> None:
    """``prepare_shutdown`` classifies from the canonical phase, and delegates everything.

    Three properties.  The **phase is read first** and every branch is a phase branch, so
    nothing is inferred from "the workflow holds no result yet" -- ``CONNECTING`` legitimately
    has no result and a held PAPER lease.  It must reuse ``self.stop()`` rather than call
    ``request_stop`` itself, because a second stop request would be a second sequencing of
    the same intent.  And the post-stop verdict has to be *re-derived*: the same
    ``request_stop`` can halt the session or finalize it outright, so a hard-coded
    "waiting" would describe a session that has no automatic route left.
    """

    source = _function_source(_ORCHESTRATOR_PATH, "prepare_shutdown")
    assert "self.stop()" in source
    assert "request_stop" not in source
    assert "capture_finalization_evidence" not in source
    # The phase is the first thing read, and it is read from the workflow.
    assert source.index("self._workflow.phase") < source.index("self.stop()")
    # Only RUNNING/PAUSED are stopped; STOPPING and the manual-recovery three are not.
    stopped = source.index("PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.PAUSED")
    assert stopped < source.index("PaperWorkflowPhase.STOPPING")
    # And a stop is never assumed to have worked: its outcome is re-classified.
    assert "self._shutdown_verdict_after_the_stop()" in source
    # ``CONNECTING`` is refused explicitly rather than falling through to ownership.
    assert "PaperWorkflowPhase.CONNECTING" in source
    assert "SHUTDOWN_LAUNCH_IN_FLIGHT_REASON" in source

    after_stop = _function_source(
        _ORCHESTRATOR_PATH, "_shutdown_verdict_after_the_stop"
    )
    for ending in (
        "queries.manual_recovery_phase(phase)",
        "PaperWorkflowPhase.STOPPING",
        "SHUTDOWN_STOP_REFUSED_REASON",
    ):
        assert ending in after_stop, ending


def test_the_manual_recovery_rule_has_one_definition() -> None:
    """The three operator-only phases are declared once, in ``queries``.

    Two copies -- one for the halt announcement, one for the shutdown disposition -- is
    how one of them starts offering an automatic route the other forbids.  The capability
    never *names* a manual-recovery phase at all: it asks, so the set is not even
    representable here.  ``STOPPING`` and the ``RUNNING``/``PAUSED`` pair are the
    capability's own facts and are named where its automatic routes are.
    """

    queries_source = _QUERIES_PATH.read_text(encoding="utf-8")
    orchestrator = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "PaperWorkflowPhase.HALTED" in queries_source
    assert "queries.manual_recovery_phase(" in orchestrator
    # No manual-recovery phase is spelled out where the rule is applied.
    for forbidden in (
        "PaperWorkflowPhase.HALTED",
        "PaperWorkflowPhase.RECONCILING)",
        "{PaperWorkflowPhase.RECONCILING",
    ):
        assert forbidden not in orchestrator, forbidden


def test_the_ownership_block_is_never_downgraded_to_ready() -> None:
    """E1's invariant is not relaxed now that there is a recovery path.

    The tempting edit is "we have in-app recovery now, so an unaccountable claim can be
    cancelled and the process allowed to exit".  It cannot: the ownership would be
    dropped without proof, which is the exact failure the promotion reservation exists
    to make unreachable.  So the refusal has to survive as its own disposition, on the
    path where the slot is still held.  Asserted on the method's *code* rather than its
    text, because the order the prose discusses the three situations in is not the order
    the branches are written in.
    """

    code = _code_only(
        _ORCHESTRATOR_PATH, "_ownership_verdict", class_name="PaperOrchestrator"
    )
    # Whitespace collapsed to single spaces rather than removed, so the assertions below stay
    # readable: the point is the branch, not the formatting.
    flat = re.sub(r"\s+", " ", code)
    # ``READY`` requires the *absence* of all three owners: a candidate, the active slot and
    # the lease.  The old shape short-circuited on "no active service", which is not the
    # same claim -- the service owns a candidate slot too.
    assert "self._paper_trading.has_candidate_ownership()" in flat
    assert "holds_a_slot = self._paper_trading.has_order_service()" in flat
    assert "holds_the_lease = self._workflow.lease is ExecutionLease.PAPER" in flat
    assert (
        "if not holds_a_slot and (not holds_the_lease):"
        " return PaperShutdownResult(PaperShutdownDisposition.READY)"
    ) in flat
    # Every other outcome for a held ownership is a refusal, never a silent release.
    assert "self._ownership_blocked(" in code
    assert "clear_active" not in code, "the decision must not force a release"
    for forbidden in ("except Exception", "release_paper("):
        assert forbidden not in code, forbidden

    # And the one phase that has no result *and* a held lease is refused outright, so a
    # close cannot walk past a launch that is still in flight.
    shutdown_code = _code_only(
        _ORCHESTRATOR_PATH, "prepare_shutdown", class_name="PaperOrchestrator"
    )
    assert shutdown_code.index("PaperWorkflowPhase.CONNECTING") < shutdown_code.index(
        "self._ownership_verdict()"
    )
