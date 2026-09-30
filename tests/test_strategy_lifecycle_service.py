"""Stage 6-C: the crash-safe apply/reconcile protocol.

The service owns one thing that the controller does not: the ordering between
"we decided" and "it applied".  These tests pin that a decision is durable
*before* the state machine moves, and that reconciliation resolves an
interrupted decision against the live status instead of replaying a transition
that already happened.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import (
    SQLiteStrategyLifecycleRepository,
)
from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_lifecycle import (
    StrategyLifecycleDecisionResult,
    StrategyLifecycleService,
)
from us_quant.trading.domain import strategy_lifecycle as _lifecycle
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_lifecycle import (
    ACTION_TARGET_STATUS,
    StrategyLifecycleAction,
    StrategyLifecycleAuthorization,
    StrategyLifecycleBlocker as Blocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _decision(**changes):
    values = dict(
        decision_id="sld-1", strategy_version_id="version-1", strategy_semver="1.0.0",
        parameter_hash="parameter-1", universe_hash="u", code_hash="c",
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        source_status=StrategyStatus.RESEARCH, target_status=StrategyStatus.PAPER_SHADOW,
        state=StrategyLifecycleDecisionState.PREPARED, blockers=(), triggers=(),
        policy_id="lifecycle-policy-1", policy_revision=1,
        policy_version="strategy-lifecycle-v1", authentication_id="sea-1",
        gate_evaluation_id="sge-1", coverage_evaluation_id="sce-1",
        coverage_policy_id="coverage-policy-1", coverage_policy_revision=1,
        controller_version="strategy-lifecycle-v1", authorized_at=NOW, applied_at=None,
    )
    values.update(changes)
    return StrategyLifecycleDecision(**values)


def _blocked(**changes):
    values = dict(
        decision_id="sld-blocked", policy_id=None, policy_revision=None,
        policy_version=None, authentication_id=None, gate_evaluation_id=None,
        coverage_evaluation_id=None, coverage_policy_id=None,
        coverage_policy_revision=None, state=StrategyLifecycleDecisionState.BLOCKED,
        blockers=(Blocker.POLICY_MISSING,),
    )
    return _decision(**{**values, **changes})


def _authorization(decision):
    return StrategyLifecycleAuthorization(
        decision.strategy_version_id,
        decision.action,
        ACTION_TARGET_STATUS[decision.action],
        decision.decision_id,
        NOW,
        _controller_token=_lifecycle._CONTROLLER_TOKEN,
    )


class _StubController:
    """Returns a fixed decision so the protocol is what is under test."""

    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        authorized = not self.decision.blockers
        return StrategyLifecycleDecisionResult(
            decision=self.decision,
            authorization=_authorization(self.decision) if authorized else None,
        )


class _BoomOnce(StrategyApplication):
    """A state machine whose first transition raises, to simulate a crash."""

    def __init__(self, repository):
        super().__init__(repository)
        self.explode = True

    def transition(self, version_id, target_status, **kwargs):
        if self.explode:
            self.explode = False
            raise RuntimeError("interrupted between decide and transition")
        return super().transition(version_id, target_status, **kwargs)


def _wired(tmp_path, decision, *, application=None):
    strategies = application if application is not None else StrategyApplication(
        SQLiteStrategyRepository(tmp_path / "strategies.sqlite3")
    )
    version = strategies.register(
        strategy_id="family-1", name="Example", description="test",
        semver="1.0.0-research", parameters={"whole_shares": True},
        universe_hash="u", code_hash="c", risk_budget_pct=0.01,
    )
    decisions = SQLiteStrategyLifecycleRepository(tmp_path / "lifecycle.sqlite3")
    controller = _StubController(decision)
    service = StrategyLifecycleService(
        controller=controller, decisions=decisions, strategies=strategies
    )
    return service, decisions, strategies, version


def _promotable(version):
    return _decision(
        strategy_version_id=version.version_id,
        strategy_semver=version.semver,
        parameter_hash=version.parameter_hash,
    )


# -- apply ---------------------------------------------------------------


def test_apply_prepares_then_transitions_then_marks_applied(tmp_path):
    service, decisions, strategies, version = _wired(tmp_path, None)
    decision = _promotable(version)
    service._controller = _StubController(decision)

    result = service.apply(
        version=version,
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        policy=None, authenticated=None, gate=None, coverage=None,
        applied_at=NOW,
    )

    assert result.authorised is True
    assert result.decision.state is StrategyLifecycleDecisionState.APPLIED
    assert result.decision.applied_at == NOW
    # The durable row agrees with the returned value and with the state machine.
    assert decisions.get_decision(decision.decision_id).state is (
        StrategyLifecycleDecisionState.APPLIED
    )
    assert strategies.get_version(version.version_id).status is StrategyStatus.PAPER_SHADOW


def test_a_refused_decision_is_recorded_and_changes_nothing(tmp_path):
    service, decisions, strategies, version = _wired(tmp_path, None)
    blocked = _blocked(strategy_version_id=version.version_id)
    service._controller = _StubController(blocked)

    result = service.apply(
        version=version,
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        policy=None, authenticated=None, gate=None, coverage=None,
        applied_at=NOW,
    )

    assert result.authorised is False
    assert result.decision.state is StrategyLifecycleDecisionState.BLOCKED
    assert decisions.get_decision("sld-blocked").blockers == (Blocker.POLICY_MISSING,)
    assert strategies.get_version(version.version_id).status is StrategyStatus.RESEARCH


def test_an_interruption_between_decide_and_apply_leaves_a_prepared_row(tmp_path):
    """The decision is durable before the transition, so a crash is recoverable."""

    strategies = _BoomOnce(SQLiteStrategyRepository(tmp_path / "strategies.sqlite3"))
    service, decisions, strategies, version = _wired(
        tmp_path, None, application=strategies
    )
    decision = _promotable(version)
    service._controller = _StubController(decision)

    with pytest.raises(RuntimeError):
        service.apply(
            version=version,
            action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            policy=None, authenticated=None, gate=None, coverage=None,
            applied_at=NOW,
        )

    prepared = decisions.prepared_decisions(version.version_id)
    assert [item.decision_id for item in prepared] == [decision.decision_id]
    assert strategies.get_version(version.version_id).status is StrategyStatus.RESEARCH


# -- reconcile -----------------------------------------------------------


def test_reconcile_finishes_a_transition_that_already_happened(tmp_path):
    service, decisions, strategies, version = _wired(tmp_path, None)
    decision = _promotable(version)
    service._controller = _StubController(decision)
    decisions.record_decision(decision)
    # The transition happened, but the APPLIED write did not.
    strategies.transition(
        version.version_id,
        StrategyStatus.PAPER_SHADOW,
        reason="interrupted",
        authorization=_authorization(decision),
    )

    resolved = service.reconcile(version_id=version.version_id, now=NOW)

    assert [item.state for item in resolved] == [
        StrategyLifecycleDecisionState.APPLIED
    ]
    assert decisions.prepared_decisions(version.version_id) == ()


def test_reconcile_leaves_an_unstarted_decision_prepared(tmp_path):
    """Nothing happened, so a retry must still be able to apply it."""

    service, decisions, strategies, version = _wired(tmp_path, None)
    decision = _promotable(version)
    decisions.record_decision(decision)

    resolved = service.reconcile(version_id=version.version_id, now=NOW)

    assert [item.state for item in resolved] == [
        StrategyLifecycleDecisionState.PREPARED
    ]
    assert decisions.prepared_decisions(version.version_id) == (decision,)


def test_reconcile_supersedes_a_decision_the_world_moved_past(tmp_path):
    service, decisions, strategies, version = _wired(tmp_path, None)
    decision = _promotable(version)
    decisions.record_decision(decision)
    # Somebody stopped the version by another route.
    strategies.transition(
        version.version_id, StrategyStatus.STOPPED, reason="operator"
    )

    resolved = service.reconcile(version_id=version.version_id, now=NOW)

    assert [item.state for item in resolved] == [
        StrategyLifecycleDecisionState.SUPERSEDED
    ]
    assert strategies.get_version(version.version_id).status is StrategyStatus.STOPPED


def test_reconcile_never_repeats_a_transition(tmp_path):
    """Reconciling an already-applied decision must not transition again."""

    service, decisions, strategies, version = _wired(tmp_path, None)
    decision = _promotable(version)
    service._controller = _StubController(decision)
    service.apply(
        version=version,
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        policy=None, authenticated=None, gate=None, coverage=None,
        applied_at=NOW,
    )

    resolved = service.reconcile(version_id=version.version_id, now=NOW)

    assert resolved == ()
    assert strategies.get_version(version.version_id).status is StrategyStatus.PAPER_SHADOW


def test_reconcile_requires_an_aware_clock(tmp_path):
    service, _, _, version = _wired(tmp_path, None)

    with pytest.raises(ValueError):
        service.reconcile(version_id=version.version_id, now=datetime(2026, 1, 1))
