"""Read-only readiness diagnostics for real Paper evidence acquisition.

This module answers exactly one operator question -- *may we collect real
captured-stream evidence right now, and if not, what precisely is missing?* --
and nothing else.  It is a projection over facts that already exist:

* the IBKR socket probe and the loaded broker configuration;
* the recorder's most recent health snapshot;
* the durable minute-quote evidence already judged by ``MarketEvidenceQuality``.

It never starts a recorder, opens a writable database, switches a provider,
edits configuration or submits an order, and it grants no authority.  The
authorities stay ``MarketEvidenceQuality``, ``StrategyLifecycle``,
``PaperAuthorization`` and ``PortfolioRuntime``; ``READY`` here means
"collection may proceed", never "trading is approved".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadiness,
)


class PaperEvidenceReadinessStatus(StrEnum):
    """Diagnostic verdict.  It is not a state machine and grants no authority."""

    READY = "READY"
    DATA_COLLECTION = "DATA_COLLECTION"
    BLOCKED = "BLOCKED"


#: Ports the IBKR Paper API listens on: Gateway Paper and TWS Paper.
PAPER_API_PORTS: frozenset[int] = frozenset({4002, 7497})

#: Ports the IBKR Live API listens on.  Named separately so the operator is
#: told *why* the port was refused instead of a generic range complaint.
LIVE_API_PORTS: frozenset[int] = frozenset({4001, 7496})

#: Operator-facing labels.  ``DATA_COLLECTION`` blocks the operator even though
#: it is a distinct machine status: collection may not be called complete.
DISPLAY_STATUS: Mapping[PaperEvidenceReadinessStatus, str] = {
    PaperEvidenceReadinessStatus.READY: "READY",
    PaperEvidenceReadinessStatus.DATA_COLLECTION: "BLOCKED_DATA_COLLECTION",
    PaperEvidenceReadinessStatus.BLOCKED: "BLOCKED",
}

#: A health snapshot older than this is treated as a dead stream.  The recorder
#: emits one line per ``--health-interval`` (60 s default); two missed intervals
#: means the process is gone or wedged, and a stale "realtime" flag is exactly
#: the lie this diagnostic exists to catch.
STREAM_STALE_AFTER_SECONDS = 120.0

EVIDENCE_READY_STATUS = "READY"
EVIDENCE_INVALID_STATUS = "DATA_INVALID"
STORE_UNAVAILABLE_BLOCKER = "EVIDENCE_STORE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class IbkrConnectionProjection:
    """The read-only socket probe plus the loaded broker configuration."""

    checked: bool
    socket_reachable: bool
    host: str
    port: int
    api_read_only: bool
    paper_order_submission_enabled: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("IBKR host must not be blank")
        if not 1 <= self.port <= 65535:
            raise ValueError("IBKR port must be in [1, 65535]")
        if self.paper_order_submission_enabled and self.api_read_only:
            raise ValueError(
                "order submission cannot be enabled while the API is read-only"
            )
        object.__setattr__(self, "host", self.host.strip())


@dataclass(frozen=True, slots=True)
class MarketStreamProjection:
    """The recorder's most recent health snapshot, or the absence of one."""

    observed: bool
    source_id: str
    provider: str
    connected: bool
    realtime: bool
    stalled: bool
    expected_symbols: tuple[str, ...] = ()
    realtime_symbols: tuple[str, ...] = ()
    last_snapshot_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("stream source id must not be blank")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("stream provider must not be blank")
        if not self.observed and (self.connected or self.realtime or self.stalled):
            raise ValueError("an unobserved stream cannot report a state")
        object.__setattr__(
            self, "expected_symbols", _normalized_symbols(self.expected_symbols)
        )
        object.__setattr__(
            self, "realtime_symbols", _normalized_symbols(self.realtime_symbols)
        )
        if self.last_snapshot_at is not None:
            if self.last_snapshot_at.tzinfo is None:
                raise ValueError("health snapshot time must be timezone-aware")
            object.__setattr__(
                self,
                "last_snapshot_at",
                self.last_snapshot_at.astimezone(UTC),
            )


