"""Explicit, disabled-only recovery for ambiguous Paper autonomy actions."""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import pathlib

import pytest

from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.adapters.sqlite.paper_autonomy_repository import (
    SQLitePaperAutonomyRepository,
)
from us_quant.trading.application.paper_autonomy import PaperAutonomyApplication
from us_quant.trading.application.paper_autonomy_recovery import (
    PaperAutonomyRecoveryApplication,
)
from us_quant.desktop_v2.orchestration.autonomy.facts import (
    PaperAutonomyStartupFactsAdapter,
)
from us_quant.trading.domain.paper_autonomy import INITIAL_REVISION, PaperAutonomyMode
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomyStartupFacts,
    PaperAutonomySupervisorViolation,
    recovery_authorization_is_valid,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
    PaperAutonomyActionStoreUnreadable,
)

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 27)
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"


def _record(
    key: str,
    status: PaperAutonomyActionStatus,
    *,
    revision: int = INITIAL_REVISION,
) -> PaperAutonomyActionRecord:
    return PaperAutonomyActionRecord(
        action_key=key,
        intent_revision=revision,
        trading_day=DAY,
        action=PaperAutonomyActionType.START,
        status=status,
        claimed_at=NOW,
        completed_at=None,
        detail="supervisor requested a Paper start",
    )


@pytest.fixture
def setup(tmp_path: pathlib.Path):
    intent_store = SQLitePaperAutonomyRepository(tmp_path / "intent.sqlite3")
    actions = SQLitePaperAutonomyActionRepository(tmp_path / "actions.sqlite3")
    intent = PaperAutonomyApplication(intent_store, clock=lambda: NOW)
    recovery = PaperAutonomyRecoveryApplication(
        intent, actions, clock=lambda: NOW
    )
    return intent_store, actions, intent, recovery


def test_resolve_requires_disabled_intent(setup) -> None:
    _, actions, intent, recovery = setup
    enabled = intent.enable(INITIAL_REVISION, "operator enabled Paper autonomy")
    actions.claim(
        _record("old-start", PaperAutonomyActionStatus.CLAIMED, revision=1)
    )
    actions.mark_requested(action_key="old-start", detail="Paper accepted start")

    with pytest.raises(PaperAutonomySupervisorViolation, match="disable Paper autonomy"):
        recovery.resolve_unknown(
            action_key="old-start",
            expected_status=PaperAutonomyActionStatus.REQUESTED,
            reason="reviewed broker state",
        )

    assert intent.snapshot() == enabled
    assert actions.get("old-start").status is PaperAutonomyActionStatus.REQUESTED


def test_resolution_uses_expected_status(setup) -> None:
    _, actions, intent, recovery = setup
    enabled = intent.enable(INITIAL_REVISION, "enable for the old process")
    intent.disable(enabled.revision, "stop autonomy before recovery")
    actions.claim(
        _record("old-start", PaperAutonomyActionStatus.CLAIMED, revision=2)
    )
    actions.mark_requested(action_key="old-start", detail="Paper accepted start")

    with pytest.raises(PaperAutonomyActionRepositoryError, match="changed status"):
        recovery.resolve_unknown(
            action_key="old-start",
            expected_status=PaperAutonomyActionStatus.CLAIMED,
            reason="broker and account reviewed",
        )
    assert actions.get("old-start").status is PaperAutonomyActionStatus.REQUESTED
    unsafe_startup = PaperAutonomyStartupFacts(
        intent_store_readable=True,
        action_store_readable=True,
        broker_state_known=True,
        account_identity_known=True,
        open_broker_orders=0,
        broker_positions=0,
        unreconciled_rows=0,
        paper_ownership_clear=True,
        manual_recovery_required=False,
        unresolved_action_count=len(recovery.unresolved()),
        recovery_authorization_valid=True,
    )
    assert unsafe_startup.proven_safe is False

    resolved = recovery.resolve_unknown(
        action_key="old-start",
        expected_status=PaperAutonomyActionStatus.REQUESTED,
        reason="broker, account, orders, and positions reviewed",
    )
    assert resolved.status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    assert resolved.completed_at == NOW
    assert resolved.authorization_floor_revision == intent.snapshot().revision
    assert resolved.detail == "broker, account, orders, and positions reviewed"
    assert resolved.action_key == "old-start"
    assert resolved.claimed_at == NOW
    assert resolved.intent_revision == 2
    assert resolved.action is PaperAutonomyActionType.START
    assert resolved.trading_day == DAY and resolved.action_day == DAY
    assert recovery.unresolved() == ()
    assert actions.start_attempted(DAY) is True
    startup = PaperAutonomyStartupFacts(
        intent_store_readable=True,
        action_store_readable=True,
        broker_state_known=True,
        account_identity_known=True,
        open_broker_orders=0,
        broker_positions=0,
        unreconciled_rows=0,
        paper_ownership_clear=True,
        manual_recovery_required=False,
        unresolved_action_count=len(recovery.unresolved()),
        recovery_authorization_valid=True,
    )
    assert startup.proven_safe is True


