"""Architecture guards for the G2-B execution / AutoQuant orchestration.

The round's subject is *who owns the desktop execution route*.  The guards read
the source as an AST, for the same reason the F1/F2/G1/G2-A guards do: "which
attributes does this class touch?" is the question ownership actually asks, and
text matching would either over- or under-match.

The properties, matching the round brief:

1. the window holds no execution-route mutable state -- no
   ``auto_quant_candidates``, no ``_launch_busy``, no
   ``_channel_check_inflight``;
2. the window calls no ``ExecutionPage`` orchestration method: constructing the
   page and handing it the palette is the whole of its relationship with it;
3. ``ExecutionOrchestrator`` is the only orchestration caller of those methods
   (``set_strategy_options`` additionally belongs to the backtest and targeted
   capabilities on *their* pages, and to nobody else);
4. the execution package imports no other capability orchestrator and not
   ``MainWindow``;
5. it imports no Paper type at all -- not the workflow controller, not the phase
   enum, not the workflow's error -- and likewise no risk / execution
   application, no concrete broker adapter and no order repository;
6. the candidate shortlist has exactly one retained desktop owner, and it is
   ``ExecutionOrchestrator.candidates``;
7. the route caches nothing else: its state is five injected handles and three
   route-local facts;
8. ``Paper``'s retained presentation is read for *display only* -- one reader,
   and it is the render path;
9. the generic task failure path does not touch the execution route;
10. the generic worker release does not repaint the execution route;
11. ``StrategySelectionService`` is still the AUTO_ROTATION runtime selection
    truth, and the route reads it there rather than off the combo;
12. governance selection still does not repoint AUTO_ROTATION;
13. the Market stop / switch interlock is still in the composition root;
14. Paper keeps its lifecycle: the route's Paper surface is exactly the six
    delegated seams, and the port it is typed against declares no more;
15. the G1 generic-runtime ownership does not regress;
16. the G2-A strategy-governance ownership does not regress;
17. the F1 runtime-event ownership does not regress;
18. the F2 settings ownership does not regress;
19. the no-god-object rule: no ``ExecutionManager`` / ``AutoQuantManager`` /
    ``TradingManager`` / ``ApplicationContext`` / ``ServiceBag`` /
    ``CrossCapabilityCoordinator``.

Each of 15-18 is also asserted by its own round's suite; the copies here exist so
a G2-B change that quietly unwinds them fails in the round that caused it.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_ORCH_DIR = _SRC / "desktop_v2" / "orchestration"
_EXECUTION_DIR = _ORCH_DIR / "execution"
_EXECUTION_ORCHESTRATOR = _EXECUTION_DIR / "orchestrator.py"
_EXECUTION_MODELS = _EXECUTION_DIR / "models.py"
_EXECUTION_QUERIES = _EXECUTION_DIR / "queries.py"
_PAPER_ORCHESTRATOR = _ORCH_DIR / "paper" / "orchestrator.py"
_STRATEGY_DIR = _ORCH_DIR / "strategy"

_EXECUTION_MODULE = (
    "us_quant.desktop_v2.orchestration.execution.orchestrator"
)
_BACKTEST_MODULE = (
    "us_quant.desktop_v2.orchestration.research.backtest.orchestrator"
)
_TARGETED_SESSION_MODULE = (
    "us_quant.desktop_v2.orchestration.research.targeted.session.orchestrator"
)

#: The desktop layer.  Ownership claims are about this layer: the Paper
#: application has its own ``_candidates`` slot for a broker candidate, which is
#: a different fact with a different owner and is deliberately out of scope.
_DESKTOP_LAYER = (_SRC / "desktop.py", _SRC / "desktop_v2")

#: The state the window must no longer hold.  These are the three route-local
#: facts G2-B moved into the orchestrator.
_RETIRED_WINDOW_STATE = {
    "auto_quant_candidates",
    "_launch_busy",
    "_channel_check_inflight",
}

#: The ``ExecutionPage`` orchestration API, and the only production callers each
#: one may have.  Every receiver attribute the guard recognises is listed in
#: ``_PAGE_HOLDERS``, so a caller using any of the established holder names is
#: attributed to its own class rather than slipping through.
#:
#: ``render`` is deliberately *not* in this map: every page has one, so a
#: single-owner map would be false.  That half is asserted separately, on the
#: window's own ``self.execution_page`` receiver.
_PAGE_API_OWNERS: dict[str, set[tuple[str, str]]] = {
    "render_candidates": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    "render_context": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    "render_preflight": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    "render_execution_health": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    "set_control_state": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    "set_arm_confirmed": {(_EXECUTION_MODULE, "ExecutionOrchestrator")},
    # The only shared page API: three pages, three owners.
    "set_strategy_options": {
        (_EXECUTION_MODULE, "ExecutionOrchestrator"),
        (_BACKTEST_MODULE, "BacktestOrchestrator"),
        (_TARGETED_SESSION_MODULE, "TargetedSessionOrchestrator"),
    },
}

#: Attribute names a class uses to hold a page it orchestrates.
_PAGE_HOLDERS = {"_page", "execution_page"}

#: Capability classes the execution package must never import.
_FORBIDDEN_EXECUTION_IMPORTS = {
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "PaperOrchestrator",
    "ScannerOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "ShadowOrchestrator",
    "StrategyGovernanceOrchestrator",
    "SettingsOrchestrator",
    "TargetedSessionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "DashboardOrchestrator",
    "RuntimeEventsOrchestrator",
}

#: The Paper vocabulary the execution package must not name.  Arming a session is
#: Paper's decision, and a second object able to reason about the phase is how a
#: second lifecycle owner appears.
_FORBIDDEN_PAPER_IMPORTS = {
    "PaperWorkflowController",
    "PaperWorkflowPhase",
    "WorkflowStateError",
    "PaperTradingService",
}

#: The risk / execution / broker vocabulary that belongs to the composition root.
_FORBIDDEN_COMPOSITION_IMPORTS = {
    "RiskApplication",
    "ExecutionApplication",
    "ExecutionLease",
    "IBKRConnectionConfig",
}

#: Module paths the execution package must never import.  Matched exactly or as a
#: package prefix, so ``us_quant.desktop`` never swallows ``us_quant.desktop_v2``.
_FORBIDDEN_MODULE_PATHS = (
    "us_quant.desktop",
    "us_quant.desktop_v2.orchestration.paper",
    "us_quant.trading.runtime.workflow_state",
    "us_quant.trading.application.risk",
    "us_quant.trading.application.paper",
    "us_quant.trading.composition",
)

#: Every member ``PaperFactsPort`` declares, and therefore the whole of the Paper
#: surface the route may touch: the three delegated queries it has always had,
#: plus G2-B's six narrow preparation seams.
#:
#: Note what is absent from G2-B's review fix onwards: ``has_runtime_obligations``.
#: The route used to read it to decide whether a market stop was allowed, which
#: made the Paper -> Market interlock a capability decision; choosing to stop is
#: composition's, so the route may not even ask.
_PAPER_SEAMS = {
    "preparation_active",
    "launch_attempt_in_flight",
    "order_service_held",
    "runtime_active",
    "presentation",
    "session_control_facts",
    "begin_preparation",
    "cancel_preparation",
    "mark_preparation_ready",
}

#: Every field the route's provider group may carry, enumerated and frozen.
#:
#: This is an *allowlist of the whole surface*, not a denylist of command names.
#: The G2-B review found the difference the hard way: the earlier guard banned
#: the guessed names ``start_market`` / ``stop_market`` / ``switch_market`` /
#: ``set_subscription_symbols``, and ``stop_market_data`` walked straight through
#: it while carrying a Paper fact into a Market command.  A name-based ban can
#: always be evaded by renaming; a frozen surface cannot.  Adding a field -- under
#: any name -- now has to be a deliberate edit here, which is exactly the moment
#: to ask whether the fact belongs to composition instead.
#: The market-facing provider fields, and every one of them is a read.
_MARKET_READ_PROVIDER_FIELDS = {
    "market_snapshot",
    "market_is_live",
    "market_provider",
    "was_recently_ready",
    "recently_ready_symbols",
}

#: The route's own provider-backed actions.  This route decides *when* a scan
#: runs and *when* the order channel is probed, and neither is a cross-capability
#: interlock -- one reads the research pool, the other opens a read-only probe.
#: They are named here so the market-name rule below can tell them apart from a
#: Market *lifecycle* command.
_ROUTE_OWNED_PROVIDER_ACTIONS = {
    "run_market_scan",
    "probe_order_channel",
}

_ALLOWED_PROVIDER_FIELDS = (
    _ROUTE_OWNED_PROVIDER_ACTIONS
    | _MARKET_READ_PROVIDER_FIELDS
    | {
        # Facts other capabilities own, read when needed and never cached.
        "universe",
        "scan",
        "adopt_scan",
        "schedule_history",
        "refresh_history",
        "account_portfolio",
        "fresh_paper_capital",
        "broker_state",
        "reconciliation_rows",
        "audit_rows",
        "latency_rows",
        "exposure_multipliers",
        "research_scenario_capital",
        "maximum_position_exposure_pct",
        "paper_capability_enabled",
        "extended_hours_enabled",
    }
)

#: The four market commands.  Each is a signal, because each one is gated by a
#: Paper or Shadow fact this route may not read.
_MARKET_REQUEST_SIGNALS = {
    "market_start_requested",
    "market_switch_requested",
    "market_subscription_requested",
    "market_stop_requested",
}

#: The composition bridge that owns the stop interlock, and the capabilities it
#: is the only thing allowed to name at once.
_STOP_BRIDGE = "_on_execution_market_stop_requested"
_STOP_BRIDGE_KNOWS = (
    "paper_orchestrator",
    "shadow_orchestrator",
    "market_orchestrator",
)

#: The state the execution orchestrator is allowed to keep.  Five injected
#: handles and three route-local facts -- nothing that belongs to a capability.
_ALLOWED_EXECUTION_STATE = {
    "_page",
    "_selection",
    "_paper",
    "_providers",
    "_submit_task",
    "_launch_busy",
    "_channel_probe_inflight",
    "_candidates",
}

#: Names that would be an aggregate manager if any class carried them.
_FORBIDDEN_CLASS_NAMES = {
    "ExecutionManager",
    "AutoQuantManager",
    "TradingManager",
    "TradingContext",
    "ApplicationContext",
    "ServiceBag",
    "DesktopTradingController",
    "CrossCapabilityCoordinator",
    "EventBus",
}


# -- helpers -------------------------------------------------------------


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _python_files(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        member.name: member
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _statements_without_docstring(node: ast.FunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _method_source(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(
                ast.unparse(statement)
                for statement in _statements_without_docstring(node)
            )
    raise AssertionError(f"method {name} not found")


def _main_window() -> ast.ClassDef:
    return _class_node(_tree(_DESKTOP), "MainWindow")


def _window_methods() -> dict[str, ast.FunctionDef]:
    return _methods(_main_window())


def _self_assignments(node: ast.AST) -> set[str]:
    """Every ``self.<name> = ...`` / annotated assignment inside ``node``."""

    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, ast.AnnAssign):
            targets = [child.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def _self_attribute_names(path: pathlib.Path, class_name: str) -> set[str]:
    """Every attribute name accessed on ``self`` anywhere in the class.

    AST-based on purpose: a *comment* that mentions a retired name ("there is no
    ``self.workers`` alias") is documentation of the rule, not a violation of it.
    """

    node = _class_node(_tree(path), class_name)
    names: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "self"
        ):
            names.add(child.attr)
    return names


def _self_attr_reads(node: ast.AST, owner: str) -> set[str]:
    """The attributes read on ``self.<owner>.`` inside ``node``."""

    names: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Attribute)
            and isinstance(child.value.value, ast.Name)
            and child.value.value.id == "self"
            and child.value.attr == owner
        ):
            names.add(child.attr)
    return names


def _module_of(path: pathlib.Path) -> str:
    rel = path.relative_to(_SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return "us_quant." + ".".join(parts)


def _page_api_calls() -> dict[str, set[tuple[str, str]]]:
    """``{page-API method: {(module, class) that calls it on a page}}``.

    A call counts when its receiver is ``self.<holder>`` for a holder name in
    ``_PAGE_HOLDERS``, which is how every orchestrator and the window reach a
    page.  The page's own internal delegation (``self.controls`` /
    ``self.details``) is deliberately out of scope: it is not an orchestration
    caller.
    """

    calls: dict[str, set[tuple[str, str]]] = {}
    for path in _python_files(_SRC):
        tree = _tree(path)
        module = _module_of(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if not isinstance(member, ast.FunctionDef):
                    continue
                for child in ast.walk(member):
                    if not isinstance(child, ast.Call):
                        continue
                    func = child.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    if func.attr not in _PAGE_API_OWNERS:
                        continue
                    receiver = func.value
                    if not (
                        isinstance(receiver, ast.Attribute)
                        and isinstance(receiver.value, ast.Name)
                        and receiver.value.id == "self"
                        and receiver.attr in _PAGE_HOLDERS
                    ):
                        continue
                    calls.setdefault(func.attr, set()).add(
                        (module, node.name)
                    )
    return calls


# -- 1: no execution-route state on the window ---------------------------


def test_the_window_holds_no_execution_route_state() -> None:
    assignments: set[str] = set()
    for method in _window_methods().values():
        assignments |= _self_assignments(method)
    for name in sorted(_RETIRED_WINDOW_STATE):
        assert name not in assignments, (
            f"MainWindow assigns self.{name}; the execution route's state "
            "belongs to ExecutionOrchestrator"
        )


def test_the_window_declares_no_shortlist_alias_or_shim() -> None:
    """No attribute, no method and no forwarding property for the route state."""

    source = _DESKTOP.read_text(encoding="utf-8")
    declared = set(_window_methods())
    for name in sorted(_RETIRED_WINDOW_STATE):
        assert f"self.{name}" not in source, name
        assert name not in declared, name


# -- 2 / 3: one orchestration caller of the execution page ---------------


def test_the_window_calls_no_execution_page_orchestration_method() -> None:
    forbidden = set(_PAGE_API_OWNERS) | {"render"}
    called: set[str] = set()
    for method in _window_methods().values():
        for child in ast.walk(method):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in forbidden:
                continue
            receiver = func.value
            if (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "self"
                and receiver.attr == "execution_page"
            ):
                called.add(func.attr)
    assert not called, sorted(called)


def test_the_window_only_hands_the_execution_page_its_palette() -> None:
    """The window's entire ``execution_page`` surface is the theme handoff."""

    allowed = {"set_palette"}
    called: set[str] = set()
    for method in _window_methods().values():
        for child in ast.walk(method):
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Attribute)
                and isinstance(child.value.value, ast.Name)
                and child.value.value.id == "self"
                and child.value.attr == "execution_page"
            ):
                called.add(child.attr)
    assert called <= allowed, sorted(called - allowed)