@dataclass(frozen=True, slots=True)
class EvidenceTargetProjection:
    """One target's durable coverage, as judged by MarketEvidenceQuality."""

    symbol: str
    provider: str
    status: str
    captured_session_count: int
    robustness_usable_sessions: int
    high_quality_sessions: int
    review_ready_sessions: int
    required_sessions: int
    remaining_sessions: int
    latest_session: str | None
    complete: bool
    quality_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperEvidenceReadinessSpec:
    """What the operator intends to collect, and from which single provider."""

    provider: str
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must not be blank")
        if not isinstance(self.symbols, Sequence) or isinstance(self.symbols, str):
            raise TypeError("symbols must be a sequence")
        normalized = _normalized_symbols(self.symbols)
        if not normalized:
            raise ValueError("at least one target symbol is required")
        if len(normalized) != len(tuple(self.symbols)):
            raise ValueError("target symbols must be unique and nonblank")
        object.__setattr__(self, "provider", self.provider.strip().upper())
        object.__setattr__(self, "symbols", normalized)


@dataclass(frozen=True, slots=True)
class PaperEvidenceReadinessReport:
    provider: str
    status: PaperEvidenceReadinessStatus
    ibkr: IbkrConnectionProjection
    stream: MarketStreamProjection
    targets: tuple[EvidenceTargetProjection, ...]
    blockers: tuple[str, ...]
    observed_at: datetime

    @property
    def ready(self) -> bool:
        return self.status is PaperEvidenceReadinessStatus.READY

    @property
    def display_status(self) -> str:
        return DISPLAY_STATUS[self.status]

    @property
    def total_captured_sessions(self) -> int:
        return sum(item.captured_session_count for item in self.targets)


