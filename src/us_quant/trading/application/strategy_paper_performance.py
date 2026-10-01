"""Read durable truth, evaluate a Paper policy, persist an immutable fact."""
from datetime import datetime
from dataclasses import replace

from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus
from us_quant.trading.domain.portfolio_reconciliation import (
    replay_portfolio_execution_truth, reconcile_portfolio_truth,
    portfolio_order_truth_for_reconciliation,
)
from us_quant.trading.domain.strategy_paper_performance import (
    EVALUATOR_VERSION, StrategyPaperPerformanceBlocker,
    StrategyPaperPerformanceEvaluation, digest,
    evaluate_strategy_paper_performance_policy, project_strategy_paper_performance,
    stable_strategy_paper_performance_evaluation_id, require_aware, canonical_json, canonical_value,
)
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort
from us_quant.trading.ports.portfolio_order_truth import PortfolioOrderTruthSource
from us_quant.trading.ports.broker_open_order_truth import BrokerOpenOrderTruthSource
from us_quant.trading.ports.strategy_repository import StrategyRepositoryPort
from us_quant.trading.ports.strategy_paper_performance_repository import (
    StrategyPaperPerformancePolicyStorePort, StrategyPaperPerformanceRepositoryPort,
)


class StrategyPaperPerformanceApplication:
    def __init__(self, *, strategies: StrategyRepositoryPort,
                 portfolio_repository: PortfolioStateRepositoryPort,
                 order_truth: PortfolioOrderTruthSource,
                 broker_order_truth: BrokerOpenOrderTruthSource,
                 policies: StrategyPaperPerformancePolicyStorePort,
                 evaluations: StrategyPaperPerformanceRepositoryPort):
        self._strategies = strategies
        self._portfolio = portfolio_repository
        self._orders = order_truth
        self._broker_orders = broker_order_truth
        self._policies = policies
        self._evaluations = evaluations

    def evaluate(self, *, strategy_version_id: str, policy_id: str,
                 window_start: datetime, window_end: datetime,
                 broker: BrokerAccountPortfolio, evaluated_at: datetime):
        require_aware(evaluated_at)
        require_aware(window_start)
        require_aware(window_end)
        if window_start >= window_end or window_end > evaluated_at:
            raise ValueError('invalid observation window')
        # The version is loaded from governance storage; callers cannot invent
        # a paper version or supply a passing boolean.
        version = self._strategies.get_version(strategy_version_id)
        if version.mode is not StrategyMode.PAPER_SHADOW or version.status not in (StrategyStatus.PAPER_SHADOW, StrategyStatus.PAUSED):
            raise ValueError('version must have entered Paper')
        if broker.account.environment.value != 'paper':
            raise ValueError('Paper broker truth required')
        policy = self._policies.active_policy(policy_id)
        decisions = self._portfolio.decisions()
        attributions = self._portfolio.execution_attributions()
        broker_orders = self._broker_orders.broker_open_order_truth()
        orders = portfolio_order_truth_for_reconciliation(
            order_truth=self._orders.portfolio_order_truth(), broker_order_truth=broker_orders,
            decisions=decisions, execution_attributions=attributions,
        )
        # Both projections consume the exact same loaded snapshot. No second
        # application load can pair a new fill with an older clean reconciliation.
        reconciliation = reconcile_portfolio_truth(
            now=evaluated_at, broker=broker, broker_order_truth=broker_orders,
            order_truth=orders, decisions=decisions, execution_attributions=attributions,
        )
        replay = replay_portfolio_execution_truth(
            now=evaluated_at, account_alias=broker.account.account_alias,
            order_truth=orders, decisions=decisions, execution_attributions=attributions,
            through_at=window_end,
        )
        observation_times = tuple(sorted((broker.account.observed_at, broker_orders.observed_at,
                                          *(item.observed_at for item in broker.positions))))
        reconciliation = replace(reconciliation, observed_at=min(observation_times))
        metrics = project_strategy_paper_performance(
            strategy_version_id=strategy_version_id, window_start=window_start,
            window_end=window_end, replay=replay, reconciliation=reconciliation,
        )
        source_blockers = set()
        if replay.blockers:
            source_blockers.add(StrategyPaperPerformanceBlocker.SOURCE_TRUTH_INCONSISTENT)
        # Broker observations, rather than the projection's construction time,
        # prove freshness. The reconciliation also retains STALE_SNAPSHOT.
        if policy is not None and any(
            moment > evaluated_at or evaluated_at-moment > policy.maximum_reconciliation_age
            for moment in observation_times
        ):
            source_blockers.add(StrategyPaperPerformanceBlocker.RECONCILIATION_STALE)
        verdict, blockers = evaluate_strategy_paper_performance_policy(
            policy=policy, metrics=metrics, reconciliation=reconciliation,
            evaluated_at=evaluated_at, source_blockers=tuple(source_blockers),
        )
        facts = tuple(item for item in replay.attributed_fills
                      if item.strategy_version_id == strategy_version_id and item.occurred_at <= window_end)
        # Full contributing orders and decisions retain pre-window basis. All
        # account-scoped truth is additionally committed to detect dirty sources.
        source_digest = digest({
            'version': {'strategy_version_id': version.version_id, 'semver': version.semver,
                        'parameter_hash': version.parameter_hash, 'universe_hash': version.universe_hash,
                        'code_hash': version.code_hash},
            'window': (window_start, window_end),
            'decisions': sorted(decisions, key=lambda x: (x.decision.decision_id, canonical_json(x))),
            'attributions': sorted(attributions, key=canonical_json),
            'orders': sorted(({
                'intent': order.intent, 'account_alias': order.account_alias,
                'broker_order_id': order.broker_order_id,
                'events': sorted(order.events, key=canonical_json),
                'fills': sorted(order.fills, key=lambda x: (x.occurred_at, x.execution_id, x.order_id)),
            } for order in orders.orders), key=canonical_json),
            'sessions': metrics.paper_session_ids,
            'reconciliation': {
                'observed_at': reconciliation.observed_at, 'blockers': reconciliation.blockers,
                'positions': reconciliation.positions, 'open_order_ids': reconciliation.open_order_ids,
                'accounting': [{key: value for key, value in canonical_value(item).items()
                                if key != 'trades_today'} for item in reconciliation.strategy_accounting],
            },
            'broker_observation_times': observation_times,
        })
        payload = dict(
            strategy_version_id=strategy_version_id,
            policy_id=policy.policy_id if policy else None,
            policy_revision=policy.revision if policy else None,
            policy_version=policy.policy_version if policy else None,
            window_start=window_start, window_end=window_end,
            paper_session_ids=metrics.paper_session_ids,
            source_portfolio_decision_ids=tuple(sorted({item.portfolio_decision_id for item in facts})),
            source_order_ids=tuple(sorted({item.order_id for item in facts})),
            source_execution_ids=tuple(sorted({item.execution_id for item in facts})),
            source_digest=source_digest, metrics=metrics, verdict=verdict, blockers=blockers,
            reconciliation_observed_at=reconciliation.observed_at,
            evaluated_at=evaluated_at, evaluator_version=EVALUATOR_VERSION,
        )
        evaluation = StrategyPaperPerformanceEvaluation(
            evaluation_id=stable_strategy_paper_performance_evaluation_id(payload), **payload,
        )
        self._evaluations.record_evaluation(evaluation)
        return self._evaluations.get_evaluation(evaluation.evaluation_id)
