"""v1-B foundation guards: the boundaries the control core has to keep.

The supervisor application, the desktop host and the launch-authorization wiring
do not exist yet on this branch, so this file guards what does: the vocabulary,
the four seams and the action ledger.  Each guard is written against a *fact*
rather than a wording, and each names the thing it is protecting against, so a
later slice cannot quietly widen a boundary this one closed.

The module-level import rules are shared with the Final Architecture Closure
helpers rather than re-implemented: the new modules live inside
``trading/domain`` and ``trading/ports``, so FAC already checks them, and a
second import reader here would be a second opinion about what an import names.
"""

from __future__ import annotations

import ast
from dataclasses import fields
import pathlib
import re

import pytest

from test_final_architecture_closure import (
    _imported_modules,
    _inward_violations,
    _module_name,
    _violations,
)

from us_quant.trading.domain.paper_autonomy import PaperAutonomyIntent
from us_quant.trading.domain.paper_autonomy_supervisor import (
    AUTONOMOUS_PREPARE_WINDOWS,
    AUTONOMOUS_START_WINDOWS,
    PaperAutonomyAction,
    PaperAutonomyActionStatus,
    PaperAutonomyRuntimeFacts,
    PaperAutonomyScheduleFacts,
    PaperAutonomySessionProvenance,
    PaperAutonomySessionWindow,
    PaperAutonomySupervisorError,
    PaperAutonomySupervisorViolation,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
)
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyExecutorPort,
    PaperAutonomyPreparationRequest,
    PaperAutonomyRequestOutcome,
    PaperAutonomyRuntimeFactsPort,
    PaperAutonomySchedulePort,
    PaperAutonomyStartupFactsPort,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"

_SUPERVISOR_DOMAIN = "trading/domain/paper_autonomy_supervisor.py"
_SUPERVISOR_PORTS = "trading/ports/paper_autonomy_supervisor.py"
_SUPERVISOR_APPLICATION = "trading/application/paper_autonomy_supervisor.py"
_ACTION_PORT = "trading/ports/paper_autonomy_action_repository.py"
_ACTION_ADAPTER = (
    "trading/adapters/sqlite/paper_autonomy_action_repository.py"
)


def _class_methods(path: pathlib.Path) -> dict[str, set[str]]:
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


# =====================================================================
# A-C. Import direction
# =====================================================================


