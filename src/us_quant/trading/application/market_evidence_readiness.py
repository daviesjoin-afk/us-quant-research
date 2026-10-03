"""Read-only progress for one symbol and one fixed market-data provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from us_quant.trading.domain.market_evidence_quality import (
    MINIMUM_SESSION_ROWS,
    MinuteEvidenceRecord,
    MinuteEvidenceSessionQuality,
    evaluate_minute_evidence_session,
    group_regular_sessions,
)


class MinuteEvidenceReadPort(Protocol):
    """Read-only storage interface supplied by the composition root."""

    def load(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        usable_only: bool = True,
    ) -> tuple[MinuteEvidenceRecord, ...]: ...


REQUIRED_CAPTURE_SESSIONS = 25


@dataclass(frozen=True, slots=True)
class MarketEvidenceReadiness:
    symbol: str
    provider: str
    status: str
    captured_session_count: int
    robustness_usable_sessions: int
    high_quality_sessions: int
    review_ready_sessions: int
    required_sessions: int
    remaining_sessions: int
    first_session: str | None
    latest_session: str | None
    latest_session_quality: MinuteEvidenceSessionQuality | None
    ready_for_targeted_review: bool
    blockers: tuple[str, ...]


def assess_market_evidence_readiness(
    records: Iterable[MinuteEvidenceRecord],
    *,
    symbol: str,
    provider: str,
    parameters: Mapping[str, object],
    required_sessions: int = REQUIRED_CAPTURE_SESSIONS,
) -> MarketEvidenceReadiness:
    """Assess one captured-stream set; never combines providers or origins."""

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol or not provider.strip():
        raise ValueError("readiness requires an exact symbol and provider")
    if required_sessions < 1:
        raise ValueError("required_sessions must be positive")
    required_parameters = {
        "warmup_minutes",
        "momentum_lookback_minutes",
    }
    missing_parameters = required_parameters - parameters.keys()
    if missing_parameters:
        raise ValueError(
            "readiness requires strategy parameters: "
            + ", ".join(sorted(missing_parameters))
        )

    source_records = tuple(records)
    blockers: list[str] = []
    providers = {row.provider for row in source_records}
    symbols = {row.symbol for row in source_records}
    if len(providers) > 1:
        return _invalid_readiness(
            normalized_symbol,
            provider,
            required_sessions,
            "MULTIPLE_PROVIDERS",
        )
    if providers and providers != {provider}:
        return _invalid_readiness(
            normalized_symbol,
            provider,
            required_sessions,
            "PROVIDER_MISMATCH",
        )
    if symbols and symbols != {normalized_symbol}:
        return _invalid_readiness(
            normalized_symbol,
            provider,
            required_sessions,
            "SYMBOL_MISMATCH",
        )

    captured = tuple(
        row for row in source_records
        if row.evidence_origin == "captured_stream"
    )
    if not captured:
        blockers.append("NO_CAPTURED_DATA")
        return MarketEvidenceReadiness(
            symbol=normalized_symbol,
            provider=provider,
            status="COLLECTING",
            captured_session_count=0,
            robustness_usable_sessions=0,
            high_quality_sessions=0,
            review_ready_sessions=0,
            required_sessions=required_sessions,
            remaining_sessions=required_sessions,
            first_session=None,
            latest_session=None,
            latest_session_quality=None,
            ready_for_targeted_review=False,
            blockers=tuple(blockers + [
                "INSUFFICIENT_COMPLETE_SESSIONS",
                "INSUFFICIENT_HIGH_QUALITY_SESSIONS",
            ]),
        )

    signal_required = max(
        int(parameters["warmup_minutes"]),
        int(parameters["momentum_lookback_minutes"]) + 1,
    )
    minimum_rows = max(MINIMUM_SESSION_ROWS, signal_required)
    sessions = group_regular_sessions(captured)
    qualities = tuple(
        evaluate_minute_evidence_session(
            session_date,
            rows,
            minimum_rows=minimum_rows,
            minimum_contiguous_run=signal_required,
        )
        for session_date, rows in sessions
    )
    robustness_usable = sum(row.robustness_usable for row in qualities)
    high_quality = sum(row.high_quality for row in qualities)
    review_ready = sum(
        row.robustness_usable and row.high_quality
        for row in qualities
    )
    if not sessions:
        blockers.append("NO_REGULAR_SESSION")
    if robustness_usable < required_sessions:
        blockers.append("INSUFFICIENT_COMPLETE_SESSIONS")
    if high_quality < required_sessions:
        blockers.append("INSUFFICIENT_HIGH_QUALITY_SESSIONS")
    if review_ready < required_sessions:
        blockers.append("INSUFFICIENT_QUALIFIED_SESSIONS")

    latest = qualities[-1] if qualities else None
    if latest is not None:
        blockers.extend(_latest_quality_blockers(latest))
    ready = review_ready >= required_sessions
    return MarketEvidenceReadiness(
        symbol=normalized_symbol,
        provider=provider,
        status="READY" if ready else "COLLECTING",
        captured_session_count=len(sessions),
        robustness_usable_sessions=robustness_usable,
        high_quality_sessions=high_quality,
        review_ready_sessions=review_ready,
        required_sessions=required_sessions,
        remaining_sessions=max(0, required_sessions - review_ready),
        first_session=sessions[0][0] if sessions else None,
        latest_session=sessions[-1][0] if sessions else None,
        latest_session_quality=latest,
        ready_for_targeted_review=ready,
        blockers=tuple(dict.fromkeys(blockers)),
    )


class MarketEvidenceReadinessApplication:
    """Load one provider's durable captured rows and assess them."""

    def __init__(self, store: MinuteEvidenceReadPort) -> None:
        self._store = store

    def inspect(
        self,
        *,
        symbol: str,
        provider: str,
        parameters: Mapping[str, object],
        required_sessions: int = REQUIRED_CAPTURE_SESSIONS,
    ) -> MarketEvidenceReadiness:
        rows = self._store.load(
            symbol,
            usable_only=False,
        )
        return assess_market_evidence_readiness(
            rows,
            symbol=symbol,
            provider=provider,
            parameters=parameters,
            required_sessions=required_sessions,
        )


