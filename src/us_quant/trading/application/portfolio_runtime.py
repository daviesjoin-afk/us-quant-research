"""One serialized multi-strategy portfolio evaluation authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock

from us_quant.trading.application.portfolio import CapitalAllocator
from us_quant.trading.application.portfolio_translation import (
    strategy_proposal_to_portfolio_intent,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioDecision,
    PortfolioOpenOrder,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioVerdict,
    stable_portfolio_decision_id,
)
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
)
from us_quant.trading.domain.portfolio_runtime import (
    PortfolioActionResult,
    PortfolioCycleResult,
    PortfolioDispatchResult,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyMode,
    StrategyStatus,
    TradeProposal,
)
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort
from us_quant.trading.ports.portfolio_runtime import (
    PortfolioProposalSource,
    PortfolioRiskPath,
    PortfolioSnapshotSource,
)


class PortfolioRuntimeError(RuntimeError):
    """A portfolio cycle cannot be evaluated consistently or safely."""


class PortfolioRuntime:
    """Batch governed strategies, allocate once, persist, then pass Risk."""

    def __init__(
        self,
        *,
        strategies: StrategyApplication,
        proposals: PortfolioProposalSource,
        snapshots: PortfolioSnapshotSource,
        repository: PortfolioStateRepositoryPort,
        risk_path: PortfolioRiskPath,
        allocator: CapitalAllocator | None = None,
    ) -> None:
        self._strategies = strategies
        self._proposals = proposals
        self._snapshots = snapshots
        self._repository = repository
        self._risk_path = risk_path
        self._allocator = allocator or CapitalAllocator()
        self._cycle_lock = RLock()

    def evaluate_cycle(
        self,
        *,
        portfolio_cycle_id: str,
        observed_at: datetime,
        proposal_cutoff: datetime,
        snapshot_identity: str,
        policy: PortfolioCapitalPolicy,
        policy_identity: str,
        policy_revision: str,
        selected_version_ids: frozenset[str],
    ) -> PortfolioCycleResult:
        """Evaluate one observation window and submit each net action at most once."""

        _aware(observed_at, "observed_at")
        _aware(proposal_cutoff, "proposal_cutoff")
        if proposal_cutoff < observed_at:
            raise PortfolioRuntimeError("proposal cutoff predates the portfolio observation")
        for name, value in (
            ("portfolio_cycle_id", portfolio_cycle_id),
            ("snapshot_identity", snapshot_identity),
            ("policy_identity", policy_identity),
            ("policy_revision", policy_revision),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PortfolioRuntimeError(f"{name} must be nonblank")
        if not isinstance(policy, PortfolioCapitalPolicy):
            raise TypeError("policy must be PortfolioCapitalPolicy")
        if not isinstance(selected_version_ids, frozenset):
            raise TypeError("selected_version_ids must be a frozenset")

        # The lock spans snapshot acquisition, decision persistence, Risk and
        # dispatch. A second cycle on this runtime cannot spend the same view.
        with self._cycle_lock:
            versions = tuple(
                sorted(self._strategies.list_versions(), key=lambda item: item.version_id)
            )
            versions_by_id = {item.version_id: item for item in versions}
            unknown_selected = selected_version_ids - versions_by_id.keys()
            if unknown_selected:
                raise PortfolioRuntimeError("runtime selection contains an unknown version")

            eligible = tuple(
                version
                for version in versions
                if version.version_id in selected_version_ids
                and version.status is StrategyStatus.PAPER_SHADOW
                and version.mode is StrategyMode.PAPER_SHADOW
                and version.gate_passed
                and (allocation := policy.allocation_for(version.version_id)) is not None
                and allocation.enabled
                and allocation.capital_weight > 0
                and allocation.max_capital > 0
                and allocation.max_gross_exposure > 0
            )
            trade_proposals: list[TradeProposal] = []
            for version in eligible:
                generated = self._proposals.proposals_for(
                    version,
                    observed_at=observed_at,
                    proposal_cutoff=proposal_cutoff,
                )
                if not isinstance(generated, tuple) or any(
                    not isinstance(item, TradeProposal) for item in generated
                ):
                    raise PortfolioRuntimeError("proposal source returned an invalid batch")
                for proposal in generated:
                    if proposal.strategy != version.identity:
                        raise PortfolioRuntimeError("proposal identity does not match its governed version")
                    if proposal.generated_at != observed_at or proposal.generated_at > proposal_cutoff:
                        raise PortfolioRuntimeError("proposal belongs to a different observation window")
                    trade_proposals.append(proposal)
            intents = tuple(
                item
                for proposal in trade_proposals
                if (item := strategy_proposal_to_portfolio_intent(proposal)) is not None
            )

            snapshot = self._snapshots.snapshot(observed_at=observed_at)
            if not isinstance(snapshot, PortfolioSnapshot) or snapshot.observed_at != observed_at:
                raise PortfolioRuntimeError("portfolio snapshot does not match cycle observation")
            anticipated_ids = {
                stable_portfolio_decision_id(
                    symbol=symbol,
                    proposal_ids=tuple(
                        item.proposal_id for item in intents if item.symbol == symbol
                    ),
                    observed_at=observed_at,
                    context_identity=_decision_context(
                        portfolio_cycle_id=portfolio_cycle_id,
                        proposal_cutoff=proposal_cutoff,
                        snapshot_identity=snapshot_identity,
                        policy_identity=policy_identity,
                        policy_revision=policy_revision,
                    ),
                )
                for symbol in {item.symbol for item in intents}
            }
            snapshot = self._reserve_unlinked_approved_actions(
                snapshot,
                excluded_decision_ids=anticipated_ids,
            )
            decisions = self._allocator.allocate(
                intents=intents,
                snapshot=snapshot,
                policy=policy,
                decision_context_id=_decision_context(
                    portfolio_cycle_id=portfolio_cycle_id,
                    proposal_cutoff=proposal_cutoff,
                    snapshot_identity=snapshot_identity,
                    policy_identity=policy_identity,
                    policy_revision=policy_revision,
                ),
            )
            actions: list[PortfolioActionResult] = []
            for decision in decisions:
                candidate = PortfolioDecisionRecord(
                    decision=decision,
                    portfolio_cycle_id=portfolio_cycle_id,
                    observed_at=observed_at,
                    snapshot_identity=snapshot_identity,
                    policy_identity=policy_identity,
                    policy_revision=policy_revision,
                    created_at=observed_at,
                    proposal_cutoff=proposal_cutoff,
                )
                previous = self._repository.decision(decision.decision_id)
                record = self._repository.record_decision(candidate)
                if (
                    decision.decision is not PortfolioVerdict.APPROVE
                    or decision.action is None
                ):
                    continue
                if previous is not None and previous.risk_outcome is not None:
                    # Durable Risk state means this action was already judged;
                    # even an uncertain submit is reconciled, never regenerated.
                    recovered_dispatch = None
                    if previous.risk_outcome == "approved":
                        recovered_dispatch = PortfolioDispatchResult(
                            submitted=previous.dispatch_submitted,
                            halt=(
                                previous.dispatch_halt
                                if previous.dispatch_outcome_recorded
                                else True
                            ),
                            order_id=previous.order_id,
                            status=(
                                previous.dispatch_status or ""
                                if previous.dispatch_outcome_recorded
                                else "Risk approval persisted without a dispatch result; reconciliation required"
                            ),
                        )
                    actions.append(
                        PortfolioActionResult(
                            decision=decision,
                            risk=previous.risk_decision,
                            dispatch=recovered_dispatch,
                            recovered=True,
                        )
                    )
                    continue

                risk_decision = self._risk_path.evaluate(decision, observed_at=observed_at)
                if not isinstance(risk_decision, RiskDecision) or (
                    risk_decision.requested_quantity != decision.action.quantity
                ):
                    raise PortfolioRuntimeError("Risk returned an invalid portfolio verdict")
                risk_outcome = "approved" if risk_decision.approved else "rejected"
                record = self._repository.update_decision(
                    replace(
                        record,
                        risk_outcome=risk_outcome,
                        risk_decision=risk_decision,
                    ),
                    expected_revision=record.revision,
                )
                if not risk_decision.approved:
                    actions.append(PortfolioActionResult(decision, risk_decision))
                    continue
                dispatch = self._risk_path.submit(
                    decision,
                    risk_decision,
                    observed_at=observed_at,
                )
                if not isinstance(dispatch, PortfolioDispatchResult):
                    raise PortfolioRuntimeError("portfolio Risk path returned an invalid dispatch result")
                execution_attribution = (
                    PortfolioExecutionAttribution(
                        order_id=dispatch.order_id,
                        portfolio_decision_id=decision.decision_id,
                        symbol=decision.symbol,
                        side=decision.action.side,
                        net_quantity=decision.net_quantity,
                        attributions=decision.attribution,
                    )
                    if dispatch.order_id is not None
                    else None
                )
                record = self._repository.record_dispatch_outcome(
                    replace(
                        record,
                        order_id=dispatch.order_id,
                        dispatch_outcome_recorded=True,
                        dispatch_submitted=dispatch.submitted,
                        dispatch_halt=dispatch.halt,
                        dispatch_status=dispatch.status,
                    ),
                    execution_attribution,
                    expected_revision=record.revision,
                )
                actions.append(
                    PortfolioActionResult(decision, risk_decision, dispatch)
                )
            return PortfolioCycleResult(
                portfolio_cycle_id=portfolio_cycle_id,
                snapshot_identity=snapshot_identity,
                decisions=decisions,
                actions=tuple(actions),
            )

    def _reserve_unlinked_approved_actions(
        self,
        snapshot: PortfolioSnapshot,
        *,
        excluded_decision_ids: set[str],
    ) -> PortfolioSnapshot:
        pending = tuple(
            record
            for record in self._repository.decisions()
            if record.decision.decision_id not in excluded_decision_ids
            and record.risk_outcome == "approved"
            and record.order_id is None
            and record.decision.action is not None
        )
        reservations: list[PortfolioOpenOrder] = []
        for record in pending:
            action = record.decision.action
            assert action is not None
            if action.side is PortfolioSide.SELL:
                raise PortfolioRuntimeError(
                    "unlinked approved sell requires reconciliation before another cycle"
                )
            reservations.append(
                PortfolioOpenOrder(
                    strategy_version_id="portfolio-runtime",
                    symbol=action.symbol,
                    side=PortfolioSide.BUY,
                    notional=action.reference_price * action.quantity,
                    quantity=action.quantity,
                )
            )
        reserved_buy = sum(
            (item.notional for item in reservations if item.side is PortfolioSide.BUY),
            Decimal("0"),
        )
        return replace(
            snapshot,
            cash=max(Decimal("0"), snapshot.cash - reserved_buy),
            gross_exposure=snapshot.gross_exposure + reserved_buy,
            open_orders=tuple(sorted(
                (*snapshot.open_orders, *reservations),
                key=lambda item: (item.strategy_version_id, item.symbol, item.side.value, item.quantity),
            )),
        )


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioRuntimeError(f"{name} must be timezone-aware")


def _decision_context(
    *,
    portfolio_cycle_id: str,
    proposal_cutoff: datetime,
    snapshot_identity: str,
    policy_identity: str,
    policy_revision: str,
) -> str:
    return "\0".join(
        (
            portfolio_cycle_id,
            proposal_cutoff.isoformat(),
            snapshot_identity,
            policy_identity,
            policy_revision,
        )
    )
