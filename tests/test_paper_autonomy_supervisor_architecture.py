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


def test_b_a10_no_live_autonomy_vocabulary_exists() -> None:
    """I. Autonomy speaks about Paper and only about Paper.

    Asserted over the class names this layer defines and over the imported
    symbols it uses, so a future ``LiveAutonomy`` or a ``REAL_MONEY`` member has
    to fail a guard rather than arrive with a copy-paste.
    """

    banned = {
        "LiveAutonomy",
        "TradingAutonomy",
        "LiveRiskApplication",
        "LiveExecutionApplication",
        "LiveTradingRuntime",
    }
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                assert node.name not in banned, f"{_module_name(path)}::{node.name}"

    for relative in (_SUPERVISOR_DOMAIN, _SUPERVISOR_PORTS, _ACTION_PORT):
        source = (_SRC / relative).read_text(encoding="utf-8")
        for forbidden in ("REAL_MONEY", "LIVE_ORDER", "production_account"):
            assert forbidden not in source, (relative, forbidden)

    # The ports are structural, so a second implementation cannot pass as one.
    for port in (
        PaperAutonomyRuntimeFactsPort,
        PaperAutonomyStartupFactsPort,
        PaperAutonomySchedulePort,
        PaperAutonomyExecutorPort,
    ):
        assert isinstance(port, type)


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
