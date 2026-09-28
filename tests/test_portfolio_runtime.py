from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock, Thread
from time import sleep

import pytest

from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime, PortfolioRuntimeError
from us_quant.trading.domain.portfolio import (
    PortfolioBlocker,
    PortfolioCapitalPolicy,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
    PortfolioVerdict,
)
from us_quant.trading.domain.portfolio_runtime import PortfolioDispatchResult
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.runtime.portfolio_dispatch import PortfolioOrderDispatchBridge
from us_quant.trading.runtime.portfolio import SessionBook


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


class _Strategies:
    def __init__(self, versions):
        self.versions = tuple(versions)

    def list_versions(self):
        return self.versions


class _ProposalSource:
    def __init__(self, mapping=None, *, reverse=False, on_call=None):
        self.mapping = mapping or {}
        self.reverse = reverse
        self.calls = []
        self.observations = []
        self.on_call = on_call

    def proposals_for(self, strategy, *, observed_at, proposal_cutoff, portfolio_snapshot):
        self.calls.append(strategy.version_id)
        self.observations.append((observed_at, proposal_cutoff, portfolio_snapshot))
        self.last_snapshot = portfolio_snapshot
        if self.on_call:
            self.on_call()
        values = tuple(self.mapping.get(strategy.version_id, ()))
        return tuple(reversed(values)) if self.reverse else values


class _Snapshots:
    def __init__(self, *, cash=Decimal("1000")):
        self.cash = cash
        self.calls = []

    def snapshot(self, *, observed_at):
        self.calls.append(observed_at)
        return PortfolioSnapshot(
            cash=self.cash,
            equity=Decimal("1000"),
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            observed_at=observed_at,
        )


class _RiskPath:
    def __init__(self, *, risk=None, dispatch=None):
        self.risk = risk
        self.dispatch = dispatch or PortfolioDispatchResult(
            submitted=True, order_id="order-1", status="submitted"
        )
        self.evaluations = []
        self.submissions = []

    def evaluate(self, decision, *, observed_at):
        self.evaluations.append((decision, observed_at))
        return self.risk or RiskDecision.approve(
            requested_quantity=decision.action.quantity
        )

    def submit(self, decision, risk_decision, *, observed_at):
        self.submissions.append((decision, risk_decision, observed_at))
        return self.dispatch


def _version(
    version_id,
    *,
    status=StrategyStatus.PAPER_SHADOW,
    mode=StrategyMode.PAPER_SHADOW,
    gate_passed=True,
):
    return StrategyVersion(
        definition=StrategyDefinition("family-" + version_id, "name", "test"),
        identity=StrategyIdentity("family-" + version_id, version_id, "hash-" + version_id),
        semver="1.0.0",
        status=status,
        mode=mode,
        parameters={},
        universe_hash="universe",
        code_hash="code",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=gate_passed,
        gate_reason="passed" if gate_passed else "not passed",
        created_at=NOW,
        updated_at=NOW,
    )


def _policy(versions, *, disabled=()):
    weight = Decimal("1") / Decimal(len(versions)) if versions else Decimal("0")
    ceiling = Decimal("1000") / Decimal(len(versions)) if versions else Decimal("0")
    allocations = tuple(
        PortfolioStrategyAllocation(
            strategy_version_id=version.version_id,
            capital_weight=weight,
            max_capital=ceiling,
            max_gross_exposure=ceiling,
            enabled=version.version_id not in disabled,
        )
        for version in versions
    )
    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"),
        max_single_position_notional=Decimal("1000"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=10,
        max_open_orders=10,
        allocations=allocations,
    )


def _proposal(version, *, symbol="AAPL", action=TradeAction.BUY, quantity=10, at=NOW, price="10"):
    return TradeProposal(
        strategy=version.identity,
        symbol=symbol,
        action=action,
        desired_quantity=0 if action is TradeAction.HOLD else quantity,
        reference_price=Decimal(price),
        reason="test proposal",
        generated_at=at,
    )


def _runtime(
    tmp_path: Path,
    versions,
    proposal_source,
    *,
    risk_path=None,
    snapshots=None,
):
    repo = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")
    snapshots = snapshots or _Snapshots()
    risk_path = risk_path or _RiskPath()
    runtime = PortfolioRuntime(
        strategies=_Strategies(versions),
        proposals=proposal_source,
        snapshots=snapshots,
        repository=repo,
        risk_path=risk_path,
    )
    return runtime, repo, snapshots, risk_path


