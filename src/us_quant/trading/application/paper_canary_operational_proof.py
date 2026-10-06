"""Post-run audit of durable Paper facts; never owns a trading session."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from us_quant.trading.application.portfolio_reconciliation import PortfolioReconciliationApplication
from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.domain.portfolio_ledger import PortfolioDecisionRecord, PortfolioExecutionAttribution
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrderTruth, PortfolioOrderTruth,
    portfolio_order_truth_for_reconciliation, replay_portfolio_execution_truth,
)
from us_quant.trading.domain.strategy_paper_performance import (
    StrategyPaperPerformanceEvaluation, canonical_json, canonical_value, digest,
    project_strategy_paper_performance, require_aware,
)
from us_quant.trading.ports.strategy_paper_performance_repository import StrategyPaperPerformanceRepositoryNotFound
from us_quant.trading.ports.strategy_repository import StrategyRepositoryNotFound


class ProofStatus(StrEnum):
    NO_CANARY_EVIDENCE = "NO_CANARY_EVIDENCE"
    INCOMPLETE_SESSION_TRUTH = "INCOMPLETE_SESSION_TRUTH"
    RECONCILIATION_BLOCKED = "RECONCILIATION_BLOCKED"
    PERFORMANCE_MISSING = "PERFORMANCE_MISSING"
    RESTART_BASELINE_MISSING = "RESTART_BASELINE_MISSING"
    RESTART_MISMATCH = "RESTART_MISMATCH"
    OPERATIONAL_PROOF_PASS = "OPERATIONAL_PROOF_PASS"


class PerformanceComparison(StrEnum):
    DURABLE_RECOVERY = "durable-recovery"
    FRESH_RECONSTRUCTION = "fresh-reconstruction"


def _runtime_revision_known(value):
    return isinstance(value, str) and bool(value.strip())


def _identities(values: tuple[str, ...], *, name: str, required: bool = True):
    if (not isinstance(values, tuple) or (required and not values)
            or any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"{name} must contain unique nonblank identities")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class PaperCanaryOperationalProofSpec:
    strategy_version_ids: tuple[str, ...]
    expected_session_ids: tuple[str, ...]
    performance_evaluation_ids: tuple[str, ...]
    expected_runtime_revision: str | None = None

    def __post_init__(self):
        for name in ("strategy_version_ids", "expected_session_ids", "performance_evaluation_ids"):
            object.__setattr__(self, name, _identities(
                getattr(self, name), name=name, required=name != "performance_evaluation_ids",
            ))
        if self.expected_runtime_revision is not None and (
            not isinstance(self.expected_runtime_revision, str) or not self.expected_runtime_revision.strip()
        ):
            raise ValueError("expected runtime revision must be nonblank")


class PortfolioProofReadPort(Protocol):
    def decisions(self) -> tuple[PortfolioDecisionRecord, ...]: ...
    def execution_attributions(self) -> tuple[PortfolioExecutionAttribution, ...]: ...


class OrderProofReadPort(Protocol):
    def portfolio_order_truth(self) -> PortfolioOrderTruth: ...


class PerformanceProofReadPort(Protocol):
    def get_evaluation(self, evaluation_id: str) -> StrategyPaperPerformanceEvaluation: ...


class BrokerProofReadPort(Protocol):
    def broker_open_order_truth(self) -> BrokerOpenOrderTruth: ...


class StrategyProofReadPort(Protocol):
    def get_version(self, version_id: str) -> StrategyVersion: ...


@dataclass(frozen=True, slots=True)
class StrategySourceIdentity:
    strategy_version_id: str
    semver: str
    parameter_hash: str
    universe_hash: str
    code_hash: str


def _strategy_source_identity(version):
    # These immutable repository fields are also bound by the evaluator's
    # source_digest. Keep them visible even when observation times may vary.
    return StrategySourceIdentity(
        version.version_id, version.semver, version.parameter_hash,
        version.universe_hash, version.code_hash,
    )


@dataclass(frozen=True, slots=True)
class _PortfolioSnapshot:
    records: tuple[PortfolioDecisionRecord, ...]
    attributions: tuple[PortfolioExecutionAttribution, ...]

    def decisions(self):
        return self.records

    def execution_attributions(self):
        return self.attributions


@dataclass(frozen=True, slots=True)
class _OrderSnapshot:
    truth: PortfolioOrderTruth

    def portfolio_order_truth(self):
        return self.truth


@dataclass(frozen=True, slots=True)
class _BrokerSnapshot:
    truth: BrokerOpenOrderTruth

    def broker_open_order_truth(self):
        return self.truth


def _sorted_values(values):
    return tuple(sorted(values, key=canonical_json))


def _accounting_semantics(values):
    # trades_today describes the observer's calendar day, not durable ownership.
    return _sorted_values({k: v for k, v in canonical_value(x).items() if k != "trades_today"} for x in values)


def _reconciliation_semantics(result):
    return canonical_json({
        "blockers": sorted(result.blockers), "positions": _sorted_values(result.positions),
        "strategy_accounting": _accounting_semantics(result.strategy_accounting),
        "open_order_ids": sorted(result.open_order_ids),
    })


def performance_semantics(evaluation, *, mode: PerformanceComparison):
    """A new broker observation may change identity/digest, never the economics."""
    value = {
        "strategy_version_id": evaluation.strategy_version_id,
        "paper_session_ids": sorted(evaluation.paper_session_ids),
        "source_portfolio_decision_ids": sorted(evaluation.source_portfolio_decision_ids),
        "source_order_ids": sorted(evaluation.source_order_ids),
        "source_execution_ids": sorted(evaluation.source_execution_ids),
        "metrics": evaluation.metrics, "verdict": evaluation.verdict,
        "blockers": sorted(evaluation.blockers),
        "requested_policy_id": evaluation.requested_policy_id,
        "policy_id": evaluation.policy_id, "policy_revision": evaluation.policy_revision,
        "policy_version": evaluation.policy_version, "evaluator_version": evaluation.evaluator_version,
    }
    if mode is PerformanceComparison.DURABLE_RECOVERY:
        value.update(evaluation_id=evaluation.evaluation_id, source_digest=evaluation.source_digest)
    return canonical_value(value)


@dataclass(frozen=True, slots=True)
class PaperCanaryOperationalProof:
    runtime_revision: str | None
    strategy_version_ids: tuple[str, ...]
    paper_session_ids: tuple[str, ...]
    portfolio_decision_ids: tuple[str, ...]
    portfolio_cycle_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    execution_ids: tuple[str, ...]
    attribution_count: int
    fill_count: int
    open_order_ids: tuple[str, ...]
    replay_blockers: tuple[str, ...]
    attribution_complete: bool
    attributed_fill_counts: tuple[tuple[str, int], ...]
    strategy_source_identities: tuple[StrategySourceIdentity, ...]
    performance_evaluations: tuple[StrategyPaperPerformanceEvaluation, ...]
    reconciliation_semantics: str | None
    durable_truth_digest: str
    status: ProofStatus
    blockers: tuple[str, ...]

    def semantic_projection(self, mode=PerformanceComparison.DURABLE_RECOVERY):
        return canonical_value({
            "runtime_revision": self.runtime_revision,
            "strategy_version_ids": self.strategy_version_ids,
            "paper_session_ids": self.paper_session_ids,
            "portfolio_decision_ids": self.portfolio_decision_ids,
            "portfolio_cycle_ids": self.portfolio_cycle_ids,
            "order_ids": self.order_ids, "execution_ids": self.execution_ids,
            "attribution_count": self.attribution_count, "fill_count": self.fill_count,
            "open_order_ids": self.open_order_ids, "replay_blockers": self.replay_blockers,
            "attribution_complete": self.attribution_complete,
            "attributed_fill_counts": self.attributed_fill_counts,
            "strategy_source_identities": self.strategy_source_identities,
            "performance_evaluations": _sorted_values(
                performance_semantics(x, mode=mode) for x in self.performance_evaluations
            ),
            "reconciliation_semantics": self.reconciliation_semantics,
            "durable_truth_digest": self.durable_truth_digest,
        })


class PaperCanaryOperationalProofApplication:
    def __init__(self, *, spec: PaperCanaryOperationalProofSpec,
                 portfolio_repository: PortfolioProofReadPort,
                 order_truth: OrderProofReadPort, evaluations: PerformanceProofReadPort,
                 strategies: StrategyProofReadPort,
                 broker_order_truth: BrokerProofReadPort | None = None):
        self.spec = spec
        self._portfolio = portfolio_repository
        self._orders = order_truth
        self._evaluations = evaluations
        self._strategies = strategies
        self._broker_orders = broker_order_truth

    def inspect(self, *, now: datetime, runtime_revision: str | None,
                broker: BrokerAccountPortfolio | None = None) -> PaperCanaryOperationalProof:
        require_aware(now)
        decisions = self._portfolio.decisions()
        attrs = self._portfolio.execution_attributions()
        orders = self._orders.portfolio_order_truth()
        broker_orders = self._broker_orders.broker_open_order_truth() if self._broker_orders else None
        versions = set(self.spec.strategy_version_ids)
        sessions = set(self.spec.expected_session_ids)
        campaign_orders = tuple(x for x in orders.orders if x.intent and x.intent.session_id in sessions)
        order_ids = {x.intent.order_id for x in campaign_orders}
        linked_ids = {x.portfolio_decision_id for x in attrs if x.order_id in order_ids}
        cycles = {x.portfolio_cycle_id for x in decisions if x.decision.decision_id in linked_ids or x.order_id in order_ids}
        campaign_decisions = tuple(x for x in decisions if (
            x.decision.decision_id in linked_ids or x.order_id in order_ids
            or (x.portfolio_cycle_id in cycles and versions.intersection(x.decision.strategy_version_ids))
            or (not campaign_orders and versions.intersection(x.decision.strategy_version_ids))
        ))
        campaign_attrs = tuple(x for x in attrs if x.order_id in order_ids)
        fills = tuple(f for x in campaign_orders for f in x.fills)
        present_versions = {v for x in campaign_decisions for v in x.decision.strategy_version_ids}
        present_sessions = {x.intent.session_id for x in campaign_orders}
        failures: dict[ProofStatus, set[str]] = {}

        def block(status, reason):
            failures.setdefault(status, set()).add(reason)

        source_identities = []
        for version_id in self.spec.strategy_version_ids:
            try:
                version = self._strategies.get_version(version_id)
            except StrategyRepositoryNotFound:
                block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "strategy_source_identity_missing")
                continue
            identity = _strategy_source_identity(version)
            if identity.strategy_version_id != version_id or any(
                not isinstance(value, str) or not value.strip()
                for value in (identity.semver, identity.parameter_hash, identity.universe_hash, identity.code_hash)
            ):
                block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "strategy_source_identity_invalid")
            source_identities.append(identity)

        if not campaign_decisions and not campaign_orders:
            block(ProofStatus.NO_CANARY_EVIDENCE, "no_canary_evidence")
        if not campaign_decisions or not campaign_orders or not fills:
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "decision_order_fill_required")
        if present_versions != versions:
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "exact_strategy_set_mismatch")
        if present_sessions != sessions:
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "exact_session_set_mismatch")
        if any(x.order_id is not None and x.order_id not in order_ids for x in campaign_decisions):
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "decision_order_outside_session")
        if any(x.intent.order_id not in {a.order_id for a in campaign_attrs} for x in campaign_orders):
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "missing_execution_attribution")
        if any(
            (x.intent is not None and x.intent.created_at > now)
            or any(e.occurred_at > now for e in x.events)
            or any(f.occurred_at > now for f in x.fills)
            for x in orders.orders
        ):
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "order_truth_in_future")
        if any(x.created_at > now or x.observed_at > now for x in decisions):
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "decision_truth_in_future")
        if not _runtime_revision_known(runtime_revision):
            block(ProofStatus.RESTART_MISMATCH, "runtime_revision_unknown")
        if self.spec.expected_runtime_revision is not None and runtime_revision != self.spec.expected_runtime_revision:
            block(ProofStatus.RESTART_MISMATCH, "runtime_revision_mismatch")

        aliases = {x.account_alias for x in orders.orders if x.account_alias}
        alias = broker.account.account_alias if broker else next(iter(aliases), "")
        replay_orders = portfolio_order_truth_for_reconciliation(
            order_truth=orders, broker_order_truth=broker_orders,
            decisions=decisions, execution_attributions=attrs,
        ) if broker_orders is not None else orders
        # One canonical replay, over the full account input, detects unrelated dirty truth too.
        replay = replay_portfolio_execution_truth(
            now=now, account_alias=alias, order_truth=replay_orders,
            decisions=decisions, execution_attributions=attrs,
        )
        if replay.blockers:
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "replay_blocked")
        if not replay.attribution_complete:
            block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "attribution_incomplete")
        reconciliation = None
        if broker is None or broker_orders is None:
            block(ProofStatus.RECONCILIATION_BLOCKED, "broker_observation_missing")
        else:
            if broker.account.environment is not Environment.PAPER:
                block(ProofStatus.RECONCILIATION_BLOCKED, "paper_broker_required")
            reconciliation = PortfolioReconciliationApplication(
                portfolio_repository=_PortfolioSnapshot(decisions, attrs),
                order_truth=_OrderSnapshot(orders), broker_order_truth=_BrokerSnapshot(broker_orders),
            ).reconcile(broker=broker, now=now)
            if reconciliation.blockers:
                block(ProofStatus.RECONCILIATION_BLOCKED, "canonical_reconciliation_blocked")

        evaluations = []
        for evaluation_id in self.spec.performance_evaluation_ids:
            try:
                evaluations.append(self._evaluations.get_evaluation(evaluation_id))
            except StrategyPaperPerformanceRepositoryNotFound:
                block(ProofStatus.PERFORMANCE_MISSING, "performance_evaluation_missing")
        if len(evaluations) != len(versions) or {x.strategy_version_id for x in evaluations} != versions:
            block(ProofStatus.PERFORMANCE_MISSING, "exact_performance_strategy_set_required")
        if {s for x in evaluations for s in x.paper_session_ids} != sessions:
            block(ProofStatus.PERFORMANCE_MISSING, "performance_session_linkage_mismatch")
        # Evaluations also retain pre-window fills for canonical cost basis.
        # They must cover all campaign fills, but may legitimately source older sessions.
        if not {x.execution_id for x in fills} <= {e for x in evaluations for e in x.source_execution_ids}:
            block(ProofStatus.PERFORMANCE_MISSING, "performance_does_not_cover_all_session_fills")
        for evaluation in evaluations:
            if evaluation.evaluated_at > now:
                block(ProofStatus.PERFORMANCE_MISSING, "performance_evaluated_in_future")
            if (not set(evaluation.source_portfolio_decision_ids) <= {x.decision.decision_id for x in decisions}
                    or not set(evaluation.source_order_ids) <= {x.intent.order_id for x in orders.orders if x.intent}
                    or not set(evaluation.source_execution_ids) <= {f.execution_id for x in orders.orders for f in x.fills}):
                block(ProofStatus.PERFORMANCE_MISSING, "performance_source_linkage_mismatch")
            # The existing domain projector validates economics without evaluate()/record_evaluation().
            cutoff_replay = replay_portfolio_execution_truth(
                now=now, account_alias=alias, order_truth=replay_orders, decisions=decisions,
                execution_attributions=attrs, through_at=evaluation.window_end,
            )
            metrics = project_strategy_paper_performance(
                strategy_version_id=evaluation.strategy_version_id,
                window_start=evaluation.window_start, window_end=evaluation.window_end,
                replay=cutoff_replay, reconciliation=reconciliation,
            )
            source_fills = tuple(x for x in cutoff_replay.attributed_fills if x.strategy_version_id == evaluation.strategy_version_id)
            expected_sources = (
                tuple(sorted({x.portfolio_decision_id for x in source_fills})),
                tuple(sorted({x.order_id for x in source_fills})),
                tuple(sorted({x.execution_id for x in source_fills})),
            )
            if expected_sources != (evaluation.source_portfolio_decision_ids, evaluation.source_order_ids, evaluation.source_execution_ids):
                block(ProofStatus.PERFORMANCE_MISSING, "performance_source_replay_mismatch")
            if reconciliation is not None and canonical_json(metrics) != canonical_json(evaluation.metrics):
                block(ProofStatus.PERFORMANCE_MISSING, "performance_metrics_replay_mismatch")

        truth_digest = digest({
            "strategy_source_identities": _sorted_values(source_identities),
            "decisions": _sorted_values(decisions), "attributions": _sorted_values(attrs),
            "orders": _sorted_values({
                "intent": x.intent, "account_alias": x.account_alias, "broker_order_id": x.broker_order_id,
                "events": _sorted_values(x.events), "fills": _sorted_values(x.fills),
            } for x in orders.orders),
        })
        # Snapshot eligibility is not proof that a process restart has happened.
        priority = (ProofStatus.NO_CANARY_EVIDENCE, ProofStatus.RESTART_MISMATCH,
                    ProofStatus.INCOMPLETE_SESSION_TRUTH, ProofStatus.RECONCILIATION_BLOCKED,
                    ProofStatus.PERFORMANCE_MISSING)
        status = next((x for x in priority if x in failures), ProofStatus.RESTART_BASELINE_MISSING)
        blockers = tuple(sorted({x for items in failures.values() for x in items}))
        return PaperCanaryOperationalProof(
            runtime_revision, tuple(sorted(present_versions)), tuple(sorted(present_sessions)),
            tuple(sorted(x.decision.decision_id for x in campaign_decisions)),
            tuple(sorted({x.portfolio_cycle_id for x in campaign_decisions})),
            tuple(sorted(order_ids)), tuple(sorted(x.execution_id for x in fills)),
            len(campaign_attrs), len(fills), tuple(sorted(replay.open_order_ids)),
            tuple(sorted(replay.blockers)), replay.attribution_complete,
            tuple((v, sum(1 for x in replay.attributed_fills if x.strategy_version_id == v and x.order_id in order_ids))
                  for v in sorted(versions)),
            tuple(source_identities),
            tuple(sorted(evaluations, key=lambda x: x.evaluation_id)),
            _reconciliation_semantics(reconciliation) if reconciliation else None,
            truth_digest, status, blockers,
        )


def compare_restart(baseline: Mapping | None, current: PaperCanaryOperationalProof,
                    *, mode=PerformanceComparison.DURABLE_RECOVERY) -> PaperCanaryOperationalProof:
    """Compare an edge-validated baseline; a PASS has no trading authority."""
    if not isinstance(mode, PerformanceComparison):
        raise ValueError("unknown comparison mode")
    if baseline is None:
        return replace(current, status=ProofStatus.RESTART_BASELINE_MISSING,
                       blockers=tuple(sorted(set(current.blockers) | {"restart_baseline_missing"})))
    if baseline.get("snapshot_status") != ProofStatus.RESTART_BASELINE_MISSING or baseline.get("snapshot_blockers") != []:
        return replace(current, status=ProofStatus.RESTART_MISMATCH, blockers=("baseline_not_eligible",))
    if current.blockers:
        return current
    if not _runtime_revision_known(baseline.get("runtime_revision")) or not _runtime_revision_known(current.runtime_revision):
        return replace(current, status=ProofStatus.RESTART_MISMATCH,
                       blockers=("runtime_revision_unknown",))
    if mode is PerformanceComparison.FRESH_RECONSTRUCTION and (
        set(baseline.get("performance_evaluation_ids", ()))
        & {x.evaluation_id for x in current.performance_evaluations}
    ):
        return replace(current, status=ProofStatus.RESTART_MISMATCH,
                       blockers=("fresh_evaluation_required",))
    if mode is PerformanceComparison.FRESH_RECONSTRUCTION:
        generated_at = datetime.fromisoformat(baseline["generated_at"])
        require_aware(generated_at)
        if any(x.evaluated_at <= generated_at for x in current.performance_evaluations):
            return replace(current, status=ProofStatus.RESTART_MISMATCH,
                           blockers=("fresh_evaluation_must_postdate_baseline",))
    key = "semantic_projection" if mode is PerformanceComparison.DURABLE_RECOVERY else "fresh_semantic_projection"
    if canonical_json(baseline.get(key)) != canonical_json(current.semantic_projection(mode)):
        return replace(current, status=ProofStatus.RESTART_MISMATCH, blockers=("restart_semantic_mismatch",))
    return replace(current, status=ProofStatus.OPERATIONAL_PROOF_PASS, blockers=())
