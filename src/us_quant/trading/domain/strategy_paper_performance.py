"""Immutable Paper performance facts and deterministic, explicit policy rules.

Exposure is time-weighted cost basis. Drawdown is realized net only; there is
no historical mark-price curve here. All accounting comes from Stage 5 replay.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json

from us_quant.trading.domain.portfolio_reconciliation import (
    PortfolioExecutionReplay, PortfolioReconciliationResult,
)
from us_quant.trading.domain.common import ZERO

EVALUATOR_VERSION = 'paper-performance-v1'
POLICY_VERSION = 'paper-performance-policy-v1'


def canonical_value(value):
    """Lossless semantic JSON values; absolute instants have one UTC spelling."""
    if is_dataclass(value):
        return {field.name: canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        require_aware(value)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, timedelta):
        return str(duration_seconds(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite Decimal')
        text = format(value, 'f')
        text = text.rstrip('0').rstrip('.') if '.' in text else text
        return '0' if value == 0 else text
    if isinstance(value, Mapping):
        return {str(key): canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    return value


def canonical_json(value) -> str:
    return json.dumps(canonical_value(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def duration_seconds(value: timedelta) -> Decimal:
    return Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1000000)


def require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('time must be timezone-aware')


class StrategyPaperPerformanceVerdict(StrEnum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    INSUFFICIENT = 'INSUFFICIENT'


class StrategyPaperPerformanceBlocker(StrEnum):
    POLICY_MISSING = 'POLICY_MISSING'
    POLICY_VERSION_UNSUPPORTED = 'POLICY_VERSION_UNSUPPORTED'
    RECONCILIATION_STALE = 'RECONCILIATION_STALE'
    RECONCILIATION_NOT_CLEAN = 'RECONCILIATION_NOT_CLEAN'
    ATTRIBUTION_INCOMPLETE = 'ATTRIBUTION_INCOMPLETE'
    FEES_INCOMPLETE = 'FEES_INCOMPLETE'
    INSUFFICIENT_SESSIONS = 'INSUFFICIENT_SESSIONS'
    INSUFFICIENT_ROUND_TRIPS = 'INSUFFICIENT_ROUND_TRIPS'
    INSUFFICIENT_EVIDENCE_DURATION = 'INSUFFICIENT_EVIDENCE_DURATION'
    MAX_DRAWDOWN_BREACHED = 'MAX_DRAWDOWN_BREACHED'
    MAX_CUMULATIVE_LOSS_BREACHED = 'MAX_CUMULATIVE_LOSS_BREACHED'
    MAX_ADVERSE_SLIPPAGE_BREACHED = 'MAX_ADVERSE_SLIPPAGE_BREACHED'
    NON_POSITIVE_NET_RESULT = 'NON_POSITIVE_NET_RESULT'
    SOURCE_TRUTH_MISSING = 'SOURCE_TRUTH_MISSING'
    SOURCE_TRUTH_INCONSISTENT = 'SOURCE_TRUTH_INCONSISTENT'


@dataclass(frozen=True, slots=True)
class StrategyPaperPerformancePolicy:
    policy_id: str
    revision: int
    policy_version: str
    minimum_distinct_sessions: int
    minimum_completed_round_trips: int
    minimum_evidence_duration: timedelta
    maximum_realized_net_drawdown: Decimal
    maximum_cumulative_loss: Decimal
    maximum_adverse_slippage: Decimal
    require_positive_net_result: bool
    require_complete_fees: bool
    require_complete_attribution: bool
    require_clean_reconciliation: bool
    maximum_reconciliation_age: timedelta
    created_at: datetime

    def __post_init__(self):
        for name in ('policy_id', 'policy_version'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonblank')
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError('revision must be positive')
        for name in ('minimum_distinct_sessions', 'minimum_completed_round_trips'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be positive')
        for name in ('minimum_evidence_duration', 'maximum_reconciliation_age'):
            if not isinstance(getattr(self, name), timedelta) or getattr(self, name) <= timedelta(0):
                raise ValueError(f'{name} must be positive')
        for name in ('maximum_realized_net_drawdown', 'maximum_cumulative_loss', 'maximum_adverse_slippage'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
                raise ValueError(f'{name} must be finite and nonnegative')
        for name in ('require_positive_net_result', 'require_complete_fees', 'require_complete_attribution', 'require_clean_reconciliation'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} must be bool')
        require_aware(self.created_at)


@dataclass(frozen=True, slots=True)
class StrategyPaperPerformanceMetrics:
    strategy_version_id: str
    window_start: datetime
    window_end: datetime
    paper_session_ids: tuple[str, ...]
    attributed_fill_count: int
    completed_round_trips: int
    gross_traded_notional: Decimal
    realized_pnl: Decimal
    fees: Decimal
    net_realized_pnl: Decimal
    total_slippage: Decimal
    adverse_slippage: Decimal
    average_cost_basis_exposure: Decimal
    max_cost_basis_exposure: Decimal
    max_realized_net_drawdown: Decimal
    fees_complete: bool
    attribution_complete: bool
    reconciliation_clean: bool
    first_evidence_at: datetime | None
    last_evidence_at: datetime | None
    evidence_duration: timedelta

    def __post_init__(self):
        require_aware(self.window_start)
        require_aware(self.window_end)
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip() or self.window_start >= self.window_end:
            raise ValueError('invalid metrics identity/window')
        if not isinstance(self.paper_session_ids, tuple) or self.paper_session_ids != tuple(sorted(set(self.paper_session_ids))):
            raise ValueError('sessions must be sorted and unique')
        for name in ('attributed_fill_count', 'completed_round_trips'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError('counts must be nonnegative integers')
        for name in ('gross_traded_notional', 'realized_pnl', 'fees', 'net_realized_pnl', 'total_slippage',
                     'adverse_slippage', 'average_cost_basis_exposure', 'max_cost_basis_exposure', 'max_realized_net_drawdown'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f'{name} must be a finite Decimal')
        for name in ('gross_traded_notional', 'adverse_slippage', 'average_cost_basis_exposure', 'max_cost_basis_exposure', 'max_realized_net_drawdown'):
            if getattr(self, name) < ZERO:
                raise ValueError(f'{name} must be nonnegative')
        if self.net_realized_pnl != self.realized_pnl - self.fees:
            raise ValueError('net result must deduct fees exactly once')
        for name in ('fees_complete', 'attribution_complete', 'reconciliation_clean'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('completeness must be bool')
        if self.first_evidence_at is None or self.last_evidence_at is None:
            if self.first_evidence_at is not None or self.last_evidence_at is not None or self.evidence_duration != timedelta(0):
                raise ValueError('invalid empty evidence duration')
        else:
            require_aware(self.first_evidence_at)
            require_aware(self.last_evidence_at)
            if not self.window_start <= self.first_evidence_at <= self.last_evidence_at <= self.window_end:
                raise ValueError('evidence outside window')
            if self.evidence_duration != self.last_evidence_at-self.first_evidence_at:
                raise ValueError('evidence duration must come from fills')


def project_strategy_paper_performance(
    *, strategy_version_id: str, window_start: datetime, window_end: datetime,
    replay: PortfolioExecutionReplay, reconciliation: PortfolioReconciliationResult | None,
) -> StrategyPaperPerformanceMetrics:
    require_aware(window_start)
    require_aware(window_end)
    if window_start >= window_end:
        raise ValueError('window must have positive duration')
    # Replay has already established pre-window basis. Projection never applies
    # fills, allocates contributions/fees or computes average acquisition costs.
    observations = tuple(item for item in replay.observations
                         if item.strategy_version_id == strategy_version_id and item.occurred_at <= window_end)
    basis = {}
    for item in observations:
        if item.occurred_at < window_start:
            basis[item.symbol] = item.cost_basis
    exposure = sum(basis.values(), ZERO)
    maximum = exposure
    integral = ZERO
    cursor = window_start
    pnl = fees = total_slippage = adverse = notional = curve = peak = drawdown = ZERO
    fees_complete = True
    sessions = set()
    executions = set()
    trips = 0
    moments = []
    for item in observations:
        if item.occurred_at < window_start:
            continue
        integral += exposure * duration_seconds(item.occurred_at - cursor)
        cursor = item.occurred_at
        basis[item.symbol] = item.cost_basis
        exposure = sum(basis.values(), ZERO)
        maximum = max(maximum, exposure)
        notional += item.gross_traded_notional
        pnl += item.realized_pnl
        fees_complete = fees_complete and item.allocated_fee is not None
        fees += item.allocated_fee if item.allocated_fee is not None else ZERO
        total_slippage += item.slippage
        adverse += max(item.slippage, ZERO)
        curve = pnl - fees
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
        trips += int(item.completed_round_trip)
        sessions.add(item.session_id)
        executions.add(item.execution_id)
        moments.append(item.occurred_at)
    integral += exposure * duration_seconds(window_end - cursor)
    first = moments[0] if moments else None
    last = moments[-1] if moments else None
    return StrategyPaperPerformanceMetrics(
        strategy_version_id, window_start, window_end, tuple(sorted(sessions)),
        len(executions), trips, notional, pnl, fees, pnl - fees,
        total_slippage, adverse, integral / duration_seconds(window_end-window_start),
        maximum, drawdown, fees_complete, replay.attribution_complete,
        reconciliation is not None and not reconciliation.blockers,
        first, last, last-first if first is not None and last is not None else timedelta(0),
    )


def evaluate_strategy_paper_performance_policy(
    *, policy: StrategyPaperPerformancePolicy | None, metrics: StrategyPaperPerformanceMetrics,
    reconciliation: PortfolioReconciliationResult | None, evaluated_at: datetime,
    source_blockers: tuple[StrategyPaperPerformanceBlocker, ...] = (),
) -> tuple[StrategyPaperPerformanceVerdict, tuple[StrategyPaperPerformanceBlocker, ...]]:
    require_aware(evaluated_at)
    B = StrategyPaperPerformanceBlocker
    hard = set(source_blockers)
    insufficient = set()
    qualification = set()
    if policy is None:
        hard.add(B.POLICY_MISSING)
    else:
        if policy.policy_version != POLICY_VERSION:
            hard.add(B.POLICY_VERSION_UNSUPPORTED)
        if reconciliation is None:
            hard.add(B.SOURCE_TRUTH_MISSING)
        else:
            moment = reconciliation.observed_at
            if (not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None
                    or moment > evaluated_at or evaluated_at-moment > policy.maximum_reconciliation_age):
                hard.add(B.RECONCILIATION_STALE)
            if policy.require_clean_reconciliation and reconciliation.blockers:
                hard.add(B.RECONCILIATION_NOT_CLEAN)
        if policy.require_complete_attribution and not metrics.attribution_complete:
            hard.add(B.ATTRIBUTION_INCOMPLETE)
        if policy.require_complete_fees and not metrics.fees_complete:
            hard.add(B.FEES_INCOMPLETE)
        if metrics.max_realized_net_drawdown > policy.maximum_realized_net_drawdown:
            hard.add(B.MAX_DRAWDOWN_BREACHED)
        if -metrics.net_realized_pnl > policy.maximum_cumulative_loss:
            hard.add(B.MAX_CUMULATIVE_LOSS_BREACHED)
        if metrics.adverse_slippage > policy.maximum_adverse_slippage:
            hard.add(B.MAX_ADVERSE_SLIPPAGE_BREACHED)
        if len(metrics.paper_session_ids) < policy.minimum_distinct_sessions:
            insufficient.add(B.INSUFFICIENT_SESSIONS)
        if metrics.completed_round_trips < policy.minimum_completed_round_trips:
            insufficient.add(B.INSUFFICIENT_ROUND_TRIPS)
        if metrics.evidence_duration < policy.minimum_evidence_duration:
            insufficient.add(B.INSUFFICIENT_EVIDENCE_DURATION)
        if not insufficient and policy.require_positive_net_result and metrics.net_realized_pnl <= ZERO:
            qualification.add(B.NON_POSITIVE_NET_RESULT)
    verdict = (StrategyPaperPerformanceVerdict.FAIL if hard else
               StrategyPaperPerformanceVerdict.INSUFFICIENT if insufficient else
               StrategyPaperPerformanceVerdict.FAIL if qualification else StrategyPaperPerformanceVerdict.PASS)
    return verdict, tuple(sorted(hard | insufficient | qualification, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class StrategyPaperPerformanceEvaluation:
    evaluation_id: str
    strategy_version_id: str
    requested_policy_id: str
    policy_id: str | None
    policy_revision: int | None
    policy_version: str | None
    window_start: datetime
    window_end: datetime
    paper_session_ids: tuple[str, ...]
    source_portfolio_decision_ids: tuple[str, ...]
    source_order_ids: tuple[str, ...]
    source_execution_ids: tuple[str, ...]
    source_digest: str
    metrics: StrategyPaperPerformanceMetrics
    verdict: StrategyPaperPerformanceVerdict
    blockers: tuple[StrategyPaperPerformanceBlocker, ...]
    reconciliation_observed_at: datetime | None
    evaluated_at: datetime
    evaluator_version: str

    def __post_init__(self):
        require_aware(self.evaluated_at)
        require_aware(self.window_start)
        require_aware(self.window_end)
        if self.window_start >= self.window_end or self.window_end > self.evaluated_at:
            raise ValueError('invalid observation window')
        for name in ('strategy_version_id', 'requested_policy_id', 'evaluator_version'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonblank')
        policy_identity = (self.policy_id, self.policy_revision, self.policy_version)
        if any(value is not None for value in policy_identity):
            if (not isinstance(self.policy_id, str) or not self.policy_id.strip()
                    or type(self.policy_revision) is not int or self.policy_revision < 1
                    or not isinstance(self.policy_version, str) or not self.policy_version.strip()):
                raise ValueError('incomplete policy identity')
            if self.requested_policy_id != self.policy_id:
                raise ValueError('requested policy identity mismatch')
        if self.reconciliation_observed_at is not None:
            require_aware(self.reconciliation_observed_at)
        if any(not isinstance(item, StrategyPaperPerformanceBlocker) for item in self.blockers):
            raise ValueError('invalid blocker')
        if self.metrics.strategy_version_id != self.strategy_version_id or self.metrics.window_start != self.window_start or self.metrics.window_end != self.window_end:
            raise ValueError('metrics identity mismatch')
        if self.paper_session_ids != self.metrics.paper_session_ids:
            raise ValueError('session identity mismatch')
        for name in ('paper_session_ids', 'source_portfolio_decision_ids', 'source_order_ids', 'source_execution_ids', 'blockers'):
            values = getattr(self, name)
            if not isinstance(values, tuple) or values != tuple(sorted(set(values))):
                raise ValueError(f'{name} must be a sorted unique tuple')
        if not isinstance(self.verdict, StrategyPaperPerformanceVerdict):
            raise ValueError('invalid verdict')
        if (self.verdict is StrategyPaperPerformanceVerdict.PASS) != (not self.blockers):
            raise ValueError('verdict/blocker mismatch')
        if len(self.source_digest) != 64 or any(c not in '0123456789abcdef' for c in self.source_digest):
            raise ValueError('invalid source digest')
        if self.evaluation_id != stable_strategy_paper_performance_evaluation_id(self):
            raise ValueError('evaluation semantic ID mismatch')


def stable_strategy_paper_performance_evaluation_id(value) -> str:
    payload = canonical_value(value) if is_dataclass(value) else dict(value)
    payload.pop('evaluation_id', None)
    payload.pop('evaluated_at', None)
    return 'paper-performance-' + digest(payload)