def _evaluate(runtime, versions, *, at=NOW, cycle="cycle-1", snapshot_id="snapshot-1", policy=None):
    return runtime.evaluate_cycle(
        portfolio_cycle_id=cycle,
        observed_at=at,
        proposal_cutoff=at,
        snapshot_identity=snapshot_id,
        policy=policy or _policy(versions),
        policy_identity="paper-policy",
        policy_revision="4",
        selected_version_ids=frozenset(version.version_id for version in versions),
    )


def test_three_governed_strategies_share_one_cycle_and_net_deterministically(tmp_path):
    versions = tuple(_version(name) for name in ("alpha", "beta", "gamma"))
    proposals = _ProposalSource(
        {
            versions[0].version_id: (_proposal(versions[0], quantity=5),),
            versions[1].version_id: (_proposal(versions[1], quantity=4),),
            versions[2].version_id: (
                _proposal(versions[2], action=TradeAction.SELL, quantity=3),
            ),
        }
    )
    class ExistingGammaPosition:
        def snapshot(self, *, observed_at):
            return PortfolioSnapshot(
                cash=Decimal("1000"),
                equity=Decimal("1000"),
                gross_exposure=Decimal("30"),
                net_exposure=Decimal("30"),
                positions=(PortfolioPosition("AAPL", 3, Decimal("30")),),
                strategy_exposure=(
                    PortfolioStrategyExposure("gamma", "AAPL", Decimal("30"), 3),
                ),
                observed_at=observed_at,
            )

    runtime, repo, _, risk = _runtime(
        tmp_path, versions, proposals, snapshots=ExistingGammaPosition()
    )

    result = _evaluate(runtime, versions)

    assert proposals.calls == sorted(version.version_id for version in versions)
    assert len(proposals.observations) == len(versions)
    assert all(item[0] == item[1] == NOW for item in proposals.observations)
    assert all(item[2] is proposals.observations[0][2] for item in proposals.observations)
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.decision is PortfolioVerdict.APPROVE
    assert decision.net_quantity == 6
    assert decision.action.quantity == 6
    assert sum(item.signed_requested_quantity for item in decision.attribution) == 6
    assert risk.evaluations[0][0] == decision
    assert len(risk.submissions) == 1
    persisted = repo.decision(decision.decision_id)
    assert persisted.snapshot_identity == "snapshot-1"
    assert len(persisted.decision.attribution) == 3


def test_zero_net_and_hold_never_reach_risk(tmp_path):
    versions = (_version("alpha"), _version("beta"))
    proposals = _ProposalSource(
        {
            versions[0].version_id: (_proposal(versions[0], quantity=6),),
            versions[1].version_id: (
                _proposal(versions[1], action=TradeAction.SELL, quantity=6),
            ),
        }
    )
    class ExistingBetaPosition:
        def snapshot(self, *, observed_at):
            return PortfolioSnapshot(
                cash=Decimal("1000"),
                equity=Decimal("1000"),
                gross_exposure=Decimal("60"),
                net_exposure=Decimal("60"),
                positions=(PortfolioPosition("AAPL", 6, Decimal("60")),),
                strategy_exposure=(
                    PortfolioStrategyExposure("beta", "AAPL", Decimal("60"), 6),
                ),
                observed_at=observed_at,
            )

    runtime, _, _, risk = _runtime(
        tmp_path, versions, proposals, snapshots=ExistingBetaPosition()
    )

    result = _evaluate(runtime, versions)

    assert result.decisions[0].net_quantity == 0
    assert result.decisions[0].action is None
    assert risk.evaluations == []
    assert risk.submissions == []

    hold_source = _ProposalSource(
        {versions[0].version_id: (_proposal(versions[0], action=TradeAction.HOLD),)}
    )
    hold_runtime, _, _, hold_risk = _runtime(tmp_path / "hold", versions[:1], hold_source)
    hold_result = _evaluate(hold_runtime, versions[:1], cycle="hold-cycle")
    assert hold_result.decisions == ()
    assert hold_risk.evaluations == []


