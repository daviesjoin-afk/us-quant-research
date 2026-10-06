"""Read-only composition for the Paper evidence readiness diagnostic.

Everything here observes; nothing here changes state.  The SQLite evidence
store is opened in read-only mode (and a missing file is reported, never
created), the broker check is a socket probe that performs no IBKR handshake
and no account read, and the health snapshot is parsed from a log the
recorder already writes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from us_quant.ibkr import IBKRConnectionConfig, probe_ibkr_socket
from us_quant.minute_data import MinuteQuoteStore
from us_quant.trading.application.market_data import SOURCE_LABELS
from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadiness,
    MarketEvidenceReadinessApplication,
)
from us_quant.trading.application.paper_evidence_readiness import (
    IbkrConnectionProjection,
    MarketStreamProjection,
    PaperEvidenceReadinessApplication,
    PaperEvidenceReadinessSpec,
)


class EvidenceStoreUnavailable(RuntimeError):
    """The durable minute-quote database cannot be read."""


@dataclass(frozen=True, slots=True)
class EvidenceStoreReadPort:
    """The narrow read surface the composition hands to the application."""

    store: MinuteQuoteStore

    def load(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        usable_only: bool = True,
    ) -> tuple:
        return self.store.load(
            symbol, provider=provider, usable_only=usable_only
        )


def build_ibkr_projection(
    config: IBKRConnectionConfig, *, checked: bool = True
) -> IbkrConnectionProjection:
    """Probe the loopback socket only; never handshake or read the account."""

    if not checked:
        return IbkrConnectionProjection(
            checked=False,
            socket_reachable=False,
            host=config.host,
            port=config.port,
            api_read_only=config.api_read_only,
            paper_order_submission_enabled=config.paper_order_submission_enabled,
            detail="socket probe not requested",
        )
    probe = probe_ibkr_socket(config)
    return IbkrConnectionProjection(
        checked=True,
        socket_reachable=probe.reachable,
        host=probe.host,
        port=probe.port,
        api_read_only=config.api_read_only,
        paper_order_submission_enabled=config.paper_order_submission_enabled,
        detail=probe.detail,
    )


def load_stream_projection(
    health_log: Path | None,
    *,
    default_source_id: str,
    default_provider: str,
) -> MarketStreamProjection:
    """Read the recorder's newest health line; never write to the log.

    The recorder prints one health JSON object per interval to **stdout**; it
    does not own a log file.  An operator therefore either redirects that
    stream to a file and passes it as ``health_log``, or passes ``None`` and
    gets ``observed=False`` -- which is reported as a blocker, never as a
    healthy stream.
    """

    payload: Mapping[str, Any] | None = None
    if health_log is not None:
        path = Path(health_log)
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        candidate = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, Mapping):
                        payload = candidate
        except FileNotFoundError:
            payload = None
        except OSError as error:
            raise EvidenceStoreUnavailable(
                f"health log is unreadable: {error}"
            ) from error

    if payload is None:
        return unobserved_stream(
            default_source_id=default_source_id, default_provider=default_provider
        )
    return projection_from_payload(
        payload,
        default_source_id=default_source_id,
        default_provider=default_provider,
    )


def unobserved_stream(
    *, default_source_id: str, default_provider: str
) -> MarketStreamProjection:
    """The honest projection when no health line has been seen."""

    return MarketStreamProjection(
        observed=False,
        source_id=default_source_id,
        provider=default_provider,
        connected=False,
        realtime=False,
        stalled=False,
    )


def projection_from_payload(
    payload: Mapping[str, Any],
    *,
    default_source_id: str,
    default_provider: str,
) -> MarketStreamProjection:
    """Map one recorder health payload onto the stream projection.

    Both transports (a redirected file and a piped stdin) decode the same
    recorder payload, so the mapping lives here once.
    """

    source_id = str(payload.get("source") or default_source_id)
    provider = str(
        payload.get("provider") or SOURCE_LABELS.get(source_id, default_provider)
    )
    return MarketStreamProjection(
        observed=True,
        source_id=source_id,
        provider=provider,
        connected=bool(payload.get("broker_api_connected")),
        realtime=bool(payload.get("market_stream_realtime")),
        stalled=str(payload.get("status") or "") == "CAPTURE_STALLED",
        expected_symbols=tuple(payload.get("symbols_expected") or ()),
        realtime_symbols=tuple(payload.get("symbols_realtime") or ()),
        last_snapshot_at=_parse_timestamp(payload.get("last_snapshot_at")),
    )


def build_evidence_readiness(
    *,
    spec: PaperEvidenceReadinessSpec,
    quote_path: Path,
    parameters: Mapping[str, object],
) -> tuple[MarketEvidenceReadiness, ...]:
    """Assess every target against the durable store, or fail closed."""

    store_path = Path(quote_path)
    if not store_path.exists():
        raise EvidenceStoreUnavailable(
            f"minute quote database not found: {store_path}"
        )
    store = MinuteQuoteStore(store_path, read_only=True)
    application = MarketEvidenceReadinessApplication(EvidenceStoreReadPort(store))
    rows: list[MarketEvidenceReadiness] = []
    try:
        for symbol in spec.symbols:
            rows.append(
                application.inspect(
                    symbol=symbol,
                    provider=spec.provider,
                    parameters=parameters,
                )
            )
    except Exception as error:  # noqa: BLE001 - diagnostics fail closed
        raise EvidenceStoreUnavailable(
            f"minute quote database is unreadable: {error}"
        ) from error
    return tuple(rows)


def build_paper_evidence_readiness_application(
    spec: PaperEvidenceReadinessSpec,
) -> PaperEvidenceReadinessApplication:
    return PaperEvidenceReadinessApplication(spec=spec)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


__all__ = [
    "EvidenceStoreReadPort",
    "EvidenceStoreUnavailable",
    "build_evidence_readiness",
    "build_ibkr_projection",
    "build_paper_evidence_readiness_application",
    "load_stream_projection",
    "projection_from_payload",
    "unobserved_stream",
]