def test_the_execution_orchestrator_is_the_only_orchestration_caller() -> None:
    calls = _page_api_calls()
    for method, owners in sorted(_PAGE_API_OWNERS.items()):
        assert calls.get(method, set()) == owners, (method, calls.get(method))


# -- 4 / 5 / 6: what the execution package may import --------------------


def test_the_execution_package_imports_no_other_capability() -> None:
    for path in _python_files(_EXECUTION_DIR):
        tree = _tree(path)
        names = _imported_names(tree)
        assert not (names & _FORBIDDEN_EXECUTION_IMPORTS), (
            path.name,
            sorted(names & _FORBIDDEN_EXECUTION_IMPORTS),
        )
        for module in _imports(tree):
            for forbidden in _FORBIDDEN_MODULE_PATHS:
                assert not (
                    module == forbidden
                    or module.startswith(forbidden + ".")
                ), (path.name, module)


def test_the_execution_package_names_no_paper_type() -> None:
    for path in _python_files(_EXECUTION_DIR):
        names = _imported_names(_tree(path))
        assert not (names & _FORBIDDEN_PAPER_IMPORTS), (
            path.name,
            sorted(names & _FORBIDDEN_PAPER_IMPORTS),
        )
        source = path.read_text(encoding="utf-8")
        assert "PaperWorkflowPhase" not in source, path.name
        assert "WorkflowStateError" not in source, path.name