def test_claimed_crash_window_can_be_resolved_while_disabled(setup) -> None:
    _, actions, _, recovery = setup
    actions.claim(_record("claim-only", PaperAutonomyActionStatus.CLAIMED))

    resolved = recovery.resolve_unknown(
        action_key="claim-only",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="confirmed no unattended retry is safe",
    )
    assert resolved.status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    assert actions.start_attempted(DAY) is True


def test_kill_latched_disabled_intent_allows_resolution(setup) -> None:
    _, actions, intent, recovery = setup
    latched = intent.engage_kill_switch(INITIAL_REVISION, "operator kill")
    actions.claim(_record("kill-window", PaperAutonomyActionStatus.CLAIMED))

    resolved = recovery.resolve_unknown(
        action_key="kill-window",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="operator reviewed the previous process",
    )
    assert resolved.status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    assert intent.snapshot() == latched
    assert intent.snapshot().mode is PaperAutonomyMode.DISABLED
    assert intent.snapshot().kill_switch_latched is True


def test_operator_resolution_is_not_success(setup) -> None:
    _, actions, intent, recovery = setup
    enabled = intent.enable(INITIAL_REVISION, "authorize prior process")
    intent.disable(enabled.revision, "disable before action review")
    actions.claim(_record("owner-outcome", PaperAutonomyActionStatus.CLAIMED))

    resolved = recovery.resolve_unknown(
        action_key="owner-outcome",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="operator reviewed the ambiguous action",
    )
    assert resolved.status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    assert resolved.status is not PaperAutonomyActionStatus.SUCCEEDED

    actions.claim(_record("canonical-outcome", PaperAutonomyActionStatus.CLAIMED))
    with pytest.raises(PaperAutonomyActionRepositoryError):
        actions.complete(
            action_key="canonical-outcome",
            status=PaperAutonomyActionStatus.OPERATOR_RESOLVED,
            completed_at=NOW,
            detail="must use the recovery authority",
        )
    assert actions.get("canonical-outcome").status is PaperAutonomyActionStatus.CLAIMED


def test_history_is_preserved_as_operator_resolved(setup) -> None:
    _, actions, intent, recovery = setup
    enabled = intent.enable(INITIAL_REVISION, "authorize prior process")
    intent.disable(enabled.revision, "disable before action review")
    actions.claim(_record("history", PaperAutonomyActionStatus.CLAIMED))
    before = actions.get("history")

    after = recovery.resolve_unknown(
        action_key="history",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="operator checked the account and broker",
    )

    assert after is not None
    assert after.status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    assert after.action_key == before.action_key
    assert after.intent_revision == before.intent_revision
    assert after.trading_day == before.trading_day
    assert after.action_day == before.action_day
    assert after.action is before.action
    assert after.claimed_at == before.claimed_at
    assert after.completed_at == NOW
    assert after.detail == "operator checked the account and broker"
    assert len(actions.recent()) == 1


def test_resolved_start_still_counts_as_attempted(setup) -> None:
    _, actions, intent, recovery = setup
    enabled = intent.enable(INITIAL_REVISION, "authorize prior process")
    intent.disable(enabled.revision, "disable before action review")
    actions.claim(_record("daily-start", PaperAutonomyActionStatus.CLAIMED))

    recovery.resolve_unknown(
        action_key="daily-start",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="reviewed; do not retry unattended",
    )

    assert actions.start_attempted(DAY) is True