def _latest_quality_blockers(
    quality: MinuteEvidenceSessionQuality,
) -> tuple[str, ...]:
    blockers: list[str] = []
    reasons = set(quality.failure_reasons)
    if "完整率低于 98%" in reasons or not quality.evaluation_window_covered:
        blockers.append("INCOMPLETE_WINDOW")
    if "连续缺口超过 2 分钟" in reasons:
        blockers.append("EXCESSIVE_GAP")
    if "存在非正或倒挂报价" in reasons:
        blockers.append("INVALID_QUOTES")
    if (
        "行情年龄不可估计" in reasons
        or "行情年龄 P95 超过 5 秒" in reasons
    ):
        blockers.append("EXCESSIVE_SOURCE_AGE")
    if quality.stale_rows:
        blockers.append("STALE_DATA")
    if "连续预热分钟不足" in quality.robustness_failure_reasons:
        blockers.append("INSUFFICIENT_CONTIGUOUS_MINUTES")
    return tuple(blockers)


def _invalid_readiness(
    symbol: str,
    provider: str,
    required_sessions: int,
    blocker: str,
) -> MarketEvidenceReadiness:
    return MarketEvidenceReadiness(
        symbol=symbol,
        provider=provider,
        status="DATA_INVALID",
        captured_session_count=0,
        robustness_usable_sessions=0,
        high_quality_sessions=0,
        review_ready_sessions=0,
        required_sessions=required_sessions,
        remaining_sessions=required_sessions,
        first_session=None,
        latest_session=None,
        latest_session_quality=None,
        ready_for_targeted_review=False,
        blockers=(blocker,),
    )


__all__ = [
    "MarketEvidenceReadiness",
    "MarketEvidenceReadinessApplication",
    "REQUIRED_CAPTURE_SESSIONS",
    "assess_market_evidence_readiness",
]