def test_only_selected_governed_enabled_and_allocated_versions_run(tmp_path):
    eligible = _version("eligible")
    research = _version("research", status=StrategyStatus.RESEARCH, mode=StrategyMode.RESEARCH)
    stopped = _version("stopped", status=StrategyStatus.STOPPED)
    disabled = _version("disabled")
    versions = (eligible, research, stopped, disabled)
    proposals = _ProposalSource(
        {version.version_id: (_proposal(version),) for version in versions}
    )
    runtime, _, _, _ = _runtime(tmp_path, versions, proposals)
    selected = frozenset({eligible.version_id})

    runtime.evaluate_cycle(
        portfolio_cycle_id="eligibility",
        observed_at=NOW,
        proposal_cutoff=NOW,
        snapshot_identity="snapshot",
        policy=_policy(versions, disabled={disabled.version_id}),
        policy_identity="policy",
        policy_revision="1",
        selected_version_ids=selected,
    )

    assert proposals.calls == [eligible.version_id]

    stale_selected = frozenset({research.version_id})
    blocked, _, _, blocked_risk = _runtime(
        tmp_path / "stale-selection", versions, proposals
    )
    with pytest.raises(PortfolioRuntimeError, match="no longer governed or allocated"):
        blocked.evaluate_cycle(
            portfolio_cycle_id="stale-selection",
            observed_at=NOW,
            proposal_cutoff=NOW,
            snapshot_identity="snapshot",
            policy=_policy(versions),
            policy_identity="policy",
            policy_revision="1",
            selected_version_ids=stale_selected,
        )
    assert blocked_risk.evaluations == []


def test_selected_version_with_disabled_allocation_fails_closed(tmp_path):
    disabled = _version("disabled-allocation")
    proposals = _ProposalSource(
        {disabled.version_id: (_proposal(disabled),)}
    )
    runtime, _, _, risk = _runtime(tmp_path, (disabled,), proposals)
    policy = _policy((disabled,), disabled={disabled.version_id})
    assert policy.allocation_for(disabled.version_id).enabled is False

    with pytest.raises(PortfolioRuntimeError, match="no longer governed or allocated"):
        runtime.evaluate_cycle(
            portfolio_cycle_id="disabled-allocation",
            observed_at=NOW,
            proposal_cutoff=NOW,
            snapshot_identity="snapshot",
            policy=policy,
            policy_identity="policy",
            policy_revision="1",
            selected_version_ids=frozenset({disabled.version_id}),
        )

    assert proposals.calls == []
    assert risk.evaluations == []