def test_concurrent_enable_before_resolution_is_not_fresh_authorization(
    tmp_path: pathlib.Path,
) -> None:
    intent_store = SQLitePaperAutonomyRepository(tmp_path / "intent-race.sqlite3")
    actions = SQLitePaperAutonomyActionRepository(tmp_path / "actions-race.sqlite3")
    at = lambda second: datetime(2026, 9, 27, 9, 0, second, tzinfo=timezone.utc)
    intent = PaperAutonomyApplication(intent_store, clock=lambda: at(1))
    enabled = intent.enable(INITIAL_REVISION, "initial authorization")
    intent = PaperAutonomyApplication(intent_store, clock=lambda: at(2))
    disabled = intent.disable(enabled.revision, "disable before recovery")
    actions.claim(
        PaperAutonomyActionRecord(
            action_key="race-start",
            intent_revision=disabled.revision,
            trading_day=DAY,
            action=PaperAutonomyActionType.START,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=at(1),
            completed_at=None,
            detail="interrupted start",
        )
    )

    class RacingActionRepository:
        def __getattr__(self, name):
            return getattr(actions, name)

        def resolve_unknown(self, **kwargs):
            PaperAutonomyApplication(
                intent_store, clock=lambda: at(3)
            ).enable(disabled.revision, "enable during action resolution")
            actions.resolve_unknown(**kwargs)

    recovery = PaperAutonomyRecoveryApplication(
        intent, RacingActionRepository(), clock=lambda: at(4)
    )
    resolved = recovery.resolve_unknown(
        action_key="race-start",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="operator reviewed the ambiguous action",
    )

    concurrent_intent = intent_store.load_intent()
    assert concurrent_intent.mode is PaperAutonomyMode.ENABLED
    assert resolved.completed_at == at(4)
    assert resolved.authorization_floor_revision == concurrent_intent.revision
    assert actions.latest_operator_resolution_barrier() == concurrent_intent.revision
    assert not recovery_authorization_is_valid(
        concurrent_intent.mode,
        concurrent_intent.revision,
        actions.latest_operator_resolution_barrier(),
    )

    disabled_again = PaperAutonomyApplication(
        intent_store, clock=lambda: at(5)
    ).disable(concurrent_intent.revision, "operator reviewed after recovery")
    enabled_after_recovery = PaperAutonomyApplication(
        intent_store, clock=lambda: at(6)
    ).enable(disabled_again.revision, "explicit post-recovery authorization")
    assert recovery_authorization_is_valid(
        enabled_after_recovery.mode,
        enabled_after_recovery.revision,
        actions.latest_operator_resolution_barrier(),
    )


def test_recovery_authorization_revision_comparison_is_strict() -> None:
    assert recovery_authorization_is_valid(PaperAutonomyMode.DISABLED, None, 7)
    assert recovery_authorization_is_valid(PaperAutonomyMode.ENABLED, 8, None)
    assert not recovery_authorization_is_valid(PaperAutonomyMode.ENABLED, 7, 7)
    assert not recovery_authorization_is_valid(PaperAutonomyMode.PAUSED, 6, 7)
    assert recovery_authorization_is_valid(PaperAutonomyMode.ENABLED, 8, 7)


def test_unsealed_resolution_is_startup_unsafe_and_can_be_resumed(setup) -> None:
    _, actions, intent, recovery = setup
    actions.claim(_record("prior-sealed", PaperAutonomyActionStatus.CLAIMED))
    actions.resolve_unknown(
        action_key="prior-sealed",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        resolved_at=NOW,
        detail="prior resolution",
    )
    actions.seal_operator_resolution(
        action_key="prior-sealed",
        authorization_floor_revision=INITIAL_REVISION,
    )
    actions.claim(_record("crashed-before-seal", PaperAutonomyActionStatus.CLAIMED))
    actions.resolve_unknown(
        action_key="crashed-before-seal",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        resolved_at=NOW,
        detail="operator reviewed before crash",
    )

    assert actions.unresolved()[0].status is PaperAutonomyActionStatus.OPERATOR_RESOLVED
    with pytest.raises(PaperAutonomyActionStoreUnreadable, match="no sealed authorization floor"):
        actions.latest_operator_resolution_barrier()

    def recovery_barrier_valid():
        try:
            current = intent.snapshot()
            floor = actions.latest_operator_resolution_barrier()
        except PaperAutonomyActionStoreUnreadable:
            return None
        return recovery_authorization_is_valid(
            current.mode, current.revision, floor
        )

    startup = PaperAutonomyStartupFactsAdapter(
        intent_store_readable=lambda: True,
        unresolved_action_count=lambda: len(actions.unresolved()),
        recovery_authorization_valid=recovery_barrier_valid,
        broker_state_known=lambda: True,
        account_identity_known=lambda: True,
        open_broker_orders=lambda: 0,
        broker_positions=lambda: 0,
        unreconciled_rows=lambda: 0,
        paper_ownership_clear=lambda: True,
        manual_recovery_required=lambda: False,
    ).startup_facts()
    assert startup.unresolved_action_count == 1
    assert startup.recovery_authorization_valid is None
    assert not startup.proven_safe

    sealed = recovery.seal_operator_resolution(
        action_key="crashed-before-seal"
    )
    assert sealed.authorization_floor_revision == intent.snapshot().revision
    assert actions.unresolved() == ()
    assert actions.latest_operator_resolution_barrier() == intent.snapshot().revision