def test_b_a1_the_supervisor_vocabulary_is_a_value_module() -> None:
    """A. Qt, the desktop, adapters, the broker, risk and execution are all out.

    The vocabulary is what a future scheduler decides with, so it must not be
    able to reach a component that could act: a rule expressed here and an
    effect performed here would be the same object again.
    """

    offending = _violations(
        _SUPERVISOR_DOMAIN,
        (
            "PySide6",
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.trading.runtime",
            "us_quant.trading.application",
            "us_quant.ibkr",
            "us_quant.broker",
            "sqlite3",
            "ibapi",
        ),
    )
    assert offending == []

    # And it is not empty -- a guard over a deleted vocabulary proves nothing.
    defined = {
        node.name
        for node in ast.walk(
            ast.parse((_SRC / _SUPERVISOR_DOMAIN).read_text(encoding="utf-8"))
        )
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    assert {
        "PaperAutonomyPolicy",
        "PaperAutonomyRuntimeFacts",
        "PaperAutonomyScheduleFacts",
        "PaperAutonomySessionProvenance",
        "PaperAutonomyDecision",
        "decide_paper_autonomy",
    } <= defined


def test_b_a2_the_supervisor_ports_name_no_concrete_counterpart() -> None:
    """B. A port that knows its adapter is not a seam, and one that reaches the
    runtime names a component it is supposed to be independent of."""

    for relative in (_SUPERVISOR_PORTS, _ACTION_PORT):
        offending = _violations(
            relative,
            (
                "us_quant.trading.adapters",
                "us_quant.trading.composition",
                "us_quant.sqlite_support",
                "us_quant.desktop",
                "us_quant.desktop_v2",
                "sqlite3",
                "PySide6",
                "ibapi",
            ),
        )
        assert offending == [], (relative, offending)


def test_b_a2b_no_port_takes_a_paper_runtime_type() -> None:
    """The runtime-facts seam must not become a second view of the phase.

    Checked as imported symbols rather than as text: the whole reason the facts
    port answers booleans is that the sequencing belongs to the component that
    owns the session, and one import of the phase vocabulary would hand the
    supervisor the ability to re-derive it.
    """

    symbols: set[str] = set()
    for relative in (_SUPERVISOR_PORTS, _SUPERVISOR_DOMAIN, _ACTION_PORT):
        tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                symbols.update(alias.name for alias in node.names)
    for forbidden in (
        "PaperWorkflowPhase",
        "PaperWorkflowController",
        "PaperControlFacts",
        "PaperSessionResult",
        "PaperPresentationSnapshot",
        "ExecutionLease",
        "ExecutionLeaseManager",
        "BrokerExecutionPort",
        "OrderRepositoryPort",
        "RiskApplication",
        "ExecutionApplication",
        "PaperTradingService",
        "PaperOrchestrator",
        "ExecutionOrchestrator",
    ):
        assert forbidden not in symbols, forbidden


@pytest.mark.parametrize(
    "layer, expected",
    (
        ("trading/domain", "us_quant.trading.domain.paper_autonomy_supervisor"),
        ("trading/ports", "us_quant.trading.ports.paper_autonomy_supervisor"),
        (
            "trading/ports",
            "us_quant.trading.ports.paper_autonomy_action_repository",
        ),
    ),
)
def test_b_a3_the_new_modules_are_inside_the_guarded_layers(
    layer: str, expected: str
) -> None:
    """FAC's allowlist covers these files, and they satisfy it.

    This is the check that the boundary work above is not self-reported: the
    modules are discovered by the shared FAC discovery, and the FAC layer rule --
    evaluated on the tree as it now stands -- passes over them.
    """

    names = {
        _module_name(path)
        for path in _layer_modules_of(layer)
    }
    assert expected in names
    assert _inward_violations(layer) == []


def _layer_modules_of(relative: str) -> list[pathlib.Path]:
    from test_final_architecture_closure import _layer_modules

    return _layer_modules(relative)


def test_b_a4_the_action_adapter_is_only_a_store() -> None:
    """C. The ledger adapter interprets nothing but its own rows.

    It may not reach for a queue, a timer, a broker or a capability: it is the
    persistence of what was requested, and every rule about *when* to request
    something lives in the decision function.
    """

    offending = _violations(
        _ACTION_ADAPTER,
        (
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.trading.composition",
            "us_quant.ibkr",
            "PySide6",
            "ibapi",
        ),
    )
    assert offending == []

    methods = _class_methods(_SRC / _ACTION_ADAPTER)
    store = methods["SQLitePaperAutonomyActionRepository"]
    # The write surface, exactly: opening state, accepted request, terminal
    # outcome.  A fourth write is a fourth transition to keep consistent with
    # the decision layer.
    assert {
        "claim",
        "mark_requested",
        "complete",
        "unresolved",
        "recent",
        "start_attempted",
    } <= store


# =====================================================================
# D-I. What the values may carry
# =====================================================================


def test_b_a5_the_action_record_holds_no_system_truth() -> None:
    """D. The ledger records requests, not the state of the world."""

    names = {field.name for field in fields(PaperAutonomyActionRecord)}
    for forbidden in (
        "candidate",
        "candidates",
        "candidate_symbols",
        "strategy",
        "strategy_version_id",
        "phase",
        "positions",
        "orders",
        "broker",
        "portfolio",
        "market_snapshot",
        "lease",
        "session",
    ):
        assert forbidden not in names, forbidden


def test_b_a6_the_runtime_facts_carry_no_phase_and_no_session_type() -> None:
    """E. A phase would let a scheduler re-derive sequencing it must be told."""

    annotations = {
        field.name: str(field.type)
        for field in fields(PaperAutonomyRuntimeFacts)
    }
    for name, annotation in annotations.items():
        assert "PaperWorkflowPhase" not in annotation, name
        assert "PaperSessionResult" not in annotation, name
        assert "PaperControlFacts" not in annotation, name
        assert "PaperPresentationSnapshot" not in annotation, name
        assert "Phase" not in annotation, name


def test_b_a7_provenance_is_an_ephemeral_runtime_fact_only() -> None:
    """F. Provenance describes a session, so it belongs to neither record.

    The intent is what the operator authorised; the ledger is what the scheduler
    asked for.  A persisted provenance would be a claim about a session that
    survives the session -- and after a restart the honest answer is that nobody
    knows, which is the runtime value ``UNKNOWN`` rather than a stored one.
    """

    assert "session_provenance" in {
        field.name for field in fields(PaperAutonomyRuntimeFacts)
    }
    for record in (PaperAutonomyIntent, PaperAutonomyActionRecord):
        assert "session_provenance" not in {
            field.name for field in fields(record)
        }, record.__name__

    # And it is not written down anywhere: the enum is referenced by the
    # vocabulary and the guards, never by a store.
    for relative in (
        "trading/adapters/sqlite/paper_autonomy_repository.py",
        "trading/adapters/sqlite/paper_autonomy_action_repository.py",
    ):
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert "session_provenance" not in source, relative
        assert "PaperAutonomySessionProvenance" not in source, relative


def test_b_a8_only_regular_hours_may_start_and_premarket_may_only_prepare(
) -> None:
    """G. The window limits, exactly as the vocabulary states them.

    Asserted as an exact tuple rather than as "regular is in the set": the claim
    is not that regular is permitted but that nothing else is, and an added
    member has to fail this on purpose.  The session enum is also checked against
    the canonical one, because folding ``MAINTENANCE`` into ``CLOSED`` here would
    be this vocabulary making a session judgement it is not entitled to make.
    """

    from us_quant.extended_hours import USEquitySession

    assert AUTONOMOUS_START_WINDOWS == (PaperAutonomySessionWindow.REGULAR,)
    assert AUTONOMOUS_PREPARE_WINDOWS == (
        PaperAutonomySessionWindow.PREMARKET,
        PaperAutonomySessionWindow.REGULAR,
    )
    assert {member.value for member in PaperAutonomySessionWindow} == {
        member.value for member in USEquitySession
    }


def test_b_a9_no_repairing_action_exists() -> None:
    """H. The manual recovery gate cannot be bypassed by a new enum member."""

    names = {member.name for member in PaperAutonomyAction}
    for forbidden in (
        "AUTO_RECONCILE",
        "CONFIRM_RECONCILIATION",
        "FORCE_FLAT",
        "FORCE_RELEASE",
        "FORCE_RESUME",
        "FORCE_CANCEL_ALL",
        "RELEASE_LEASE",
        "FLATTEN",
    ):
        assert forbidden not in names, forbidden

    # And the executor seam offers exactly five requests, none of them a repair.
    executor = _class_methods(_SRC / _SUPERVISOR_PORTS)["PaperAutonomyExecutorPort"]
    assert executor == {
        "request_prepare",
        "request_start",
        "request_pause",
        "request_resume",
        "request_stop",
    }

    # The reading seams are one method each.  A second method is a wider door,
    # and every one of these exists to answer a single question.
    surfaces = _class_methods(_SRC / _SUPERVISOR_PORTS)
    assert surfaces["PaperAutonomyRuntimeFactsPort"] == {"facts"}
    assert surfaces["PaperAutonomyStartupFactsPort"] == {"startup_facts"}
    assert surfaces["PaperAutonomySchedulePort"] == {"schedule"}
    # The intent is read-only here, and the exact surface is the guard: a
    # scheduler that could call ``enable`` or ``engage_kill_switch`` would hold
    # the authority ``PaperAutonomyApplication`` exists to keep separate from the
    # machinery that acts.
    assert surfaces["PaperAutonomyIntentReaderPort"] == {"snapshot"}


def test_b_a10_the_autonomy_capability_cannot_express_live_authority() -> None:
    """The boundary is this capability's, not a repository-wide naming rule.

    An earlier version of this guard banned a set of future Live class names
    across the whole tree.  That mixed two different invariants and made v1-B a
    standing veto over the route: the roadmap still has a Live-ready Execution
    Core and a small-capital Live canary, and neither may be blocked by a naming
    rule a Paper phase happened to write.  What this capability has to prove is
    narrower and checkable -- *it* cannot describe live or real-money authority.

    The Risk and Execution fork rules are deliberately **not** restated here.
    FAC's ``test_fa19b_a_future_live_path_must_reuse_the_single_authority_stack``
    already owns them, and one invariant has one owner.
    """

    modules = _autonomy_module_paths(_SRC)
    assert modules, "the autonomy modules must exist"

    offending: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        label = path.name
        offending += [f"{label}: arg/name {name}" for name in _authority_widening(tree)]
        offending += [
            f"{label}: enum {member}" for member in _enum_authority_members(tree)
        ]
        offending += [
            f"{label}: definition {name}" for name in _live_definitions(tree)
        ]
        offending += [
            f"{label}: method {name}" for name in _forbidden_public_methods(tree)
        ]
    assert offending == []

    # Provenance answers "which session", never "which environment".
    assert {member.name for member in PaperAutonomySessionProvenance} == {
        "NONE",
        "AUTONOMOUS",
        "MANUAL",
        "UNKNOWN",
    }

    # The action vocabulary is an exact set, so a live action has to fail this
    # rather than arrive as one more member.
    assert {member.value for member in PaperAutonomyAction} == {
        "noop",
        "prepare",
        "start",
        "pause_entries",
        "resume_entries",
        "stop",
        "blocked_requires_operator",
    }


#: Identifiers that would widen this capability's authority beyond Paper.
#:
#: An exact vocabulary rather than a substring search: ``liveness`` and
#: ``delivery`` are legitimate words, and a rule that banned the substring
#: ``live`` would reject them while still missing a spelling nobody thought of.
#: The list is not meant to grow without limit -- the structural protection is
#: the enum and port checks below, which cannot be spelled around.
_AUTHORITY_WIDENING_NAMES = frozenset(
    {
        "account_mode",
        "broker_mode",
        "environment",
        "execution_environment",
        "live_account",
        "live_enabled",
        "live_mode",
        "real_money",
        "real_money_enabled",
        "trading_environment",
    }
)

#: Enum member *values* that would describe live authority.  The member *names*
#: are judged by the same whole-word rule as definition names, so ``LIVE``,
#: ``live``, ``PAPER_LIVE`` and ``RealMoney`` are all one case rather than four
#: spellings somebody has to keep adding.
_AUTHORITY_WIDENING_VALUES = frozenset({"live", "real_money"})

#: Public method spellings that would offer a live or environment-scoped request.
_FORBIDDEN_METHOD_PREFIXES = (
    "enable_live",
    "request_live",
    "set_environment",
    "start_live",
)

#: Whole words in a definition name that announce a live authority, and the one
#: adjacent pair that does.  Whole words because the alternative is worse in both
#: directions: ``liveness`` and ``delivery`` must survive, and ``build_live_autonomy``
#: and ``LivePaperAutonomyHost`` must not.
_LIVE_WORDS = frozenset({"live"})
_LIVE_SEQUENCES = (("real", "money"),)

_WORD_BOUNDARY = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _autonomy_module_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Every module of the Paper autonomy capability under ``root``.

    Two subtrees, discovered rather than listed:

    * the trading-side modules, matched by name -- domain, ports, application,
      adapters and composition all live under ``trading``;
    * the desktop autonomy capability, matched by package path, because a host
      there is legitimately called ``host.py`` and no filename pattern would find
      it.

    A root that does not have the desktop subtree yields a list without it rather
    than failing, so this runs before the host exists and covers it the moment it
    does.  The scan is deliberately **not** over the whole source tree: the claim
    is about this capability, not about the repository, and a global scan is how
    a Paper phase ends up vetoing a future Live one.
    """

    trading_root = root / "trading"
    trading = (
        list(trading_root.rglob("paper_autonomy*.py"))
        if trading_root.exists()
        else []
    )
    autonomy_root = root / "desktop_v2" / "orchestration" / "autonomy"
    desktop = list(autonomy_root.rglob("*.py")) if autonomy_root.exists() else []
    return sorted(
        path
        for path in {*trading, *desktop}
        if "__pycache__" not in path.parts
    )


def _identifier_name(node: ast.AST) -> str | None:
    """The authority-relevant identifier ``node`` spells, if it spells one.

    ``ast.arg`` is here because a function parameter is exactly where an
    environment or a real-money flag would arrive: checking only ``Name`` and
    ``Attribute`` would let ``def build(environment: str)`` through untouched.
    Definition names are here too, since an authority is as likely to arrive as
    ``build_live_autonomy`` as it is as a field.
    """

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    return None


def _authority_widening(tree: ast.AST) -> list[str]:
    """Every identifier in ``tree`` that names a wider authority."""

    found: list[str] = []
    for node in ast.walk(tree):
        name = _identifier_name(node)
        if name is not None and name.casefold() in _AUTHORITY_WIDENING_NAMES:
            found.append(name)
    return found


def _name_words(name: str) -> list[str]:
    """``name`` as lowercase whole words, camel case included."""

    return [
        word.casefold() for word in _WORD_BOUNDARY.split(name) if word
    ]


def _announces_live_authority(name: str) -> bool:
    """Whether ``name`` announces a live authority, as whole words.

    Deliberately not a substring rule.  ``liveness`` and ``delivery`` contain the
    letters of ``live`` and mean nothing of the sort, while
    ``build_live_autonomy``, ``LivePaperAutonomyHost`` and ``PAPER_LIVE`` are
    exactly what this is for -- and a split on word boundaries, camel case
    included, separates the two cases that a naive ``"live" in name`` cannot.

    One helper for definition names and enum member names both: they are the same
    question asked of two node kinds, and two copies is how one of them ends up
    stricter than the other.
    """

    words = _name_words(name)
    if _LIVE_WORDS.intersection(words):
        return True
    return any(
        words[index] == first and words[index + 1] == second
        for first, second in _LIVE_SEQUENCES
        for index in range(len(words) - 1)
    )


def _live_definitions(tree: ast.AST) -> list[str]:
    """Definition names that announce a live authority."""

    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and _announces_live_authority(node.name)
    ]


def _enum_member_assignment(node: ast.AST) -> tuple[str, ast.expr | None] | None:
    """The name and value of an enum member assignment, annotated or not.

    ``LIVE = "paper"`` is an ``ast.Assign`` and ``LIVE: str = "paper"`` is an
    ``ast.AnnAssign``.  An earlier version of this detector knew only the first,
    so the annotated spelling -- which is the one a typed codebase actually
    writes -- walked straight past it, and the guard reported success over a hole
    it had been asked to close.
    """

    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return (target.id, node.value)
        return None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id, node.value)
    return None


def _enum_authority_members(tree: ast.AST) -> list[str]:
    """Enum member names and values in ``tree`` that describe live authority.

    Only enum members are inspected, and that is the point: prose in this
    capability legitimately discusses the Live route it must never implement, so
    a text search would reject the documentation.  An enum member cannot be
    prose -- it is an authority the code can express.
    """

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(base) for base in node.bases}
        if not any(base.endswith("Enum") for base in bases):
            continue
        for member in node.body:
            assignment = _enum_member_assignment(member)
            if assignment is None:
                continue
            name, value = assignment
            if _announces_live_authority(name):
                found.append(f"{node.name}.{name}")
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.casefold() in _AUTHORITY_WIDENING_VALUES
            ):
                found.append(f"{node.name}.{name} = {value.value!r}")
    return found


def _forbidden_public_methods(tree: ast.AST) -> list[str]:
    """Method names in ``tree`` that would offer a live request."""

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lowered = node.name.casefold()
        if lowered.startswith(_FORBIDDEN_METHOD_PREFIXES):
            found.append(node.name)
    return found


# =====================================================================
# The detector itself, and the discovery contract
# =====================================================================


@pytest.mark.parametrize(
    "label, source",
    (
        (
            "a function argument named environment",
            "def build(environment: str) -> None: ...",
        ),
        (
            "a function argument named real_money",
            "def start(real_money: bool) -> None: ...",
        ),
        (
            "an annotated method argument named broker_mode",
            "class X:\n    def run(self, broker_mode: str) -> None: ...",
        ),
        (
            "an attribute named live_mode",
            "def read(settings: object) -> object:\n    return settings.live_mode",
        ),
        (
            # The value is deliberately innocuous, so only the *member name*
            # check can catch this one.  A fixture that used ``LIVE = 'live'``
            # would be caught by the value rule as well, and a mutant removing
            # the name rule would survive -- measured, not assumed.
            "an enum member named LIVE",
            "class Mode(StrEnum):\n    PAPER = 'paper'\n    LIVE = 'paper'",
        ),
        (
            "an annotated enum member named LIVE",
            "class Mode(StrEnum):\n    PAPER: str = 'paper'\n    LIVE: str = 'paper'",
        ),
        (
            "a lowercase enum member",
            "class Mode(StrEnum):\n    live = 'paper'",
        ),
        (
            "an enum member with live as a suffix",
            "class Mode(StrEnum):\n    PAPER_LIVE = 'paper'",
        ),
        (
            "an enum member with live as a prefix",
            "class Mode(StrEnum):\n    LIVE_PAPER = 'paper'",
        ),
        (
            "an annotated camel-case enum member",
            "class Mode(StrEnum):\n    RealMoney: bool = False",
        ),
        (
            "an enum member whose value is real_money",
            "class Mode(StrEnum):\n    PAPER = 'paper'\n    SOLVENT = 'real_money'",
        ),
        (
            "a definition that announces a live authority",
            "def build_live_autonomy() -> None: ...",
        ),
        (
            "a class that announces a live authority",
            "class LivePaperAutonomyHost:\n    pass",
        ),
        (
            "a method that would request a live launch",
            "class X:\n    def request_live_start(self) -> None: ...",
        ),
    ),
)
def test_b_a13_the_detector_catches_every_shape(label: str, source: str) -> None:
    """The detector is exercised on its own, through the same functions the tree
    check uses.

    One parser, two callers.  A fixture copy of the detection would prove only
    that the copy works, and the thing that actually needs proving is that the
    guard reading the production tree would notice a widening -- including the
    shapes an earlier version missed entirely, where the authority arrives as a
    *parameter* rather than as a field.
    """

    tree = ast.parse(source)
    found = (
        _authority_widening(tree)
        + _enum_authority_members(tree)
        + _live_definitions(tree)
        + _forbidden_public_methods(tree)
    )
    assert found, label


@pytest.mark.parametrize(
    "label, source",
    (
        (
            "a provenance field",
            "class Facts:\n    session_provenance: str",
        ),
        (
            "a legal enum",
            "class Mode(StrEnum):\n    PAPER_AUTONOMOUS = 'autonomous'\n"
            "    MANUAL = 'manual'\n    NONE = 'none'",
        ),
        (
            "words that merely contain live",
            "def check(liveness: str, delivery: str) -> None: ...",
        ),
        (
            "an enum whose members merely contain live",
            "class Mode(StrEnum):\n    DELIVERY = 'paper'\n"
            "    LIVENESS = 'paper'",
        ),
        (
            "a definition about Paper autonomy",
            "def build_paper_autonomy_supervisor() -> None: ...",
        ),
    ),
)
def test_b_a13b_the_detector_leaves_legitimate_names_alone(
    label: str, source: str
) -> None:
    """The other half of a detector, and the half that costs more to get wrong.

    ``liveness`` and ``delivery`` contain the letters of ``live``; a guard that
    flagged them would be turned off within a week, and a turned-off guard
    protects nothing.  So the rule is whole words, and it is asserted.
    """

    tree = ast.parse(source)
    found = (
        _authority_widening(tree)
        + _enum_authority_members(tree)
        + _live_definitions(tree)
        + _forbidden_public_methods(tree)
    )
    assert found == [], label


def test_b_a14_discovery_covers_both_subtrees_and_nothing_else(
    tmp_path: pathlib.Path,
) -> None:
    """The coverage contract, checked without inventing a production file.

    Both halves have to be found -- a trading-side module by name, and a desktop
    host by package path, because ``host.py`` matches no filename pattern.  And
    a sibling tree that has nothing to do with this capability has to stay
    unscanned: the guard is about Paper autonomy, not about the repository, and
    the difference is the whole reason it is scoped this way.
    """

    root = tmp_path / "us_quant"
    (root / "trading" / "application").mkdir(parents=True)
    (
        root / "trading" / "application" / "paper_autonomy_supervisor.py"
    ).write_text("", encoding="utf-8")
    (root / "desktop_v2" / "orchestration" / "autonomy").mkdir(parents=True)
    (root / "desktop_v2" / "orchestration" / "autonomy" / "host.py").write_text(
        "", encoding="utf-8"
    )
    (root / "trading" / "live").mkdir(parents=True)
    (root / "trading" / "live" / "canary.py").write_text("", encoding="utf-8")

    discovered = {path.name for path in _autonomy_module_paths(root)}
    assert discovered == {"paper_autonomy_supervisor.py", "host.py"}


def test_b_a14b_discovery_tolerates_a_missing_desktop_subtree(
    tmp_path: pathlib.Path,
) -> None:
    """The guard has to run before the host exists.

    An earlier version of the naming rule claimed "the desktop host is covered
    the moment it is written" while matching only filenames beginning
    ``paper_autonomy`` -- so a host at ``autonomy/host.py`` would have been
    invisible, and the claim would have been false.  This is the regression that
    keeps the claim honest, and it runs on a tree with no desktop subtree at all.
    """

    root = tmp_path / "us_quant"
    (root / "trading" / "domain").mkdir(parents=True)
    (root / "trading" / "domain" / "paper_autonomy.py").write_text(
        "", encoding="utf-8"
    )

    assert {path.name for path in _autonomy_module_paths(root)} == {
        "paper_autonomy.py"
    }


def test_b_a11_the_supervisor_errors_share_the_feature_root() -> None:
    """One catchable root, so a caller can fail closed in one clause.

    A supervisor runs unattended; every failure it can meet has to be reachable
    from a single ``except`` or the one caller that forgot will be the one
    running unsupervised.
    """

    from us_quant.trading.domain.paper_autonomy import PaperAutonomyError

    assert issubclass(
        PaperAutonomySupervisorViolation, PaperAutonomySupervisorError
    )
    assert issubclass(PaperAutonomySupervisorError, PaperAutonomyError)


def test_b_a12_the_executor_seam_cannot_express_completion() -> None:
    """A request's outcome is not the action's outcome.

    ``PaperAutonomyRequestOutcome`` carries only whether the owner admitted the
    request.  A field a caller could read as "started" would let the scheduler
    record a success the canonical publication has not reported yet.
    """

    names = {field.name for field in fields(PaperAutonomyRequestOutcome)}
    assert names == {"accepted", "detail"}

    for forbidden in (
        "completed",
        "succeeded",
        "running",
        "session",
        "result",
        "phase",
    ):
        assert forbidden not in names, forbidden

    preparation = {field.name for field in fields(PaperAutonomyPreparationRequest)}
    assert preparation == {"candidate_limit", "capital_limit"}


def test_b_a15_the_supervisor_reaches_no_authority_and_no_toolkit() -> None:
    """The tick core may name the vocabulary, the seams and nothing else.

    Checked as imported *symbols* as well as module prefixes: the application
    layer is already allowed to name ``us_quant.trading.ports``, and
    ``BrokerExecutionPort`` lives there -- so without a symbol rule a supervisor
    could reach the execution seam and stay "architecturally correct" while
    becoming a second route to an order.
    """

    offending = _violations(
        _SUPERVISOR_APPLICATION,
        (
            "PySide6",
            "us_quant.desktop",
            "us_quant.desktop_v2",
            "us_quant.trading.adapters",
            "us_quant.trading.composition",
            "us_quant.trading.runtime",
            "us_quant.ibkr",
            "us_quant.broker",
            "sqlite3",
            "ibapi",
        ),
    )
    assert offending == []

    symbols = {
        alias.name
        for node in ast.walk(
            ast.parse(
                (_SRC / _SUPERVISOR_APPLICATION).read_text(encoding="utf-8")
            )
        )
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    for forbidden in (
        "RiskApplication",
        "ExecutionApplication",
        "BrokerExecutionPort",
        "OrderRepositoryPort",
        "PaperOrchestrator",
        "ExecutionOrchestrator",
        "PaperWorkflowPhase",
        "PaperWorkflowController",
        "ExecutionLeaseManager",
        "PaperTradingService",
        "StrategySelectionService",
        "IBKRExecutionAdapter",
    ):
        assert forbidden not in symbols, forbidden

    # And the capability discovery sees it, which is what makes the boundary
    # checks above run against it at all.
    assert "paper_autonomy_supervisor.py" in {
        path.name for path in _autonomy_module_paths(_SRC)
    }


def test_b_a16_the_supervisor_retains_no_runtime_truth() -> None:
    """Its collaborators, and one startup classification.  Nothing else.

    An exact set rather than a denylist, so a new retained field has to be argued
    for here.  The startup facts are the only fact held across ticks and they are
    held on purpose: they describe *this process's* beginning, and re-reading them
    later would answer a different question.

    Everything else -- runtime facts, the schedule verdict, the operator intent,
    the action state -- is read inside a tick and must not survive it: a scheduler
    deciding on a previous tick's facts is deciding on a session that may since
    have stopped.
    """

    tree = ast.parse(
        (_SRC / _SUPERVISOR_APPLICATION).read_text(encoding="utf-8")
    )
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.ClassDef)
        and item.name == "PaperAutonomySupervisor"
    )
    assigned: set[str] = set()
    for member in node.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(member):
            target = None
            if isinstance(child, ast.Assign) and len(child.targets) == 1:
                target = child.targets[0]
            elif isinstance(child, ast.AnnAssign):
                target = child.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)

    assert assigned == {
        "_actions",
        "_clock",
        "_emit",
        "_executor",
        "_intent",
        "_policy",
        "_runtime_facts",
        "_schedule",
        "_startup",
        "_startup_facts",
    }, sorted(assigned)