class PaperEvidenceReadinessApplication:
    """Combine existing facts into a diagnosis; never grant authority."""

    def __init__(
        self,
        *,
        spec: PaperEvidenceReadinessSpec,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._spec = spec
        self._clock = clock or (lambda: datetime.now(UTC))

    def inspect(
        self,
        *,
        ibkr: IbkrConnectionProjection,
        stream: MarketStreamProjection,
        evidence: tuple[MarketEvidenceReadiness, ...] = (),
        evidence_unavailable_reason: str | None = None,
    ) -> PaperEvidenceReadinessReport:
        now = self._clock()
        targets = tuple(
            sorted(
                (self._projection(row) for row in evidence),
                key=lambda item: self._target_rank(item.symbol),
            )
        )
        blockers: list[str] = [
            *_ibkr_blockers(ibkr),
            *_stream_blockers(
                stream,
                provider=self._spec.provider,
                targets=self._spec.symbols,
                now=now,
            ),
        ]

        # The store is independent of connectivity: a missing or unreadable
        # database is reported even when the socket and stream also failed, so
        # an operator does not fix one blocker only to discover the next.
        if evidence_unavailable_reason is not None:
            blockers.append(evidence_unavailable_reason)

        if blockers:
            status = PaperEvidenceReadinessStatus.BLOCKED
        elif tuple(item.symbol for item in targets) != self._spec.symbols:
            status = PaperEvidenceReadinessStatus.BLOCKED
            blockers.append("EVIDENCE_TARGET_SET_MISMATCH")
        elif any(item.status == EVIDENCE_INVALID_STATUS for item in targets):
            status = PaperEvidenceReadinessStatus.BLOCKED
            invalid = tuple(
                item for item in targets if item.status == EVIDENCE_INVALID_STATUS
            )
            if any(
                "PROVIDER_MISMATCH" in item.quality_blockers for item in invalid
            ):
                blockers.append("PROVIDER_MISMATCH")
            blockers.append("EVIDENCE_DATA_INVALID")
            for item in invalid:
                blockers.extend(
                    f"{item.symbol}:{reason}"
                    for reason in (item.quality_blockers or ("UNKNOWN",))
                )
        elif all(item.captured_session_count == 0 for item in targets):
            status = PaperEvidenceReadinessStatus.DATA_COLLECTION
            blockers.append("EVIDENCE_NOT_COLLECTED")
        elif any(not item.complete for item in targets):
            status = PaperEvidenceReadinessStatus.BLOCKED
            blockers.append("EVIDENCE_INCOMPLETE")
            for item in targets:
                if not item.complete:
                    blockers.extend(
                        f"{item.symbol}:{reason}"
                        for reason in (item.quality_blockers or ("NOT_READY",))
                    )
        else:
            status = PaperEvidenceReadinessStatus.READY

        return PaperEvidenceReadinessReport(
            provider=self._spec.provider,
            status=status,
            ibkr=ibkr,
            stream=stream,
            targets=targets,
            blockers=tuple(dict.fromkeys(blockers)),
            observed_at=now,
        )

    def _target_rank(self, symbol: str) -> int:
        """Order targets the way the operator declared them, deterministically."""

        try:
            return self._spec.symbols.index(symbol)
        except ValueError:
            return len(self._spec.symbols)

    def _projection(self, row: MarketEvidenceReadiness) -> EvidenceTargetProjection:
        return EvidenceTargetProjection(
            symbol=row.symbol,
            provider=row.provider,
            status=row.status,
            captured_session_count=row.captured_session_count,
            robustness_usable_sessions=row.robustness_usable_sessions,
            high_quality_sessions=row.high_quality_sessions,
            review_ready_sessions=row.review_ready_sessions,
            required_sessions=row.required_sessions,
            remaining_sessions=row.remaining_sessions,
            latest_session=row.latest_session,
            complete=row.status == EVIDENCE_READY_STATUS,
            quality_blockers=tuple(row.blockers),
        )


def _ibkr_blockers(ibkr: IbkrConnectionProjection) -> tuple[str, ...]:
    if not ibkr.checked:
        return ("IBKR_NOT_CHECKED",)
    blockers: list[str] = []
    if not ibkr.socket_reachable:
        blockers.append("IBKR_SOCKET_UNREACHABLE")
    if ibkr.port in LIVE_API_PORTS:
        blockers.append("IBKR_LIVE_PORT")
    elif ibkr.port not in PAPER_API_PORTS:
        blockers.append("IBKR_PORT_NOT_PAPER")
    if not ibkr.api_read_only:
        blockers.append("IBKR_API_NOT_READ_ONLY")
    if ibkr.paper_order_submission_enabled:
        blockers.append("IBKR_ORDER_SUBMISSION_ENABLED")
    return tuple(blockers)


def _stream_blockers(
    stream: MarketStreamProjection,
    *,
    provider: str,
    targets: tuple[str, ...],
    now: datetime,
) -> tuple[str, ...]:
    if not stream.observed:
        return ("MARKET_STREAM_NOT_OBSERVED",)
    blockers: list[str] = []
    if not stream.connected:
        blockers.append("MARKET_STREAM_DISCONNECTED")
    if stream.stalled or _snapshot_is_stale(stream.last_snapshot_at, now):
        blockers.append("MARKET_STREAM_STALLED")
    if not stream.realtime:
        blockers.append("MARKET_STREAM_NOT_REALTIME")
    if stream.provider.strip().upper() != provider.strip().upper():
        blockers.append("PROVIDER_MISMATCH")
    # A requested symbol the recorder is not receiving in realtime cannot
    # produce captured evidence, so "the stream is realtime" is not enough.
    # Membership is judged against the realtime set alone: a recorder that was
    # configured for a narrower symbol list reports a healthy stream for its own
    # symbols, and that must not certify targets it never subscribed to.
    missing = tuple(
        symbol for symbol in targets if symbol not in stream.realtime_symbols
    )
    if missing:
        blockers.append("MARKET_STREAM_SYMBOL_NOT_REALTIME")
    return tuple(blockers)


def _snapshot_is_stale(
    last_snapshot_at: datetime | None, now: datetime
) -> bool:
    if last_snapshot_at is None:
        return True
    age = (now - last_snapshot_at).total_seconds()
    # A negative age is a future timestamp: a clock that disagrees with the
    # recorder cannot certify freshness, so it fails closed rather than reading
    # as arbitrarily fresh.
    if age < 0:
        return True
    return age > STREAM_STALE_AFTER_SECONDS


def _normalized_symbols(symbols: object) -> tuple[str, ...]:
    if isinstance(symbols, str) or not isinstance(symbols, Sequence):
        raise TypeError("symbols must be a sequence")
    seen: dict[str, None] = {}
    for item in symbols:
        if isinstance(item, str) and item.strip():
            seen.setdefault(item.strip().upper(), None)
    return tuple(seen)


__all__ = [
    "DISPLAY_STATUS",
    "EVIDENCE_INVALID_STATUS",
    "EVIDENCE_READY_STATUS",
    "LIVE_API_PORTS",
    "PAPER_API_PORTS",
    "STORE_UNAVAILABLE_BLOCKER",
    "STREAM_STALE_AFTER_SECONDS",
    "EvidenceTargetProjection",
    "IbkrConnectionProjection",
    "MarketStreamProjection",
    "PaperEvidenceReadinessApplication",
    "PaperEvidenceReadinessReport",
    "PaperEvidenceReadinessSpec",
    "PaperEvidenceReadinessStatus",
]
