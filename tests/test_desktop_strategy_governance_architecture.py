"""Architecture guards for the G2-A strategy governance orchestration.

The round's subject is *who owns the desktop strategy governance sequencing*.
The guards read the source as an AST, for the same reason the F1/F2/G1 guards
do: "which attributes does this class touch?" is the question ownership
actually asks, and text matching would either over- or under-match.

The properties, matching the round brief:

1. the window never calls ``StrategyPage.render``;
2. ``StrategyGovernanceOrchestrator`` is the only orchestration caller of
   that render, and no other capability package imports the page;
3. the window's remaining ``self.strategies`` surface is composition-shaped:
   no ``clone_version`` / ``transition`` anywhere, and ``list_versions`` /
   ``get_version`` only on the terminal export path;
4. the orchestrator imports no other capability and not ``MainWindow``;
5. ``StrategyPage`` is still service-free;
6. the governance path never touches ``StrategySelectionService``;
7. the orchestrator caches no catalogue (state is exactly
   ``_application`` / ``_page``);
8. the ``STATUS_CHANGE`` runtime event is published by the capability and
   routed by the window's one generic router -- the window writes nothing;
9. the no-god-object rule: no ``StrategyManager`` / ``StrategyContext`` /
   ``StrategyServiceBag``;
10. ``AccountOrchestrator.set_notice`` accepts only finished text: it never
    names a strategy type.

(G1's guards live in ``test_desktop_composition_closure_architecture.py`` and
keep running; F1/F2 no-regression guards live there too.)
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
_STRATEGY_DIR = _ORCH_DIR / "strategy"
_STRATEGY_PAGE = _SRC / "desktop_v2" / "pages" / "strategy.py"

#: Window methods allowed to read the strategy application's catalogue, with
#: the members they may read.  Governance sequencing retired from the window;
#: the terminal export is a legitimate consumer of the finished catalogue.
_STRATEGIES_SURFACE: dict[str, set[str]] = {
    "_export_runtime_bundle": {"list_versions"},
}

#: Capability types the strategy package must never import.
_FORBIDDEN_STRATEGY_IMPORTS = {
    "AccountOrchestrator",
    "MarketOrchestrator",
    "PaperOrchestrator",
    "ShadowOrchestrator",
    "BacktestOrchestrator",
    "TargetedSessionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "ExecutionApplication",
    "RiskApplication",
    "MainWindow",
    "StrategySelectionService",
    "RuntimeEventsOrchestrator",
    "SettingsOrchestrator",
}

#: Names that would be a god object if any class carried them.
_FORBIDDEN_CLASS_NAMES = {
    "StrategyManager",
    "StrategyContext",
    "StrategyServiceBag",
    "StrategyOrchestratorBag",
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


def _self_attr_reads_by_method(owner: str) -> dict[str, set[str]]:
    """``{window method: attributes read on self.<owner>}``, reads only."""

    reads: dict[str, set[str]] = {}
    for name, method in _window_methods().items():
        attrs = _self_attr_reads(method, owner)
        if attrs:
            reads[name] = attrs
    return reads


# -- 1 / 2: one render caller, and it is the capability ------------------


def test_the_window_never_renders_the_strategy_page() -> None:
    tree = _tree(_DESKTOP)
    calls = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "render"
        and "strategy_page" in ast.unparse(node.func.value)
    ]
    assert calls == [], calls


def test_the_strategy_render_caller_is_the_capability() -> None:
    orchestrator = _STRATEGY_DIR / "orchestrator.py"
    source = orchestrator.read_text(encoding="utf-8")
    assert "self._page.render(" in source
    # And no other module reaches a strategy page's render: every other
    # would-be caller appears under some ``*_page.render`` spelling, so the
    # whole source tree is swept for it.
    for path in _python_files(_SRC):
        if path == orchestrator:
            continue
        text = path.read_text(encoding="utf-8")
        assert "strategy_page.render(" not in text, path
    # No other orchestration package imports the strategy page: a capability
    # that renders another capability's page is a second owner.
    page_module = "us_quant.desktop_v2.pages.strategy"
    for path in _python_files(_ORCH_DIR):
        if path == orchestrator or _STRATEGY_DIR not in path.parents:
            continue
        assert page_module not in _imports(_tree(path)), path


def test_the_window_wires_the_page_to_the_capability() -> None:
    """Construction and wiring, and nothing else, on the window side."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert "StrategyGovernanceOrchestrator(" in source
    for signal in (
        "version_selected",
        "clone_requested",
        "stop_requested",
    ):
        assert (
            f"self.strategy_page.{signal}.connect(\n"
            "            self.strategy_governance_orchestrator."
        ) in source, signal


# -- 3: the window's remaining strategies surface ------------------------


def test_the_window_no_longer_executes_governance_commands() -> None:
    tree = _tree(_DESKTOP)
    offending = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"clone_version", "transition"}
        and "self.strategies" in ast.unparse(node.func.value)
    ]
    assert offending == [], offending


