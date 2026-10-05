"""Read-only diagnostic projection for a supervised Paper canary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from us_quant.trading.application.market_evidence_readiness import (
    REQUIRED_CAPTURE_SESSIONS,
    MarketEvidenceReadinessApplication,
)
from us_quant.trading.application.portfolio_reconciliation import (
    DEFAULT_MAX_RECONCILIATION_SNAPSHOT_AGE,
)
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus


class PaperCanaryInspectionStatus(StrEnum):
    BASELINE_MISMATCH = "BASELINE_MISMATCH"
    DATA_COLLECTION = "DATA_COLLECTION"
    GOVERNANCE_NOT_READY = "GOVERNANCE_NOT_READY"
    PORTFOLIO_NOT_READY = "PORTFOLIO_NOT_READY"
    BROKER_NOT_READY = "BROKER_NOT_READY"
    RECONCILIATION_NOT_READY = "RECONCILIATION_NOT_READY"
    READY_FOR_LIVE_PREFLIGHT = "READY_FOR_LIVE_PREFLIGHT"
    READY_FOR_CANARY = "READY_FOR_CANARY"


class PaperPerformanceObservation(StrEnum):
    NOT_YET_OBSERVED = "NOT_YET_OBSERVED"
    INSUFFICIENT = "INSUFFICIENT"
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PaperCanaryEvidenceTarget:
    strategy_version_id: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be blank")
        if not self.symbol.strip():
            raise ValueError("symbol must not be blank")
        object.__setattr__(
            self, "strategy_version_id", self.strategy_version_id.strip()
        )
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class PaperCanaryInspectionSpec:
    strategy_version_ids: tuple[str, ...]
    evidence_targets: tuple[PaperCanaryEvidenceTarget, ...]
    provider: str
    expected_runtime_revision: str | None = None

    def __post_init__(self) -> None:
        if not self.strategy_version_ids or any(
            not isinstance(item, str) or not item.strip()
            for item in self.strategy_version_ids
        ):
            raise ValueError("at least one nonblank strategy version is required")
        object.__setattr__(
            self,
            "strategy_version_ids",
            tuple(item.strip() for item in self.strategy_version_ids),
        )
        if len(set(self.strategy_version_ids)) != len(self.strategy_version_ids):
            raise ValueError("strategy version ids must be unique")
        if not self.evidence_targets:
            raise ValueError("at least one evidence target is required")
        if any(
            not isinstance(item, PaperCanaryEvidenceTarget)
            for item in self.evidence_targets
        ):
            raise TypeError("evidence_targets must contain PaperCanaryEvidenceTarget")
        if len(set(self.evidence_targets)) != len(self.evidence_targets):
            raise ValueError("evidence targets must be unique")
        if any(
            item.strategy_version_id not in self.strategy_version_ids
            for item in self.evidence_targets
        ):
            raise ValueError("every evidence target must name an inspected version")
        targeted_versions = {item.strategy_version_id for item in self.evidence_targets}
        if targeted_versions != set(self.strategy_version_ids):
            raise ValueError("every inspected version must have an evidence target")
        if not self.provider.strip():
            raise ValueError("provider must not be blank")
        if (
            self.expected_runtime_revision is not None
            and not self.expected_runtime_revision.strip()
        ):
            raise ValueError("expected_runtime_revision must be nonblank or None")
        if self.expected_runtime_revision is not None:
            object.__setattr__(
                self,
                "expected_runtime_revision",
                self.expected_runtime_revision.strip(),
            )
        object.__setattr__(
            self, "strategy_version_ids", tuple(sorted(self.strategy_version_ids))
        )
        object.__setattr__(
            self,
            "evidence_targets",
            tuple(
                sorted(
                    self.evidence_targets,
                    key=lambda item: (item.strategy_version_id, item.symbol),
                )
            ),
        )
        object.__setattr__(self, "provider", self.provider.strip().upper())


@dataclass(frozen=True, slots=True)
class StrategyReadinessProjection:
    version_id: str
    strategy_id: str
    semver: str
    parameter_hash: str
    universe_hash: str
    code_hash: str
    status: str
    mode: str
    legacy_gate_passed: bool
    latest_authentication: object | None
    latest_gate: object | None
    latest_coverage: object | None
    latest_lifecycle_decision: object | None
    latest_paper_performance: object | None
    paper_performance_status: PaperPerformanceObservation
    paper_launch_authorized: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceTargetProjection:
    strategy_version_id: str
    symbol: str
    captured_session_count: int
    robustness_usable_sessions: int
    high_quality_sessions: int
    review_ready_sessions: int
    required_sessions: int
    remaining_sessions: int
    latest_session: str | None
    latest_session_quality: object | None
    status: str
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioAllocationProjection:
    strategy_version_id: str
    capital_weight: str
    max_capital: str
    max_gross_exposure: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class PortfolioPlanProjection:
    status: str
    plan_id: str | None = None
    revision: int | None = None
    selected_version_ids: tuple[str, ...] = ()
    updated_at: datetime | None = None
    hard_limits: tuple[tuple[str, str], ...] = ()
    allocations: tuple[PortfolioAllocationProjection, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BrokerCheckProjection:
    checked: bool
    refresh_succeeded: bool
    configured_environment: str
    configured_host: str
    configured_port: int
    configured_client_id: int
    paper_order_submission_enabled: bool
    api_read_only: bool
    account_alias: str | None = None
    broker_environment: str | None = None
    observed_at: datetime | None = None
    freshness: str = "NOT_RUN"
    age_seconds: float | None = None
    net_liquidation_available: bool = False
    cash_available: bool = False
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationProjection:
    status: str
    blockers: tuple[str, ...]
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PaperCanaryReadinessReport:
    current_runtime_revision: str
    expected_runtime_revision: str | None
    overall_status: PaperCanaryInspectionStatus
    strategies: tuple[StrategyReadinessProjection, ...]
    evidence_targets: tuple[EvidenceTargetProjection, ...]
    portfolio_plan: PortfolioPlanProjection
    broker_check: BrokerCheckProjection
    reconciliation: ReconciliationProjection
    stage6_f_blocker: str
    blockers: tuple[str, ...]


class _VersionReader(Protocol):
    def list_versions(self) -> tuple: ...


class _LatestReader(Protocol):
    def latest_for_version(self, version_id: str) -> object | None: ...


class _LifecycleReader(Protocol):
    def decisions_for_version(self, version_id: str) -> tuple: ...


class _PlanRepository(Protocol):
    def load(self) -> object | None: ...


class _PlanApplication(Protocol):
    def load(self) -> object: ...


class _PaperLaunchAuthorizer(Protocol):
    def authorises(self, version_id: str) -> bool: ...


class PaperCanaryReadinessApplication:
    """Combine durable facts into a diagnosis; never grants operating authority."""

    def __init__(
        self,
        *,
        spec: PaperCanaryInspectionSpec,
        strategies: _VersionReader,
        authentications: _LatestReader,
        gates: _LatestReader,
        coverages: _LatestReader,
        lifecycle_decisions: _LifecycleReader,
        paper_performance: _LatestReader,
        paper_launch_authorizer: _PaperLaunchAuthorizer,
        market_evidence_readiness: MarketEvidenceReadinessApplication,
        portfolio_plan_repository: _PlanRepository,
        portfolio_plan_application: _PlanApplication,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._spec = spec
        self._strategies = strategies
        self._authentications = authentications
        self._gates = gates
        self._coverages = coverages
        self._lifecycle_decisions = lifecycle_decisions
        self._paper_performance = paper_performance
        self._paper_launch_authorizer = paper_launch_authorizer
        self._market_evidence_readiness = market_evidence_readiness
        self._portfolio_plan_repository = portfolio_plan_repository
        self._portfolio_plan_application = portfolio_plan_application
        self._clock = clock or (lambda: datetime.now(UTC))

    def inspect(
        self,
        *,
        current_runtime_revision: str | None,
        broker_check: BrokerCheckProjection | None = None,
        reconciliation_result: object | None = None,
    ) -> PaperCanaryReadinessReport:
        revision = current_runtime_revision or "UNKNOWN"
        try:
            versions = {
                item.version_id: item for item in self._strategies.list_versions()
            }
        except Exception:  # noqa: BLE001 - read-only inspector fails closed
            versions = {}
        version_projections = tuple(
            self._strategy_projection(version_id, versions.get(version_id))
            for version_id in self._spec.strategy_version_ids
        )
        evidence = tuple(
            self._evidence_projection(target, versions.get(target.strategy_version_id))
            for target in self._spec.evidence_targets
        )
        plan = self._plan_projection()
        broker = broker_check or BrokerCheckProjection(
            checked=False,
            refresh_succeeded=False,
            configured_environment="UNKNOWN",
            configured_host="UNKNOWN",
            configured_port=0,
            configured_client_id=0,
            paper_order_submission_enabled=False,
            api_read_only=False,
        )
        reconciliation = self._reconciliation_projection(reconciliation_result, broker)
        blockers: list[str] = []
        if (
            self._spec.expected_runtime_revision is not None
            and revision != self._spec.expected_runtime_revision
        ):
            status = PaperCanaryInspectionStatus.BASELINE_MISMATCH
            blockers.append("RUNTIME_REVISION_MISMATCH")
        elif any(item.status != "READY" for item in evidence):
            status = PaperCanaryInspectionStatus.DATA_COLLECTION
            blockers.extend(
                f"{item.strategy_version_id}/{item.symbol}:{blocker}"
                for item in evidence
                if item.status != "READY"
                for blocker in (item.blockers or ("NOT_READY",))
            )
        elif any(not self._governance_ready(item) for item in version_projections):
            status = PaperCanaryInspectionStatus.GOVERNANCE_NOT_READY
            blockers.extend(
                f"{item.version_id}:GOVERNANCE_NOT_READY"
                for item in version_projections
                if not self._governance_ready(item)
            )
        elif plan.status != "VALID" or not self._plan_matches(plan):
            status = PaperCanaryInspectionStatus.PORTFOLIO_NOT_READY
            blockers.extend(plan.blockers or ("PORTFOLIO_PLAN_TARGET_MISMATCH",))
            if plan.status == "VALID" and not self._plan_matches(plan):
                blockers.append("PORTFOLIO_PLAN_TARGET_MISMATCH")
        elif broker_check is None:
            status = PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT
            blockers.append("LIVE_BROKER_PREFLIGHT_NOT_RUN")
        elif not self._broker_ready(broker):
            status = PaperCanaryInspectionStatus.BROKER_NOT_READY
            blockers.extend(self._broker_blockers(broker))
        elif reconciliation.status != "CLEAN":
            status = PaperCanaryInspectionStatus.RECONCILIATION_NOT_READY
            blockers.extend(reconciliation.blockers or ("RECONCILIATION_NOT_READY",))
        else:
            status = PaperCanaryInspectionStatus.READY_FOR_CANARY

        return PaperCanaryReadinessReport(
            current_runtime_revision=revision,
            expected_runtime_revision=self._spec.expected_runtime_revision,
            overall_status=status,
            strategies=version_projections,
            evidence_targets=evidence,
            portfolio_plan=plan,
            broker_check=broker,
            reconciliation=reconciliation,
            stage6_f_blocker="Stage 6-D supervised operational canary incomplete",
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def _strategy_projection(
        self, version_id: str, version
    ) -> StrategyReadinessProjection:
        if version is None:
            return StrategyReadinessProjection(
                version_id=version_id,
                strategy_id="UNKNOWN",
                semver="UNKNOWN",
                parameter_hash="UNKNOWN",
                universe_hash="UNKNOWN",
                code_hash="UNKNOWN",
                status="MISSING",
                mode="MISSING",
                legacy_gate_passed=False,
                latest_authentication=None,
                latest_gate=None,
                latest_coverage=None,
                latest_lifecycle_decision=None,
                latest_paper_performance=None,
                paper_performance_status=PaperPerformanceObservation.NOT_YET_OBSERVED,
                paper_launch_authorized=False,
                blockers=("STRATEGY_VERSION_MISSING",),
            )
        blockers: list[str] = []
        authentication = self._latest(
            self._authentications, version_id, "AUTHENTICATION_READ_FAILED", blockers
        )
        gate = self._latest(self._gates, version_id, "GATE_READ_FAILED", blockers)
        coverage = self._latest(
            self._coverages, version_id, "COVERAGE_READ_FAILED", blockers
        )
        performance = self._latest(
            self._paper_performance, version_id, "PERFORMANCE_READ_FAILED", blockers
        )
        decision = self._latest_lifecycle(version_id, blockers)
        try:
            authorized = bool(self._paper_launch_authorizer.authorises(version_id))
        except Exception:  # noqa: BLE001 - unreadable evidence facts fail closed
            authorized = False
            blockers.append("PAPER_LAUNCH_AUTHORIZATION_READ_FAILED")
        return StrategyReadinessProjection(
            version_id=version_id,
            strategy_id=version.strategy_id,
            semver=version.semver,
            parameter_hash=version.parameter_hash,
            universe_hash=version.universe_hash,
            code_hash=version.code_hash,
            status=version.status.value,
            mode=version.mode.value,
            legacy_gate_passed=version.gate_passed,
            latest_authentication=authentication,
            latest_gate=gate,
            latest_coverage=coverage,
            latest_lifecycle_decision=decision,
            latest_paper_performance=performance,
            paper_performance_status=_performance_status(performance),
            paper_launch_authorized=authorized,
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def _latest(self, reader, version_id: str, blocker: str, blockers: list[str]):
        try:
            return reader.latest_for_version(version_id)
        except Exception:  # noqa: BLE001 - unreadable durable projection fails closed
            blockers.append(blocker)
            return None

    def _latest_lifecycle(self, version_id: str, blockers: list[str]):
        try:
            decisions = self._lifecycle_decisions.decisions_for_version(version_id)
            if not decisions:
                return None
            return max(decisions, key=_lifecycle_sort_key)
        except Exception:  # noqa: BLE001 - unreadable lifecycle facts fail closed
            blockers.append("LIFECYCLE_READ_FAILED")
            return None

    def _evidence_projection(
        self, target: PaperCanaryEvidenceTarget, version
    ) -> EvidenceTargetProjection:
        if version is None:
            return EvidenceTargetProjection(
                strategy_version_id=target.strategy_version_id,
                symbol=target.symbol,
                captured_session_count=0,
                robustness_usable_sessions=0,
                high_quality_sessions=0,
                review_ready_sessions=0,
                required_sessions=REQUIRED_CAPTURE_SESSIONS,
                remaining_sessions=REQUIRED_CAPTURE_SESSIONS,
                latest_session=None,
                latest_session_quality=None,
                status="UNAVAILABLE",
                blockers=("STRATEGY_VERSION_MISSING",),
            )
        try:
            value = self._market_evidence_readiness.inspect(
                symbol=target.symbol,
                provider=self._spec.provider,
                parameters=version.parameters,
            )
        except Exception:  # noqa: BLE001 - unreadable market data fails closed
            return EvidenceTargetProjection(
                strategy_version_id=target.strategy_version_id,
                symbol=target.symbol,
                captured_session_count=0,
                robustness_usable_sessions=0,
                high_quality_sessions=0,
                review_ready_sessions=0,
                required_sessions=REQUIRED_CAPTURE_SESSIONS,
                remaining_sessions=REQUIRED_CAPTURE_SESSIONS,
                latest_session=None,
                latest_session_quality=None,
                status="UNAVAILABLE",
                blockers=("MARKET_EVIDENCE_READ_FAILED",),
            )
        return EvidenceTargetProjection(
            strategy_version_id=target.strategy_version_id,
            symbol=target.symbol,
            captured_session_count=value.captured_session_count,
            robustness_usable_sessions=value.robustness_usable_sessions,
            high_quality_sessions=value.high_quality_sessions,
            review_ready_sessions=value.review_ready_sessions,
            required_sessions=value.required_sessions,
            remaining_sessions=value.remaining_sessions,
            latest_session=value.latest_session,
            latest_session_quality=value.latest_session_quality,
            status=value.status,
            blockers=value.blockers,
        )

    def _plan_projection(self) -> PortfolioPlanProjection:
        try:
            stored = self._portfolio_plan_repository.load()
        except Exception:  # noqa: BLE001 - unreadable plan facts fail closed
            return PortfolioPlanProjection(
                status="INVALID", blockers=("PORTFOLIO_PLAN_READ_FAILED",)
            )
        if stored is None:
            return PortfolioPlanProjection(
                status="MISSING", blockers=("PORTFOLIO_PLAN_MISSING",)
            )
        blockers: list[str] = []
        try:
            plan = self._portfolio_plan_application.load()
        except Exception:  # noqa: BLE001 - invalid plan facts fail closed
            plan = stored
            blockers.append("CANONICAL_PORTFOLIO_PLAN_VALIDATION_FAILED")
        policy = plan.policy
        limits = tuple(
            (name, str(getattr(policy, name)))
            for name in (
                "total_capital_limit",
                "max_gross_exposure",
                "max_net_exposure",
                "max_single_position_notional",
                "max_symbol_concentration",
                "max_strategy_concentration",
                "max_positions",
                "max_open_orders",
            )
        )
        allocations = tuple(
            PortfolioAllocationProjection(
                strategy_version_id=item.strategy_version_id,
                capital_weight=str(item.capital_weight),
                max_capital=str(item.max_capital),
                max_gross_exposure=str(item.max_gross_exposure),
                enabled=item.enabled,
            )
            for item in sorted(
                policy.allocations, key=lambda item: item.strategy_version_id
            )
        )
        selected = tuple(sorted(plan.selected_version_ids))
        return PortfolioPlanProjection(
            status="INVALID" if blockers else "VALID",
            plan_id=plan.plan_id,
            revision=plan.revision,
            selected_version_ids=selected,
            updated_at=plan.updated_at,
            hard_limits=limits,
            allocations=allocations,
            blockers=tuple(blockers),
        )

    def _reconciliation_projection(
        self,
        result: object | None,
        broker: BrokerCheckProjection,
    ) -> ReconciliationProjection:
        if result is None:
            return ReconciliationProjection(
                status="NOT_CHECKED",
                blockers=("CANONICAL_RECONCILIATION_NOT_COMPOSED",),
            )
        observed_at = getattr(result, "observed_at", None)
        now = self._clock()
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            return ReconciliationProjection(
                status="NOT_CHECKED",
                blockers=("RECONCILIATION_TIMESTAMP_INVALID",),
            )
        observed_at_utc = observed_at.astimezone(UTC)
        now_utc = now.astimezone(UTC)
        if (
            observed_at_utc > now_utc
            or now_utc - observed_at_utc > DEFAULT_MAX_RECONCILIATION_SNAPSHOT_AGE
        ):
            return ReconciliationProjection(
                status="STALE",
                blockers=("RECONCILIATION_STALE",),
                observed_at=observed_at,
            )
        if (
            broker.observed_at is None
            or observed_at_utc < broker.observed_at.astimezone(UTC)
        ):
            return ReconciliationProjection(
                status="STALE",
                blockers=("RECONCILIATION_PREDATES_BROKER_TRUTH",),
                observed_at=observed_at,
            )
        blockers = tuple(
            sorted(
                str(item.value if hasattr(item, "value") else item)
                for item in result.blockers
            )
        )
        return ReconciliationProjection(
            status="CLEAN" if not blockers else "BLOCKED",
            blockers=blockers,
            observed_at=observed_at,
        )

    def _plan_matches(self, plan: PortfolioPlanProjection) -> bool:
        return set(plan.selected_version_ids) == set(self._spec.strategy_version_ids)

    @staticmethod
    def _governance_ready(strategy: StrategyReadinessProjection) -> bool:
        return (
            strategy.status == StrategyStatus.PAPER_SHADOW.value
            and strategy.mode == StrategyMode.PAPER_SHADOW.value
            and strategy.paper_launch_authorized
        )

    @staticmethod
    def _broker_ready(broker: BrokerCheckProjection) -> bool:
        return (
            broker.checked
            and broker.refresh_succeeded
            and broker.configured_environment.lower() == "paper"
            and (broker.broker_environment or "").lower() == "paper"
            and broker.freshness == "FRESH"
            and bool(broker.account_alias)
            and broker.net_liquidation_available
            and broker.cash_available
        )

    @staticmethod
    def _broker_blockers(broker: BrokerCheckProjection) -> tuple[str, ...]:
        blockers = []
        if not broker.checked:
            blockers.append("BROKER_CHECK_NOT_RUN")
        if not broker.refresh_succeeded:
            blockers.append("BROKER_REFRESH_FAILED")
        if (
            broker.configured_environment.lower() != "paper"
            or (broker.broker_environment or "").lower() != "paper"
        ):
            blockers.append("BROKER_ENVIRONMENT_MISMATCH")
        if broker.freshness != "FRESH":
            blockers.append("BROKER_TRUTH_NOT_FRESH")
        if not broker.account_alias:
            blockers.append("BROKER_ACCOUNT_TRUTH_MISSING")
        if not broker.net_liquidation_available:
            blockers.append("BROKER_NET_LIQUIDATION_UNAVAILABLE")
        if not broker.cash_available:
            blockers.append("BROKER_CASH_UNAVAILABLE")
        return tuple(blockers)


def _lifecycle_sort_key(decision: object) -> tuple[datetime, str]:
    authorized_at = decision.authorized_at
    if authorized_at.tzinfo is None or authorized_at.utcoffset() is None:
        raise ValueError("lifecycle timestamp must be timezone-aware")
    return authorized_at.astimezone(UTC), decision.decision_id


def _performance_status(evaluation: object | None) -> PaperPerformanceObservation:
    if evaluation is None:
        return PaperPerformanceObservation.NOT_YET_OBSERVED
    verdict = getattr(evaluation.verdict, "value", str(evaluation.verdict)).upper()
    if verdict == "PASS":
        return PaperPerformanceObservation.PASS
    if verdict == "INSUFFICIENT":
        return PaperPerformanceObservation.INSUFFICIENT
    return PaperPerformanceObservation.FAIL


__all__ = [
    "BrokerCheckProjection",
    "EvidenceTargetProjection",
    "PaperCanaryEvidenceTarget",
    "PaperCanaryInspectionSpec",
    "PaperCanaryInspectionStatus",
    "PaperCanaryReadinessApplication",
    "PaperCanaryReadinessReport",
    "PaperPerformanceObservation",
    "PortfolioPlanProjection",
    "ReconciliationProjection",
    "StrategyReadinessProjection",
]
