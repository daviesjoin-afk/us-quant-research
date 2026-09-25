"""Paper autonomy v1-A: the boundaries the control plane has to keep.

The control plane owns exactly one fact -- what the operator asked the system to
do -- and every guard here exists because it is easy, while adding automation,
to let a second fact in through the same door.  A persisted copy of the Paper
session phase, the active candidate list or the selected strategy would all look
harmless at the moment they were written and would all be wrong by the time an
unattended process read them.

The guards reuse the Final Architecture Closure AST helpers rather than
re-implementing a second import reader.  That is deliberate: FAC documents one
relative-import resolver for the whole repository precisely because two of them
drift, and a second copy here would be the third reading of what an import
names.  It also means the new modules are checked by the *same* rules that
already cover ``trading/domain``, ``trading/ports`` and ``trading/application``
-- which is what makes the last guard below a real assertion rather than a
promise.

Nothing here asserts on a substring of the tree.  Where this file needs a fact
about the source it parses it with ``ast``, and where it needs a fact about the
value it reads ``dataclasses.fields`` or the enum members.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timezone
import inspect
import pathlib
import sqlite3

import pytest

from test_final_architecture_closure import (
    _imported_modules,
    _inward_violations,
    _layer_modules,
    _module_name,
    _violations,
)

from us_quant.trading.adapters.sqlite.paper_autonomy_repository import (
    SQLitePaperAutonomyRepository,
)
from us_quant.trading.application.paper_autonomy import (
    PaperAutonomyApplication,
    PaperAutonomyRefused,
)
from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
    PaperAutonomyIntent,
    PaperAutonomyMode,
    PaperAutonomyViolation,
    initial_intent,
)
from us_quant.trading.ports.paper_autonomy_repository import (
    PaperAutonomyConflict,
    PaperAutonomyRepositoryPort,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"

_DOMAIN_MODULE = "trading/domain/paper_autonomy.py"
_PORT_MODULE = "trading/ports/paper_autonomy_repository.py"
_APPLICATION_MODULE = "trading/application/paper_autonomy.py"

#: The three modules of the control plane itself, in layer order.
_CONTROL_PLANE_MODULES = (_DOMAIN_MODULE, _PORT_MODULE, _APPLICATION_MODULE)

#: A fixed instant, so the audit timestamps are deterministic without any test
#: reaching for the wall clock.
_FIXED_NOW = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def store(tmp_path: pathlib.Path) -> SQLitePaperAutonomyRepository:
    return SQLitePaperAutonomyRepository(tmp_path / "paper_autonomy.sqlite3")


@pytest.fixture
def application(
    store: SQLitePaperAutonomyRepository,
) -> PaperAutonomyApplication:
    return PaperAutonomyApplication(store, clock=lambda: _FIXED_NOW)


# =====================================================================
# 1-4. Import graph
# =====================================================================


def test_pa1_the_domain_module_names_nothing_outside_the_domain() -> None:
    """The intent vocabulary is a value module or it is not safe to persist.

    Checked against the same prefix set the FAC guards use, so Qt, the desktop
    package, the adapters, the composition root, the broker vendor module and
    the storage engine are all named and all refused.
    """

    offending = _violations(
        _DOMAIN_MODULE,
        (
            "PySide6",
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.ibkr",
            "us_quant.broker",
            "sqlite3",
            "ibapi",
        ),
    )
    assert offending == []

    # And the module is not merely empty -- a guard over a deleted vocabulary
    # would pass while proving nothing.
    tree = ast.parse((_SRC / _DOMAIN_MODULE).read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    assert {
        "PaperAutonomyMode",
        "PaperAutonomyIntent",
        "PaperAutonomyViolation",
        "initial_intent",
    } <= defined


def test_pa2_the_port_does_not_name_a_concrete_store() -> None:
    """A port that knows its adapter is not a seam."""

    offending = _violations(
        _PORT_MODULE,
        (
            "sqlite3",
            "us_quant.sqlite_support",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
        ),
    )
    assert offending == []

    methods = _class_methods(_SRC / _PORT_MODULE)
    assert "PaperAutonomyRepositoryPort" in methods
    assert {
        "load_intent",
        "compare_and_swap_intent",
        "append_event",
        "recent_events",
    } <= methods["PaperAutonomyRepositoryPort"]


def test_pa3_the_application_depends_on_the_domain_and_the_ports_only() -> None:
    """No runtime, no adapter, no composition, no broker, no store."""

    offending = _violations(
        _APPLICATION_MODULE,
        (
            "us_quant.trading.runtime",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.ibkr",
            "us_quant.broker",
            "PySide6",
            "sqlite3",
            "ibapi",
        ),
    )
    assert offending == []

    # The reachable ``us_quant`` surface is exactly the two layers below it.
    reachable = {
        target
        for target in _imported_modules(_SRC / _APPLICATION_MODULE)
        if target.startswith("us_quant")
    }
    assert reachable == {
        "us_quant.trading.domain.paper_autonomy",
        "us_quant.trading.ports.paper_autonomy_repository",
    }


def test_pa4_the_application_names_no_paper_lifecycle_or_execution_authority() -> None:
    """The control plane may not hold, or reach, a runtime fact.

    A package-prefix rule decides *direction*; this decides *names*.  The
    application layer is allowed to name ``us_quant.trading.ports``, and
    ``BrokerExecutionPort`` lives there -- so without a symbol rule the control
    plane could reach the execution seam, stay "architecturally correct", and
    become a second route to an order.
    """

    symbols = _imported_symbols(_SRC / _APPLICATION_MODULE)
    for forbidden in (
        "PaperWorkflowController",
        "PaperWorkflowPhase",
        "PaperOrchestrator",
        "ExecutionOrchestrator",
        "ExecutionLeaseManager",
        "ExecutionLease",
        "BrokerExecutionPort",
        "IBKRExecutionAdapter",
        "RiskApplication",
        "ExecutionApplication",
        "OrderRepositoryPort",
        "OrderIntent",
        "StrategySelectionService",
        "StrategyVersion",
    ):
        assert forbidden not in symbols, forbidden


# =====================================================================
# 5. One persistence adapter
# =====================================================================


def test_pa5_the_sqlite_store_is_the_only_intent_persistence(
    store: SQLitePaperAutonomyRepository,
) -> None:
    """Exactly one class in the tree implements the intent store.

    Structural rather than textual: the check is "how many classes define the
    compare-and-swap", not "how many files contain a word".  A second store --
    a JSON sidecar, a settings key, an in-memory singleton presented as durable
    -- would have to define that method to *be* a store, and then this fails.
    """

    implementations: list[str] = []
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        protocols = _protocol_class_names(path)
        for name, methods in _class_methods(path).items():
            if "compare_and_swap_intent" in methods and name not in protocols:
                implementations.append(f"{_module_name(path)}::{name}")

    assert implementations == [
        "us_quant.trading.adapters.sqlite.paper_autonomy_repository::"
        "SQLitePaperAutonomyRepository"
    ]

    # And the concrete store satisfies the port it claims to implement.
    assert isinstance(store, PaperAutonomyRepositoryPort)


# =====================================================================
# 6-7. The value carries no runtime truth, and there is one authority
# =====================================================================


def test_pa6_the_intent_carries_no_runtime_truth() -> None:
    """The persisted value is an operator decision, field for field.

    An exact set rather than a denylist: a new field has to be argued for here,
    which is the point.  Every name kept describes the operator's *intent*, so
    none of them can disagree with ``PaperWorkflowController``, the lease
    manager, the broker or the strategy selection -- none of them describes
    anything those owners know.
    """

    assert {field.name for field in fields(PaperAutonomyIntent)} == {
        "revision",
        "mode",
        "kill_switch_latched",
        "updated_at",
        "reason",
    }

    # The mode vocabulary is an intent vocabulary.  The runtime phases below
    # belong to the Paper capability and must not be readable from this store.
    modes = {mode.value for mode in PaperAutonomyMode}
    assert modes == {"disabled", "enabled", "paused"}
    for phase in (
        "running",
        "preparing",
        "ready",
        "halted",
        "finalized",
        "reconciling",
        "reconciling_ready",
    ):
        assert phase not in modes


def test_pa7_no_second_autonomy_authority_was_introduced() -> None:
    """One write authority, and no aggregate manager above it.

    Modelled on the FAC guards for the Live boundary: a fork renamed
    ``LiveAutonomy`` or ``TradingAutonomy`` is the same second authority as one
    named ``AutonomyManager``, so the rule is a shape rule on the class name
    rather than a list of spellings someone has to keep extending.
    """

    banned = {
        "LiveAutonomy",
        "TradingAutonomy",
        "AutonomyManager",
        "GlobalAutonomyManager",
        "GlobalSupervisorContext",
        "ServiceBag",
        "AutomationContext",
        "TradingManager",
    }
    authorities: list[str] = []
    autonomy_classes: list[str] = []
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for name in _class_methods(path):
            assert name not in banned, f"{_module_name(path)}::{name}"
            if "Autonomy" in name:
                autonomy_classes.append(f"{_module_name(path)}::{name}")
                if name == "PaperAutonomyApplication":
                    authorities.append(f"{_module_name(path)}::{name}")

    # Exactly one definition of the write authority, in its own module.
    assert authorities == [
        "us_quant.trading.application.paper_autonomy::PaperAutonomyApplication"
    ]

    # Every autonomy class is a Paper autonomy class or a store for one.  A
    # ``Live*`` or ``Trading*`` spelling fails here without anyone having to
    # remember to add its name to a list.
    for qualified in autonomy_classes:
        name = qualified.rsplit("::", 1)[1]
        assert name.startswith(
            ("PaperAutonomy", "SQLitePaperAutonomy")
        ), qualified


def test_pa7b_the_control_plane_has_no_environment_or_broker_mode() -> None:
    """The Paper-only boundary is a missing field, not a checked one.

    A ``mode = PAPER|LIVE`` field, an ``environment`` argument or a
    ``broker_mode`` flag would each let a future Live path inherit this
    authorisation by configuration drift.  There must be nothing to widen.
    """

    for relative in _CONTROL_PLANE_MODULES:
        tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"))
        offending = [
            f"{relative}:{node.lineno}:{node.id}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id.casefold() in {"environment", "broker_mode"}
        ]
        offending += [
            f"{relative}:{node.lineno}:{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr.casefold() in {"environment", "broker_mode"}
        ]
        assert offending == [], offending


# =====================================================================
# 8-10. The safety properties, asserted on real objects
# =====================================================================


def test_pa8_the_kill_switch_cannot_be_bypassed(
    store: SQLitePaperAutonomyRepository,
    application: PaperAutonomyApplication,
) -> None:
    """Three facts, one promise: clearing a latch never resumes trading.

    The forbidden combination is refused by the value type, so it cannot be
    stored either; ``enable`` refuses while the latch is set, so the promise is
    not merely "the mode is disabled" but "no transition here re-enables"; and
    ``clear_kill_switch`` leaves the mode exactly where the kill put it.
    """

    enabled = application.enable(INITIAL_REVISION, "operator authorises autonomy")
    assert enabled.allows_autonomous_work is True

    latched = application.engage_kill_switch(enabled.revision, "operator kill")
    assert latched.kill_switch_latched is True
    assert latched.mode is PaperAutonomyMode.DISABLED
    assert latched.allows_autonomous_work is False

    # The forbidden value cannot be constructed, which is what makes the latch
    # a property of the type rather than a rule somebody has to remember.
    with pytest.raises(PaperAutonomyViolation):
        PaperAutonomyIntent(
            revision=latched.revision,
            mode=PaperAutonomyMode.ENABLED,
            kill_switch_latched=True,
            updated_at=latched.updated_at,
            reason="a latch that promises not to resume",
        )

    with pytest.raises(PaperAutonomyRefused):
        application.enable(latched.revision, "try to bypass the kill switch")
    assert application.snapshot() == latched

    cleared = application.clear_kill_switch(
        latched.revision, "operator clears the latch"
    )
    assert cleared.kill_switch_latched is False
    assert cleared.mode is PaperAutonomyMode.DISABLED
    assert cleared.allows_autonomous_work is False

    # Re-enabling is a second, explicit decision -- and it is the only route.
    re_enabled = application.enable(cleared.revision, "operator re-enables")
    assert re_enabled.mode is PaperAutonomyMode.ENABLED

    # Four accepted transitions, four events: the refusals above wrote nothing.
    assert [event.revision for event in store.recent_events(10)] == [1, 2, 3, 4]


def test_pa9_a_missing_row_is_disabled_and_never_enabled(
    store: SQLitePaperAutonomyRepository,
    application: PaperAutonomyApplication,
) -> None:
    """The default has to point away from trading."""

    assert store.load_intent() == initial_intent()
    assert store.load_intent().mode is PaperAutonomyMode.DISABLED
    assert store.load_intent().kill_switch_latched is False
    assert store.load_intent().revision == INITIAL_REVISION

    # And it stays that way if the row is destroyed underneath the store.  An
    # absent record is indistinguishable from one that was never written, and
    # that is the point: the *same* "nothing was authorised" answer comes back,
    # never a mode the kill switch had latched away.
    application.enable(INITIAL_REVISION, "operator authorises autonomy")
    _execute(store.path, "DELETE FROM paper_autonomy_intent")

    assert store.load_intent().mode is PaperAutonomyMode.DISABLED
    assert store.load_intent().allows_autonomous_work is False

    # A writer that had seen the (now deleted) revision 1 cannot write over the
    # absence, and the only write the store accepts is a first write against
    # the initial revision -- which is an explicit operator decision, not a
    # consequence of the row having gone missing.
    with pytest.raises(PaperAutonomyConflict):
        application.enable(1, "a stale surface authorises autonomy")

    assert store.load_intent().allows_autonomous_work is False


def test_pa10_the_intent_write_is_a_compare_and_swap(
    store: SQLitePaperAutonomyRepository,
    application: PaperAutonomyApplication,
) -> None:
    """A stale writer is refused, not absorbed."""

    first = application.enable(INITIAL_REVISION, "operator authorises autonomy")

    # A second surface that still believes revision 0 must not overwrite the
    # state it never saw.
    with pytest.raises(PaperAutonomyConflict):
        application.disable(INITIAL_REVISION, "stale surface withdraws")

    assert store.load_intent() == first
    assert len(store.recent_events(10)) == 1

    # The port's contract names the expectation, so an adapter cannot satisfy
    # this protocol by last-write-wins.
    signature = inspect.signature(
        PaperAutonomyRepositoryPort.compare_and_swap_intent
    )
    assert "expected_revision" in signature.parameters


# =====================================================================
# 11. The FAC guards actually see the new modules
# =====================================================================


@pytest.mark.parametrize(
    "layer, expected",
    (
        ("trading/domain", "us_quant.trading.domain.paper_autonomy"),
        (
            "trading/ports",
            "us_quant.trading.ports.paper_autonomy_repository",
        ),
        (
            "trading/application",
            "us_quant.trading.application.paper_autonomy",
        ),
    ),
)
def test_pa11_the_new_modules_are_inside_the_guarded_layers(
    layer: str, expected: str
) -> None:
    """FAC's allowlist covers these files, and they satisfy it.

    This is the check that the boundary work above is not self-reported: the
    modules are discovered by the shared FAC discovery, and the FAC layer rule
    -- evaluated on the tree as it is now -- passes over them.
    """

    assert expected in {_module_name(path) for path in _layer_modules(layer)}
    assert _inward_violations(layer) == []


# =====================================================================
# Helpers
# =====================================================================


def _class_methods(path: pathlib.Path) -> dict[str, set[str]]:
    """Every class in ``path``, mapped to the method names it defines."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _imported_symbols(path: pathlib.Path) -> set[str]:
    """Every name an ``ImportFrom`` in ``path`` binds."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _protocol_class_names(path: pathlib.Path) -> set[str]:
    """The classes in ``path`` that derive from ``typing.Protocol``.

    A protocol *declares* the store's methods without implementing anything, so
    "how many classes define the compare-and-swap" has to exclude it -- the
    port is the shape, not a second store.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            (isinstance(base, ast.Name) and base.id == "Protocol")
            or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
            for base in node.bases
        )
    }


def _execute(path: pathlib.Path, statement: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