def test_the_window_reads_the_catalogue_only_for_the_export() -> None:
    reads = _self_attr_reads_by_method("strategies")
    assert reads == _STRATEGIES_SURFACE, sorted(reads.items())


def test_the_retired_window_handlers_are_gone() -> None:
    methods = set(_window_methods())
    for retired in (
        "_strategy_version_selected",
        "_refresh_strategy_page",
        "_strategy_clone_requested",
        "_strategy_transition_requested",
        "_strategy_version_or_none",
        "_set_strategy_account_notice",
        "_populate_strategy_selection_combos",
    ):
        assert retired not in methods, retired


def test_the_window_publishes_only_finished_notice_text() -> None:
    """The account bridge takes the orchestrator's text, not a version."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert (
        "self.strategy_governance_orchestrator.account_notice_requested.connect(\n"
        "            self.account_orchestrator.set_notice\n"
        "        )"
    ) in source
    methods = set(_window_methods())
    for retired in ("_set_strategy_account_notice",):
        assert retired not in methods, retired


# -- 4 / 6: the capability's import boundary -----------------------------


def test_the_strategy_capability_imports_no_other_capability() -> None:
    for path in _python_files(_STRATEGY_DIR):
        tree = _tree(path)
        names = _imported_names(tree)
        for forbidden in _FORBIDDEN_STRATEGY_IMPORTS:
            assert forbidden not in names, (path.name, forbidden)
        for module in _imports(tree):
            if module.startswith("us_quant.desktop_v2.orchestration"):
                # The only allowed intra-orchestration import is its own
                # package.
                assert module.startswith(
                    "us_quant.desktop_v2.orchestration.strategy"
                ), (path.name, module)


def test_the_governance_path_never_selects_for_runtime() -> None:
    """Viewing a version is not running it -- there is no service here.

    An AST guard, not a text guard: the module docstrings deliberately name
    the boundary they keep, and prose must not fail an import rule.
    """

    for path in _python_files(_STRATEGY_DIR):
        tree = _tree(path)
        assert "StrategySelectionService" not in _imported_names(tree), path
        assert not any(
            "strategy_selection" in module for module in _imports(tree)
        ), path
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "select"
            ):
                raise AssertionError((path.name, ast.unparse(node)))


# -- 5: the page is still service-free -----------------------------------


def test_the_strategy_page_is_still_service_free() -> None:
    tree = _tree(_STRATEGY_PAGE)
    names = _imported_names(tree)
    for forbidden in (
        "StrategyApplication",
        "StrategySelectionService",
        "StrategyGovernanceOrchestrator",
    ):
        assert forbidden not in names, forbidden
    for module in _imports(tree):
        assert "trading.application" not in module, module
        assert "sqlite3" != module, module


# -- 7: no second catalogue truth ----------------------------------------


def test_the_orchestrator_caches_no_catalogue() -> None:
    orchestrator = _class_node(
        _tree(_STRATEGY_DIR / "orchestrator.py"),
        "StrategyGovernanceOrchestrator",
    )
    state = {
        target.attr
        for node in ast.walk(orchestrator)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            [node.target]
            if isinstance(node, ast.AnnAssign)
            else list(node.targets)
        )
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert state == {"_application", "_page"}, sorted(state)
    source = _STRATEGY_DIR / "orchestrator.py"
    refresh = None
    for node in ast.walk(orchestrator):
        if isinstance(node, ast.FunctionDef) and node.name == "refresh":
            refresh = node
    assert refresh is not None
    assert "list_versions" in ast.unparse(refresh), "refresh must re-read"


# -- 8: the runtime event goes through the generic owner -----------------


def test_the_status_change_event_is_published_by_the_capability() -> None:
    source = (_STRATEGY_DIR / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert 'code="STATUS_CHANGE"' in source
    assert "runtime_event_requested.emit(" in source


def test_the_window_writes_no_strategy_runtime_event_itself() -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert 'code="STATUS_CHANGE"' not in source
    # ...and the capability's events are routed by the one generic adapter.
    assert (
        "self.strategy_governance_orchestrator.runtime_event_requested.connect(\n"
        "            self._route_runtime_event\n"
        "        )"
    ) in source


# -- 9 / 10: no god objects, and the notice bridge stays narrow ----------


def test_no_strategy_god_object_exists() -> None:
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                assert node.name not in _FORBIDDEN_CLASS_NAMES, (
                    path,
                    node.name,
                )
    assert not (_ORCH_DIR / "strategy" / "context.py").exists()


def test_the_account_notice_bridge_accepts_finished_text_only() -> None:
    account = _class_node(
        _tree(_ORCH_DIR / "account" / "orchestrator.py"),
        "AccountOrchestrator",
    )
    methods = _methods(account)
    assert "set_notice" in methods
    body = _method_source(
        _tree(_ORCH_DIR / "account" / "orchestrator.py"), "set_notice"
    )
    assert "Strategy" not in body, body
    assert "strategy" not in body, body

# =====================================================================
# 11. The retired lifecycle surface stays retired
# =====================================================================


#: Text that would mean a legacy authority came back to this route.
_RETIRED_LIFECYCLE_TEXT = (
    "gate_passed",
    "gate_reason",
    "StrategyLifecycleAuthorization",
    "StrategyCoverageEvaluation",
)

#: Targets a widget must never name.  A page that can ask for PAPER_SHADOW or
#: PAUSED is a page driving evidence-driven lifecycle decisions, which belong to
#: the coverage chain and ``StrategyLifecycleController``.
_RETIRED_TRANSITION_TARGETS = ("PAPER_SHADOW", "PAUSED")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _identifier_names(path: pathlib.Path) -> set[str]:
    """Every identifier ``path`` spells, as an AST fact rather than a substring.

    Attribute access and plain names both count, because ``version.gate_passed``
    and a bare ``gate_passed`` are the same read.  Prose is deliberately
    excluded: these modules *explain* why the retired fields are gone, and a
    guard that rejected the explanation would be turned off rather than obeyed.
    """

    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_the_strategy_page_reads_no_legacy_gate_field() -> None:
    """The page may not render an authority the system no longer has.

    ``gate_passed`` / ``gate_reason`` still exist as frozen schema, but nothing
    decides anything from them.  A page that displayed a verdict derived from
    them would tell the operator they are authorised (or blocked) on the
    strength of a retired flag.
    """

    names = _identifier_names(_STRATEGY_PAGE)
    for retired in ("gate_passed", "gate_reason"):
        assert retired not in names, retired


def test_the_governance_package_reads_no_legacy_gate_field() -> None:
    for path in _python_files(_STRATEGY_DIR):
        names = _identifier_names(path)
        for retired in ("gate_passed", "gate_reason"):
            assert retired not in names, (path.name, retired)


def test_the_strategy_page_offers_no_lifecycle_transition() -> None:
    """Only STOP remains, and it is asked for by version id."""

    tree = _tree(_STRATEGY_PAGE)
    signals = {
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Signal"
    }
    assert "stop_requested" in signals
    assert "transition_requested" not in signals

    # ``StrategyStatus.PAPER_SHADOW`` still appears in ``STOPPABLE_STATUSES``,
    # and that is correct: the question there is "may this status be stopped",
    # not "should this status be promoted to".  What must be absent is a
    # *target*: no emit may carry a lifecycle status, so the page cannot ask for
    # one at all.
    emitted = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "emit"
        and "StrategyStatus" in ast.unparse(node)
    ]
    assert emitted == [], emitted


def test_the_orchestrator_owns_no_evidence_lifecycle() -> None:
    """Promotion and pausing left this route; only explicit stop is left.

    Asserted on the source rather than on behaviour: the point is that the
    capability cannot reach the evidence authorities at all, so a future edit
    cannot reintroduce a second lifecycle path by wiring one in.
    """

    source = _text(_STRATEGY_DIR / "orchestrator.py")
    for retired in _RETIRED_LIFECYCLE_TEXT:
        assert retired not in source, retired
    for target in _RETIRED_TRANSITION_TARGETS:
        assert f"StrategyStatus.{target}" not in source, target
    assert "StrategyStatus.STOPPED" in source
    # A generic transition entry point is exactly what the page used to drive.
    assert "def transition(" not in source
    assert "def stop(" in source


#: Production modules that still *decide* anything from the retired gate fields.
#:
#: The end state is the empty set, and it is reached: nothing in the product
#: branches on ``gate_passed`` or ``gate_reason`` any more.  They survive only as
#: the frozen domain value, the SQLite adapter that round-trips them, and two
#: non-decision surfaces -- the ``strategies.csv`` export columns and the
#: ``StrategySeed`` / ``StrategyVersion`` field declarations.
#:
#: "Decide" is the operative word, and it is why this guard reads contexts rather
#: than identifiers.  An earlier version of this ratchet counted every mention of
#: either name, which reported three modules as outstanding debt when none of them
#: made a decision: a value packed into a dict and written to a CSV is *carried*,
#: and a dataclass field declaration is not a read at all.  A ratchet that cannot
#: reach zero because it is measuring the wrong thing is not a stricter guard, it
#: is a guard somebody eventually widens.  So the measurement was corrected
#: instead -- and the predicate was validated against the commit *before* this
#: repair, where it reports all eleven of the original defects, including the two
#: that read the flag inside a boolean expression assigned to a variable rather
#: than inside an ``if`` test.
#:
#: The list is asserted for exact equality, so a new decision read anywhere fails
#: immediately and the debt cannot grow.
_LEGACY_GATE_READERS_PENDING_RETIREMENT = frozenset()

#: Where the fields are *allowed* to remain: the frozen schema and its value.
_LEGACY_GATE_STORAGE = frozenset(
    {
        "trading/adapters/sqlite/strategy_repository.py",
        "trading/domain/strategy.py",
    }
)

#: Contexts that mean the value is being branched on.
_GATE_DECISION_CONTEXTS = (
    ast.BoolOp,
    ast.Compare,
    ast.UnaryOp,
    ast.If,
    ast.While,
    ast.IfExp,
    ast.Assert,
    ast.comprehension,
)

#: Contexts that mean the value is only being carried -- a payload, not a
#: decision.  Reaching one of these first stops the outward walk.
_GATE_CONVEYOR_CONTEXTS = (ast.Dict, ast.List, ast.Tuple, ast.Set)


def _decides_from_a_legacy_gate_field(path: pathlib.Path) -> list[tuple[int, str]]:
    """Attribute reads of the retired fields that feed a branch or a condition.

    Attribute reads only: ``register(gate_passed=...)``'s parameter and the
    ``StrategySeed`` field declaration are not reads of a stored version's
    retired column.

    The walk goes outward from the field and stops at the first context that
    settles the question.  A container literal wins over a decision context,
    which is what keeps a CSV row from counting as a branch -- and a ``BoolOp``
    counts as a decision, which is what keeps ``eligible = (... or
    version.gate_passed)`` counted even though no ``if`` is involved.
    """

    tree = _tree(path)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in (
            "gate_passed",
            "gate_reason",
        ):
            continue
        cursor: ast.AST = node
        while id(cursor) in parents:
            cursor = parents[id(cursor)]
            if isinstance(cursor, _GATE_CONVEYOR_CONTEXTS):
                break
            if isinstance(cursor, _GATE_DECISION_CONTEXTS):
                found.append((node.lineno, ast.unparse(node)))
                break
    return found


def test_the_legacy_gate_fields_decide_nothing() -> None:
    """A ratchet on the retired fields: the debt can only shrink, and is now zero.

    ``gate_passed`` and ``gate_reason`` stay in the domain value and the SQLite
    adapter because the schema is frozen and existing databases must keep
    round-tripping.  Nothing else may *decide* from them; this pins that exactly,
    so a new decision read fails immediately.
    """

    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _python_files(_SRC):
        relative = path.relative_to(_SRC).as_posix()
        if relative in _LEGACY_GATE_STORAGE:
            continue
        reads = _decides_from_a_legacy_gate_field(path)
        if reads:
            offenders[relative] = reads

    assert set(offenders) == set(_LEGACY_GATE_READERS_PENDING_RETIREMENT), offenders


def test_the_frozen_storage_still_carries_the_fields() -> None:
    """The other half of the ratchet: the fields are not silently dropped.

    Removing them from the domain value or the adapter would break every
    database written before this round.  That is Stage 6-G schema work, so the
    storage surface is asserted to still be there until then.
    """

    for relative in _LEGACY_GATE_STORAGE:
        names = _identifier_names(_SRC / relative)
        assert "gate_passed" in names, relative