def test_strategy_cannot_sell_another_strategys_position(tmp_path):
    alpha = _version("alpha")
    source = _ProposalSource({
        alpha.version_id: (_proposal(alpha, action=TradeAction.SELL, quantity=1),)
    })

    class BetaOwnsPosition:
        def snapshot(self, *, observed_at):
            return PortfolioSnapshot(
                cash=Decimal("900"), equity=Decimal("1000"),
                gross_exposure=Decimal("100"), net_exposure=Decimal("100"),
                positions=(PortfolioPosition("AAPL", 10, Decimal("100")),),
                strategy_exposure=(
                    PortfolioStrategyExposure("beta", "AAPL", Decimal("100"), 10),
                ),
                observed_at=observed_at,
            )

    runtime, _, _, risk = _runtime(
        tmp_path, (alpha,), source, snapshots=BetaOwnsPosition()
    )
    result = _evaluate(runtime, (alpha,))
    assert result.decisions[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED
    assert risk.evaluations == []


def test_mixed_observation_windows_are_rejected(tmp_path):
    version = _version("alpha")
    proposals = _ProposalSource(
        {version.version_id: (_proposal(version, at=NOW + timedelta(seconds=8)),)}
    )
    runtime, _, _, risk = _runtime(tmp_path, (version,), proposals)

    with pytest.raises(PortfolioRuntimeError, match="observation window"):
        _evaluate(runtime, (version,))
    assert risk.evaluations == []


def test_proposal_input_order_does_not_change_portfolio_decision(tmp_path):
    versions = (_version("alpha"), _version("beta"))
    mapping = {
        versions[0].version_id: (
            _proposal(versions[0], symbol="AAPL", quantity=2),
            _proposal(versions[0], symbol="MSFT", quantity=2),
        ),
        versions[1].version_id: (_proposal(versions[1], action=TradeAction.SELL, quantity=1),),
    }
    first, _, _, _ = _runtime(tmp_path / "first", versions, _ProposalSource(mapping))
    second, _, _, _ = _runtime(tmp_path / "second", versions, _ProposalSource(mapping, reverse=True))

    left = _evaluate(first, versions)
    right = _evaluate(second, versions)

    assert left.decisions == right.decisions


def test_risk_reject_is_persisted_and_never_submitted(tmp_path):
    version = _version("alpha")
    proposals = _ProposalSource({version.version_id: (_proposal(version),)})
    risk_path = _RiskPath(risk=RiskDecision.reject("account halted", requested_quantity=10))
    runtime, repo, _, _ = _runtime(tmp_path, (version,), proposals, risk_path=risk_path)

    result = _evaluate(runtime, (version,))

    record = repo.decision(result.decisions[0].decision_id)
    assert record.risk_outcome == "rejected"
    assert record.order_id is None
    assert len(risk_path.evaluations) == 1
    assert risk_path.submissions == []


def test_uncertain_execution_and_restart_do_not_regenerate_action(tmp_path):
    version = _version("alpha")
    proposals = _ProposalSource({version.version_id: (_proposal(version),)})
    path = _RiskPath(
        dispatch=PortfolioDispatchResult(
            submitted=False, halt=True, order_id="uncertain-order", status="reconcile"
        )
    )
    runtime, repo, snapshots, _ = _runtime(tmp_path, (version,), proposals, risk_path=path)
    first = _evaluate(runtime, (version,))
    persisted_id = first.decisions[0].decision_id
    assert first.halted is True

    restarted = PortfolioRuntime(
        strategies=_Strategies((version,)),
        proposals=proposals,
        snapshots=snapshots,
        repository=SQLitePortfolioRepository(tmp_path / "portfolio.sqlite"),
        risk_path=path,
    )
    second = _evaluate(restarted, (version,))

    assert second.decisions[0].decision_id == persisted_id
    assert second.actions[0].recovered is True
    assert second.actions[0].dispatch.halt is True
    assert second.actions[0].dispatch.submitted is False
    assert second.actions[0].dispatch.status == "reconcile"
    assert len(path.evaluations) == 1
    assert len(path.submissions) == 1
    persisted = repo.decision(persisted_id)
    assert persisted.order_id == "uncertain-order"
    assert persisted.dispatch_outcome_recorded is True
    assert persisted.dispatch_halt is True
    assert persisted.risk_decision == RiskDecision.approve(requested_quantity=10)
    execution_attribution = repo.execution_attribution("uncertain-order")
    assert execution_attribution is not None
    assert execution_attribution.portfolio_decision_id == persisted_id
    assert sum(
        item.signed_quantity for item in execution_attribution.contributions
    ) == execution_attribution.quantity
    assert execution_attribution.quantity == 10


def test_risk_trim_is_allocated_across_execution_attribution(tmp_path):
    alpha = _version("alpha")
    beta = _version("beta")
    source = _ProposalSource(
        {
            alpha.version_id: (_proposal(alpha, quantity=7),),
            beta.version_id: (_proposal(beta, quantity=3),),
        }
    )
    risk = RiskDecision.approve(
        requested_quantity=10,
        approved_quantity=4,
        adjustments=("portfolio cap",),
    )
    runtime, repository, _, _ = _runtime(
        tmp_path, (alpha, beta), source, risk_path=_RiskPath(risk=risk)
    )

    result = _evaluate(runtime, (alpha, beta))
    attribution = repository.execution_attribution("order-1")

    assert result.actions[0].risk.approved_quantity == 4
    assert attribution is not None
    assert attribution.quantity == 4
    assert [item.signed_quantity for item in attribution.contributions] == [3, 1]
    assert sum(item.signed_quantity for item in attribution.contributions) == 4


def test_dispatch_halt_stops_later_actions_and_recovered_halt_stays_stopped(tmp_path):
    alpha = _version("alpha")
    beta = _version("beta")
    source = _ProposalSource(
        {
            alpha.version_id: (_proposal(alpha, symbol="AAPL"),),
            beta.version_id: (_proposal(beta, symbol="MSFT"),),
        }
    )
    path = tmp_path / "portfolio.sqlite"
    dispatch = _RiskPath(
        dispatch=PortfolioDispatchResult(
            submitted=False, halt=True, order_id="uncertain-order", status="reconcile"
        )
    )
    runtime, repository, snapshots, _ = _runtime(
        tmp_path, (alpha, beta), source, risk_path=dispatch
    )

    first = _evaluate(runtime, (alpha, beta))
    assert first.halted is True
    assert len(dispatch.evaluations) == len(dispatch.submissions) == 1
    assert len(first.actions) == 1

    restarted = PortfolioRuntime(
        strategies=_Strategies((alpha, beta)),
        proposals=source,
        snapshots=snapshots,
        repository=SQLitePortfolioRepository(path),
        risk_path=dispatch,
    )
    recovered = _evaluate(restarted, (alpha, beta))
    assert recovered.halted is True
    assert len(recovered.actions) == 1
    assert len(dispatch.evaluations) == len(dispatch.submissions) == 1


def test_unlinked_approved_buy_reserves_cash_for_the_next_cycle(tmp_path):
    version = _version("alpha")
    source = _ProposalSource({version.version_id: (_proposal(version, quantity=5),)})
    snapshots = _Snapshots(cash=Decimal("100"))
    path = _RiskPath(
        dispatch=PortfolioDispatchResult(submitted=False, halt=True, status="durable but unknown")
    )
    runtime, _, _, _ = _runtime(tmp_path, (version,), source, risk_path=path, snapshots=snapshots)
    first = _evaluate(runtime, (version,), cycle="cycle-1")
    assert first.decisions[0].action is not None

    source.mapping[version.version_id] = (_proposal(version, quantity=6, at=NOW + timedelta(seconds=1)),)
    next_at = NOW + timedelta(seconds=1)
    second = _evaluate(runtime, (version,), at=next_at, cycle="cycle-2")

    assert second.decisions[0].decision is PortfolioVerdict.REJECT
    assert second.decisions[0].blocker is not None
    assert second.decisions[0].blocker.value == "insufficient_cash"
    assert len(path.evaluations) == 1


def test_unlinked_approved_buy_reserves_net_exposure_for_the_next_cycle(tmp_path):
    version = _version("alpha")
    source = _ProposalSource(
        {version.version_id: (_proposal(version, quantity=6, price="10"),)}
    )
    snapshots = _Snapshots(cash=Decimal("1000"))
    path = _RiskPath(
        dispatch=PortfolioDispatchResult(submitted=False, halt=True, status="unknown")
    )
    runtime, _, _, _ = _runtime(tmp_path, (version,), source, risk_path=path, snapshots=snapshots)
    _evaluate(runtime, (version,), cycle="cycle-1")

    source.mapping[version.version_id] = (
        _proposal(version, quantity=4, at=NOW + timedelta(seconds=1), price="10"),
    )
    second = _evaluate(
        runtime,
        (version,),
        at=NOW + timedelta(seconds=1),
        cycle="cycle-2",
        policy=replace(_policy((version,)), max_net_exposure=Decimal("50")),
    )

    assert second.decisions[0].decision is PortfolioVerdict.REJECT
    assert second.decisions[0].blocker is not None
    assert second.decisions[0].blocker.value == "portfolio_capital_exceeded"


def test_portfolio_cycle_evaluation_is_serialized(tmp_path):
    versions = (_version("alpha"),)
    guard = Lock()
    active = 0
    maximum = 0

    def observed_provider_call():
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        sleep(0.04)
        with guard:
            active -= 1

    source = _ProposalSource(
        {versions[0].version_id: (_proposal(versions[0]),)},
        on_call=observed_provider_call,
    )
    runtime, _, _, _ = _runtime(
        tmp_path,
        versions,
        source,
        risk_path=_RiskPath(
            dispatch=PortfolioDispatchResult(
                submitted=False, halt=True, status="unknown order identity"
            )
        ),
    )
    failures = []

    def run(cycle):
        try:
            _evaluate(runtime, versions, cycle=cycle)
        except Exception as error:  # pragma: no cover - assertion reports unexpected failure
            failures.append(error)

    workers = [Thread(target=run, args=(f"cycle-{index}",)) for index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert failures == []
    assert maximum == 1


def test_dispatch_bridge_uses_the_injected_order_dispatch_only():
    class Dispatch:
        def __init__(self):
            self.evaluated = []
            self.submitted = []

        def evaluate(self, **kwargs):
            self.evaluated.append(kwargs)
            return RiskDecision.approve(
                requested_quantity=10,
                approved_quantity=4,
                adjustments=("test cap",),
            )

        def submit(self, **kwargs):
            self.submitted.append(kwargs)

            class Outcome:
                submitted = True
                halt = False
                status = "submitted"
                intent = type("Intent", (), {"order_id": "order-bridge"})()

            return Outcome()

    version = _version("alpha")
    proposal = _proposal(version, quantity=10)
    from us_quant.trading.application.portfolio_translation import strategy_proposal_to_portfolio_intent
    from us_quant.trading.application.portfolio import CapitalAllocator

    intent = strategy_proposal_to_portfolio_intent(proposal)
    decision = CapitalAllocator().allocate(
        intents=(intent,),
        snapshot=PortfolioSnapshot(
            cash=Decimal("1000"), equity=Decimal("1000"), gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"), observed_at=NOW,
        ),
        policy=_policy((version,)),
    )[0]
    dispatch = Dispatch()
    book = SessionBook(initial_cash=Decimal("1000"), commission=Decimal("0"))
    bridge = PortfolioOrderDispatchBridge(
        dispatch=dispatch,
        book=book,
        session_id="paper-session",
        allowed_symbols=frozenset({"AAPL"}),
    )

    risk = bridge.evaluate(decision, observed_at=NOW)
    result = bridge.submit(decision, risk, observed_at=NOW)

    assert risk.approved
    assert result.submitted is True
    assert result.order_id == "order-bridge"
    assert len(dispatch.evaluated) == len(dispatch.submitted) == 1
    assert "order-bridge" in book.pending