def test_seal_is_write_once_and_preserves_action_fields(setup) -> None:
    _, actions, intent, recovery = setup
    actions.claim(_record("seal-once", PaperAutonomyActionStatus.CLAIMED))
    actions.resolve_unknown(
        action_key="seal-once",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        resolved_at=NOW,
        detail="operator reviewed the action",
    )
    unsealed = actions.get("seal-once")
    assert unsealed.authorization_floor_revision is None

    sealed = recovery.seal_operator_resolution(action_key="seal-once")
    assert sealed.authorization_floor_revision == intent.snapshot().revision
    for field in (
        "action",
        "action_key",
        "intent_revision",
        "trading_day",
        "action_day",
        "claimed_at",
        "completed_at",
        "detail",
    ):
        assert getattr(sealed, field) == getattr(unsealed, field)

    with pytest.raises(PaperAutonomyActionRepositoryError, match="already sealed"):
        actions.seal_operator_resolution(
            action_key="seal-once",
            authorization_floor_revision=intent.snapshot().revision + 1,
        )
    assert actions.get("seal-once") == sealed


def test_enable_after_resolution_seal_advances_authorization_revision(setup) -> None:
    _, actions, intent, recovery = setup
    actions.claim(_record("post-recovery-enable", PaperAutonomyActionStatus.CLAIMED))

    resolved = recovery.resolve_unknown(
        action_key="post-recovery-enable",
        expected_status=PaperAutonomyActionStatus.CLAIMED,
        reason="operator reviewed before explicit reauthorization",
    )
    enabled = intent.enable(
        resolved.authorization_floor_revision,
        "authorize after recovery",
    )

    assert enabled.revision > resolved.authorization_floor_revision
    assert recovery_authorization_is_valid(
        enabled.mode,
        enabled.revision,
        actions.latest_operator_resolution_barrier(),
    )


def test_recovery_authority_has_no_runtime_or_enable_capability() -> None:
    path = SRC / "trading" / "application" / "paper_autonomy_recovery.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(("PySide6", "us_quant.desktop", "us_quant.ibkr", "us_quant.broker"))
        or name.endswith(("risk", "execution", "paper_orchestrator"))
        for name in imports
    )
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not identifiers & {
        "enable", "pause", "engage_kill_switch", "start", "resume", "stop"
    }


def test_only_recovery_application_calls_resolve_unknown_in_production() -> None:
    callers: list[str] = []
    for path in (ROOT / "src" / "us_quant").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve_unknown"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_actions"
            ):
                callers.append(path.relative_to(ROOT / "src" / "us_quant").as_posix())
    assert callers == ["trading/application/paper_autonomy_recovery.py"]


def test_cli_does_not_import_the_concrete_action_store() -> None:
    path = SRC / "cli.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "us_quant.trading.adapters.sqlite.paper_autonomy_action_repository" not in imported


def test_completion_and_refusal_authorities_cannot_write_operator_resolution() -> None:
    completion_path = (
        SRC / "desktop_v2" / "orchestration" / "autonomy" / "completion.py"
    )
    completion_tree = ast.parse(completion_path.read_text(encoding="utf-8"))
    observed_outcomes = set()
    for node in ast.walk(completion_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_observe"
            and len(node.args) >= 2
        ):
            status = node.args[1]
            if isinstance(status, ast.Attribute):
                observed_outcomes.add(status.attr)
    assert observed_outcomes == {"SUCCEEDED", "FAILED"}

    supervisor_path = SRC / "trading" / "application" / "paper_autonomy_supervisor.py"
    supervisor_tree = ast.parse(supervisor_path.read_text(encoding="utf-8"))
    refusal_outcomes = []
    for node in ast.walk(supervisor_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "complete"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "_actions"
        ):
            status = next(item.value for item in node.keywords if item.arg == "status")
            refusal_outcomes.append(status.attr if isinstance(status, ast.Attribute) else None)
    assert refusal_outcomes == ["REFUSED"]


def test_unsafe_startup_returns_before_completion_signals_are_connected() -> None:
    path = SRC / "desktop.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    initialize = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_initialize_paper_autonomy"
    )
    body = ast.get_source_segment(source, initialize)
    assert body is not None
    gate = next(
        node
        for node in initialize.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.operand, ast.Attribute)
        and node.test.operand.attr == "proven_safe"
    )
    assert any(isinstance(node, ast.Return) for node in gate.body)
    assert body.index("if not startup.proven_safe:") < body.index(
        "self.paper_orchestrator.session_running_published.connect("
    )
    assert body.index("if not startup.proven_safe:") < body.index(
        "self.paper_orchestrator.preparation_ready_published.connect("
    )
