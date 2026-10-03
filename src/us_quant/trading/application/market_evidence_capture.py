"""Read-only authority for durable, append-once captured market evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Iterable, Protocol

from us_quant.trading.domain.market import MarketSnapshot


CAPTURE_STALL_SECONDS = 300.0


class MarketEvidenceCaptureError(ValueError):
    """A snapshot does not belong to the fixed capture campaign."""


class DurableMinuteRecord(Protocol):
    minute: str


class EvidenceWriteResult(Protocol):
    rows_written: int
    duplicate_rows_ignored: int
    inserted_records: tuple[DurableMinuteRecord, ...]


class MinuteEvidenceWritePort(Protocol):
    """Write-only port implemented by MinuteQuoteStore at composition time."""

    def record_evidence_snapshot_once(
        self,
        snapshot: MarketSnapshot,
        *,
        symbols: Iterable[str] | None = None,
    ) -> EvidenceWriteResult: ...


@dataclass(frozen=True, slots=True)
class MarketEvidenceCaptureHealth:
    source_id: str
    provider: str
    connected: bool
    ready: bool
    market_stream_realtime: bool
    expected_symbols: tuple[str, ...]
    observed_symbols: tuple[str, ...]
    realtime_symbols: tuple[str, ...]
    last_snapshot_at: datetime | None
    last_durable_minute: str | None
    rows_written: int
    duplicate_rows_ignored: int
    capture_stalled: bool


class MarketEvidenceCaptureApplication:
    """Validate campaign identity, then persist every quote state once.

    This service has no trade, risk, strategy, or governance dependency. It
    intentionally accepts only ``MarketSnapshot`` and fixes captured-stream
    provenance inside ``MinuteQuoteStore``.
    """

    def __init__(
        self,
        *,
        store: MinuteEvidenceWritePort,
        source_id: str,
        provider: str,
        symbols: Iterable[str],
        stall_after_seconds: float = CAPTURE_STALL_SECONDS,
    ) -> None:
        normalized_symbols = tuple(
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        )
        if not source_id.strip() or not provider.strip():
            raise ValueError("capture source and provider are required")
        if not normalized_symbols:
            raise ValueError("capture requires at least one symbol")
        if stall_after_seconds <= 0:
            raise ValueError("capture stall interval must be positive")
        self._store = store
        self.source_id = source_id
        self.provider = provider
        self.expected_symbols = normalized_symbols
        self._stall_after_seconds = stall_after_seconds
        self._started_at = monotonic()
        self._last_durable_at: float | None = None
        self._last_snapshot_at: datetime | None = None
        self._last_durable_minute: str | None = None
        self._rows_written = 0
        self._duplicates_ignored = 0
        self._observed_symbols: set[str] = set()

    def capture(self, snapshot: MarketSnapshot) -> EvidenceWriteResult:
        """Persist the supplied snapshot without dropping invalid/stale rows."""

        self._validate_identity(snapshot)
        self._last_snapshot_at = snapshot.observed_at
        self._observed_symbols.update(quote.symbol for quote in snapshot.quotes)
        result = self._store.record_evidence_snapshot_once(
            snapshot,
            symbols=self.expected_symbols,
        )
        self._rows_written += result.rows_written
        self._duplicates_ignored += result.duplicate_rows_ignored
        if result.inserted_records:
            self._last_durable_at = monotonic()
            self._last_durable_minute = max(
                row.minute for row in result.inserted_records
            )
        return result

    def health(
        self,
        snapshot: MarketSnapshot,
        *,
        now_monotonic: float | None = None,
    ) -> MarketEvidenceCaptureHealth:
        now = monotonic() if now_monotonic is None else now_monotonic
        last_progress = (
            self._last_durable_at
            if self._last_durable_at is not None
            else self._started_at
        )
        realtime_symbols = tuple(
            sorted(
                quote.symbol
                for quote in snapshot.quotes
                if quote.symbol in self.expected_symbols
                if quote.realtime_ready
                and quote.source_id == self.source_id
                and quote.source_label == self.provider
            )
        )
        connected = snapshot.connected
        return MarketEvidenceCaptureHealth(
            source_id=self.source_id,
            provider=self.provider,
            connected=connected,
            ready=snapshot.ready,
            market_stream_realtime=bool(realtime_symbols),
            expected_symbols=self.expected_symbols,
            observed_symbols=tuple(sorted(self._observed_symbols)),
            realtime_symbols=realtime_symbols,
            last_snapshot_at=self._last_snapshot_at or snapshot.observed_at,
            last_durable_minute=self._last_durable_minute,
            rows_written=self._rows_written,
            duplicate_rows_ignored=self._duplicates_ignored,
            capture_stalled=(
                connected
                and now - last_progress >= self._stall_after_seconds
            ),
        )

    def _validate_identity(self, snapshot: MarketSnapshot) -> None:
        if snapshot.source_id != self.source_id:
            raise MarketEvidenceCaptureError(
                "snapshot source_id does not match the fixed capture source"
            )
        if snapshot.source_label != self.provider:
            raise MarketEvidenceCaptureError(
                "snapshot provider does not match the fixed capture provider"
            )
        allowed = set(self.expected_symbols)
        for quote in snapshot.quotes:
            if quote.source_id != self.source_id or quote.source_label != self.provider:
                raise MarketEvidenceCaptureError(
                    "quote provider identity does not match the capture campaign"
                )
            if quote.symbol != quote.symbol.strip().upper() or quote.symbol not in allowed:
                raise MarketEvidenceCaptureError(
                    "quote symbol is outside the fixed capture campaign"
                )


__all__ = [
    "CAPTURE_STALL_SECONDS",
    "MarketEvidenceCaptureApplication",
    "MarketEvidenceCaptureError",
    "MarketEvidenceCaptureHealth",
]