def test_the_execution_package_names_no_risk_or_execution_application() -> None:
    for path in _python_files(_EXECUTION_DIR):
        names = _imported_names(_tree(path))
        assert not (names & _FORBIDDEN_COMPOSITION_IMPORTS), (
            path.name,
            sorted(names & _FORBIDDEN_COMPOSITION_IMPORTS),
        )


def test_the_execution_orchestrator_keeps_only_route_local_state() -> None:
    tree = _tree(_EXECUTION_ORCHESTRATOR)
    node = _class_node(tree, "ExecutionOrchestrator")
    assigned: set[str] = set()
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assigned |= _self_assignments(member)
    unexpected = assigned - _ALLOWED_EXECUTION_STATE
    assert not unexpected, sorted(unexpected)
    # The three route-local facts are all there, and nothing else was lost.
    assert {"_launch_busy", "_channel_probe_inflight", "_candidates"} <= assigned


def test_the_shortlist_has_exactly_one_retained_desktop_owner() -> None:
    writers: set[tuple[str, str]] = set()
    files: list[pathlib.Path] = []
    for root in _DESKTOP_LAYER:
        if root.is_file():
            files.append(root)
        else:
            files.extend(_python_files(root))
    for path in files:
        tree = _tree(path)
        module = _module_of(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                assigned = _self_assignments(member)
                if "auto_quant_candidates" in assigned or "_candidates" in assigned:
                    writers.add((module, node.name))
    assert writers == {(_EXECUTION_MODULE, "ExecutionOrchestrator")}, sorted(
        writers
    )


def test_the_route_candidates_are_published_as_one_read_only_fact() -> None:
    tree = _tree(_EXECUTION_ORCHESTRATOR)
    node = _class_node(tree, "ExecutionOrchestrator")
    candidates = _methods(node)["candidates"]
    assert any(
        isinstance(item, ast.Name) and item.id == "property"
        for item in candidates.decorator_list
    ), "candidates must stay a read-only property"
    source = "\n".join(
        ast.unparse(statement)
        for statement in _statements_without_docstring(candidates)
    )
    assert "self._candidates" in source
    # No setter: a mutable handle on the shortlist is a second writer.
    assert not any(
        isinstance(member, ast.FunctionDef)
        and member.name == "candidates"
        and any(
            ast.unparse(item).endswith("setter")
            for item in member.decorator_list
        )
        for member in node.body
    )


def test_the_shortlist_is_built_only_by_the_pure_rule() -> None:
    """Where a scan becomes candidates: one rule, in ``queries``, not inline."""

    tree = _tree(_EXECUTION_ORCHESTRATOR)
    shortlist = _method_source(tree, "_build_shortlist")
    assert "queries.build_candidates" in shortlist
    assert "AutoQuantCandidate(" not in shortlist, (
        "the orchestrator must not construct candidates inline; that rule is "
        "queries.build_candidates"
    )


# -- 7: the presentation is read for display only ------------------------


def test_paper_presentation_is_read_only_on_the_render_path() -> None:
    tree = _tree(_EXECUTION_ORCHESTRATOR)
    node = _class_node(tree, "ExecutionOrchestrator")
    readers = sorted(
        member.name
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "presentation" in _self_attr_reads(member, "_paper")
    )
    assert readers == ["refresh_current"], readers


def test_the_paper_port_is_exactly_the_declared_seams() -> None:
    tree = _tree(_EXECUTION_ORCHESTRATOR)
    node = _class_node(tree, "ExecutionOrchestrator")
    used: set[str] = set()
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            used |= _self_attr_reads(member, "_paper")
    assert used <= _PAPER_SEAMS, sorted(used - _PAPER_SEAMS)
    # And the port the orchestrator is typed against declares no more than the
    # seams the capability actually delegates.
    protocol = _class_node(_tree(_EXECUTION_MODELS), "PaperFactsPort")
    declared: set[str] = set()
    for member in protocol.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared.add(member.name)
    assert declared == _PAPER_SEAMS, sorted(declared ^ _PAPER_SEAMS)


def test_the_preparation_transitions_are_delegated_not_reimplemented() -> None:
    """The route asks Paper; it never moves a phase itself."""

    source = _EXECUTION_ORCHESTRATOR.read_text(encoding="utf-8")
    for token in ("begin_preparing", "cancel_preparing", "mark_ready", ".phase"):
        assert token not in source, token
    tree = _tree(_PAPER_ORCHESTRATOR)
    for name in ("begin_preparation", "cancel_preparation", "mark_preparation_ready"):
        body = _method_source(tree, name)
        assert "self._workflow" in body, name


def test_the_route_never_reads_the_shortlist_for_a_launch_gate() -> None:
    """``launch_locked`` is Paper facts plus local flags -- never the shortlist."""

    tree = _tree(_EXECUTION_ORCHESTRATOR)
    body = _method_source(tree, "launch_locked")
    assert "_candidates" not in body
    assert "_paper.launch_attempt_in_flight" in body
    assert "_paper.order_service_held" in body
    assert "_paper.runtime_active" in body
    assert "_channel_probe_inflight" in body
    assert "_launch_busy" in body


# -- 8 / 9: the generic task lifecycle does not touch the route ----------


def test_the_generic_task_failure_does_not_touch_the_execution_route() -> None:
    body = _method_source(_tree(_DESKTOP), "_task_failed")
    for token in (
        "execution_orchestrator",
        "execution_page",
        "_launch_busy",
        "set_arm_confirmed",
    ):
        assert token not in body, token
    # What it must still do: log, persist one event, and ask the operator.
    assert "TASK_FAILED" in body
    assert "_log" in body
    assert "QMessageBox.warning" in body


def test_the_generic_worker_release_does_not_repaint_the_route() -> None:
    body = _method_source(_tree(_DESKTOP), "_worker_finished")
    assert "execution_orchestrator" not in body
    assert "execution_page" not in body
    assert "_publish_execution_controls" not in body
    assert "task_controller.finish" in body


def test_every_route_task_releases_its_own_state() -> None:
    """Both route-owned tasks name an ``on_failure`` that clears their own fact."""

    tree = _tree(_EXECUTION_ORCHESTRATOR)
    probe = _method_source(tree, "request_channel_check")
    assert "on_failure=self._channel_probe_failed" in probe
    assert "_channel_probe_inflight = False" in _method_source(
        tree, "_channel_probe_failed"
    )
    prepare = _method_source(tree, "request_prepare")
    assert "on_failure=self._preparation_failed" in prepare
    failed = _method_source(tree, "_preparation_failed")
    assert "_cancel_preparation_if_active" in failed
    assert "_set_launch_busy(False)" in failed


# -- 10: the selection service is still the truth ------------------------


def test_the_route_reads_the_auto_rotation_selection_from_the_service() -> None:
    source = _EXECUTION_ORCHESTRATOR.read_text(encoding="utf-8")
    assert "StrategySelectionPurpose.AUTO_ROTATION" in source
    assert "self._selection.selected(" in source
    assert "self._selection.restore_or_default(" in source
    # The combo is a view: the route must never read the selection off the page.
    assert "selected_strategy_version_id" not in source


def test_the_route_keeps_the_selection_service_as_the_only_writer() -> None:
    """Only ``select_strategy`` adopts a page choice, and it logs a refusal."""

    tree = _tree(_EXECUTION_ORCHESTRATOR)
    body = _method_source(tree, "select_strategy")
    assert "self._selection.select(" in body
    assert "StrategySelectionError" in body
    assert "log_requested" in body
    # A refusal changes nothing else: no refresh, no arm write.
    assert "refresh_preflight" not in body
    assert "set_arm_confirmed" not in body


def test_the_governance_route_still_cannot_repoint_auto_rotation() -> None:
    for path in _python_files(_STRATEGY_DIR):
        names = _imported_names(_tree(path))
        assert "StrategySelectionService" not in names, path.name
        source = path.read_text(encoding="utf-8")
        assert "AUTO_ROTATION" not in source, path.name


# -- 11: the market interlock stays in composition -----------------------


def _provider_fields() -> set[str]:
    providers = _class_node(_tree(_EXECUTION_MODELS), "ExecutionProviders")
    return {
        member.target.id
        for member in providers.body
        if isinstance(member, ast.AnnAssign)
        and isinstance(member.target, ast.Name)
    }


def _signal_names(path: pathlib.Path, class_name: str) -> set[str]:
    node = _class_node(_tree(path), class_name)
    return {
        member.targets[0].id
        for member in node.body
        if isinstance(member, ast.Assign)
        and isinstance(member.targets[0], ast.Name)
        and isinstance(member.value, ast.Call)
        and isinstance(member.value.func, ast.Name)
        and member.value.func.id == "Signal"
    }


def test_the_provider_surface_is_frozen_and_command_free() -> None:
    """No market command may hide in the provider group -- under any name.

    The review fix's whole point: the previous guard banned four *guessed* names
    and missed ``stop_market_data``.  The surface is now enumerated, so any
    addition fails here whatever it is called.
    """

    fields = _provider_fields()
    assert fields == _ALLOWED_PROVIDER_FIELDS, sorted(
        fields ^ _ALLOWED_PROVIDER_FIELDS
    )
    assert "stop_market_data" not in fields
    assert _MARKET_READ_PROVIDER_FIELDS <= fields
    # Nothing else may carry a market name in any spelling, so a renamed market
    # command fails here even before the frozen-surface check above is updated.
    reads = _MARKET_READ_PROVIDER_FIELDS | _ROUTE_OWNED_PROVIDER_ACTIONS
    for field in sorted(fields - reads):
        assert "market" not in field.lower(), field


def test_the_market_interlock_still_lives_in_the_composition_root() -> None:
    window = _window_methods()
    for name in ("_stop_market_data", "_request_market_switch"):
        assert name in window, name
    # The route names no market object at all.
    names = _self_attribute_names(
        _EXECUTION_ORCHESTRATOR, "ExecutionOrchestrator"
    )
    assert "market_orchestrator" not in names
    assert "market_orchestrator" not in _EXECUTION_ORCHESTRATOR.read_text(
        encoding="utf-8"
    )


def test_request_stop_stream_only_publishes_the_request() -> None:
    """The route asks; it does not read Paper, decide, or stop anything."""

    body = _method_source(_tree(_EXECUTION_ORCHESTRATOR), "request_stop_stream")
    assert "market_stop_requested.emit()" in body
    # No Paper fact, no provider call, no market call, no presentation.
    assert "_paper" not in body
    assert "has_runtime_obligations" not in body
    assert "obligations" not in body
    assert "_providers" not in body
    assert "render_context" not in body
    assert "information_requested" not in body
    # Exactly one statement: one emit.
    statements = [
        statement
        for statement in _statements_without_docstring(
            _methods(
                _class_node(_tree(_EXECUTION_ORCHESTRATOR), "ExecutionOrchestrator")
            )["request_stop_stream"]
        )
    ]
    assert len(statements) == 1, ast.unparse(statements)


def test_the_stop_outcome_is_presentation_only() -> None:
    """The route draws the refusal and the completed stop; it decides neither."""

    refused = _method_source(_tree(_EXECUTION_ORCHESTRATOR), "on_market_stop_refused")
    assert "STOP_STREAM_BLOCKED_TITLE" in refused
    assert "STOP_STREAM_BLOCKED_MESSAGE" in refused
    assert "information_requested.emit" in refused
    stopped = _method_source(_tree(_EXECUTION_ORCHESTRATOR), "on_market_stopped")
    assert "STOP_STREAM_SUMMARY" in stopped
    assert "render_context" in stopped
    for body in (refused, stopped):
        assert "_paper" not in body
        assert "_providers" not in body


def test_the_execution_stop_bridge_owns_the_interlock() -> None:
    """One composition bridge knows Paper, Shadow and Market at the same time."""

    assert _STOP_BRIDGE in _window_methods(), _STOP_BRIDGE
    bridge = _method_source(_tree(_DESKTOP), _STOP_BRIDGE)
    for owner in _STOP_BRIDGE_KNOWS:
        assert owner in bridge, owner
    # The Paper answer comes first, and it short-circuits.
    assert bridge.index("has_runtime_obligations") < bridge.index(
        "market_orchestrator.stop()"
    )
    assert "on_market_stop_refused()" in bridge
    assert "on_market_stopped()" in bridge
    # Shadow is taken down before Market, and only Market's own verdict decides
    # whether the route is told the stop happened.
    assert bridge.index("shadow_orchestrator.stop()") < bridge.index(
        "market_orchestrator.stop()"
    )
    assert "is_active" in bridge
    # The bridge is wired to the request signal, and to nothing else.
    wiring = _method_source(_tree(_DESKTOP), "_connect_execution_page")
    assert "market_stop_requested.connect(" in wiring
    assert f"self.{_STOP_BRIDGE}" in wiring


def test_the_four_market_commands_are_requests() -> None:
    """Four signals, four composition consumers, no fifth provider command."""

    signals = _signal_names(_EXECUTION_ORCHESTRATOR, "ExecutionOrchestrator")
    assert _MARKET_REQUEST_SIGNALS <= signals, sorted(
        _MARKET_REQUEST_SIGNALS - signals
    )
    wiring = _method_source(_tree(_DESKTOP), "_connect_execution_page")
    for name in sorted(_MARKET_REQUEST_SIGNALS):
        assert f"execution.{name}.connect(" in wiring, name
    # The route's public API names no market mutating verb as a method.
    methods = _methods(
        _class_node(_tree(_EXECUTION_ORCHESTRATOR), "ExecutionOrchestrator")
    )
    for name in sorted(methods):
        lowered = name.lower()
        assert not (
            lowered.startswith(("start_market", "stop_market", "switch_market"))
        ), name
        assert "subscription" not in lowered, name


def test_the_market_interlock_still_reads_paper_and_shadow() -> None:
    stop = _method_source(_tree(_DESKTOP), "_stop_market_data")
    assert "paper_orchestrator" in stop
    switch = _method_source(_tree(_DESKTOP), "_request_market_switch")
    assert "has_runtime_obligations" in switch or "paper_orchestrator" in switch


# -- 12 / 13: the earlier rounds do not regress --------------------------


def test_g1_generic_runtime_ownership_does_not_regress() -> None:
    names = _self_attribute_names(_DESKTOP, "MainWindow")
    assert "workers" not in names
    assert "_closing" not in names
    assert "task_controller" in names
    assert "runtime_supervisor" in names
    assert "shutting_down" in _method_source(_tree(_DESKTOP), "_start_task")


def test_g2a_strategy_governance_ownership_does_not_regress() -> None:
    names = _self_attribute_names(_DESKTOP, "MainWindow")
    assert "strategy_governance_orchestrator" in names
    retired = {
        "_strategy_version_selected",
        "_refresh_strategy_page",
        "_strategy_clone_requested",
        "_strategy_transition_requested",
        "_strategy_version_or_none",
    }
    assert not (retired & set(_window_methods())), sorted(
        retired & set(_window_methods())
    )


def test_f1_runtime_event_ownership_does_not_regress() -> None:
    names = _self_attribute_names(_DESKTOP, "MainWindow")
    assert "runtime_events" not in names
    router = _method_source(_tree(_DESKTOP), "_route_runtime_event")
    assert "runtime_events_orchestrator" in router


def test_f2_settings_ownership_does_not_regress() -> None:
    names = _self_attribute_names(_DESKTOP, "MainWindow")
    assert "_settings_api_provider" not in names
    assert "_connection_settings_enabled" not in names


# -- 19: no god object --------------------------------------------------


def test_no_god_object_was_introduced() -> None:
    found: set[str] = set()
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name in _FORBIDDEN_CLASS_NAMES:
                found.add(node.name)
    assert not found, sorted(found)


def test_the_route_holds_no_capability_object() -> None:
    """The providers are callables: a capability handle would be a second owner."""

    tree = _tree(_EXECUTION_MODELS)
    providers = _class_node(tree, "ExecutionProviders")
    annotated = [
        ast.unparse(member.annotation)
        for member in providers.body
        if isinstance(member, ast.AnnAssign) and member.annotation is not None
    ]
    assert annotated, "ExecutionProviders declares no fields"
    for annotation in annotated:
        assert annotation.startswith("Callable["), annotation
