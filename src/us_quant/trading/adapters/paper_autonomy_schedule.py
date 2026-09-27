"""Production schedule adapter for the Qt-free Paper autonomy supervisor.

Session and holiday classification stays in :mod:`us_quant.extended_hours`.
This adapter only maps that canonical answer and applies the deployment policy's
Eastern wall-clock boundaries.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from us_quant.extended_hours import USEquitySession, us_equity_session
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyPolicy,
    PaperAutonomyScheduleFacts,
    PaperAutonomySessionWindow,
    PaperAutonomySupervisorError,
    PaperAutonomySupervisorViolation,
)

_EASTERN = ZoneInfo("America/New_York")
_SESSION_MAP = {
    USEquitySession.CLOSED: PaperAutonomySessionWindow.CLOSED,
    USEquitySession.OVERNIGHT: PaperAutonomySessionWindow.OVERNIGHT,
    USEquitySession.MAINTENANCE: PaperAutonomySessionWindow.MAINTENANCE,
    USEquitySession.PREMARKET: PaperAutonomySessionWindow.PREMARKET,
    USEquitySession.REGULAR: PaperAutonomySessionWindow.REGULAR,
    USEquitySession.AFTER_HOURS: PaperAutonomySessionWindow.AFTER_HOURS,
}


class PaperAutonomyScheduleAdapter:
    """Classify each supplied instant using the canonical US equity calendar."""

    def __init__(self, policy: PaperAutonomyPolicy) -> None:
        self._policy = policy

    def schedule(self, *, now: datetime) -> PaperAutonomyScheduleFacts:
        if now.tzinfo is None or now.utcoffset() is None:
            raise PaperAutonomySupervisorViolation(
                "the autonomy schedule requires a timezone-aware datetime"
            )
        eastern = now.astimezone(_EASTERN)
        try:
            canonical_session = us_equity_session(eastern)
            session = _SESSION_MAP[canonical_session]
        except Exception:  # noqa: BLE001 - calendar is an external fact
            trading_day = self._trading_day_from_midday(eastern.date())
            return PaperAutonomyScheduleFacts(
                trading_day=trading_day,
                session=None,
                preparation_allowed=False,
                start_allowed=False,
                orderly_stop_due=False,
                exceptional_schedule_uncertain=True,
            )

        trading_day = self._trading_day(eastern, canonical_session)
        eastern_time = eastern.timetz().replace(tzinfo=None)
        orderly_stop_due = (
            eastern.date() == trading_day
            and canonical_session
            in {USEquitySession.REGULAR, USEquitySession.AFTER_HOURS}
            and eastern_time >= self._policy.orderly_stop_at_et
        )
        preparation_allowed = (
            not orderly_stop_due
            and canonical_session
            in {USEquitySession.PREMARKET, USEquitySession.REGULAR}
            and eastern_time >= self._policy.prepare_not_before_et
        )
        start_allowed = (
            not orderly_stop_due
            and canonical_session is USEquitySession.REGULAR
            and self._policy.start_not_before_et
            <= eastern_time
            <= self._policy.latest_start_et
        )
        return PaperAutonomyScheduleFacts(
            trading_day=trading_day,
            session=session,
            preparation_allowed=preparation_allowed,
            start_allowed=start_allowed,
            orderly_stop_due=orderly_stop_due,
            exceptional_schedule_uncertain=False,
        )

    def _trading_day(
        self, eastern: datetime, session: USEquitySession
    ) -> date:
        current_day = eastern.date()
        if session in {
            USEquitySession.PREMARKET,
            USEquitySession.REGULAR,
            USEquitySession.AFTER_HOURS,
            USEquitySession.MAINTENANCE,
        }:
            return current_day
        if (
            session is USEquitySession.OVERNIGHT
            and eastern.timetz().replace(tzinfo=None) < time(3, 50)
        ):
            return current_day
        return self._next_canonical_trading_day(current_day + timedelta(days=1))

    def _trading_day_from_midday(self, day: date) -> date:
        """Recover a date only when the canonical provider confirms it."""

        try:
            session = us_equity_session(
                datetime.combine(day, time(12), tzinfo=_EASTERN)
            )
            if session is USEquitySession.REGULAR:
                return day
        except Exception as error:  # noqa: BLE001 - preserve fail-closed result
            raise PaperAutonomySupervisorError(
                "the canonical schedule could not establish a trading day"
            ) from error
        return self._next_canonical_trading_day(day + timedelta(days=1))

    @staticmethod
    def _next_canonical_trading_day(first_day: date) -> date:
        """Find the next day classified REGULAR by the sole canonical provider."""

        for offset in range(14):
            day = first_day + timedelta(days=offset)
            try:
                session = us_equity_session(
                    datetime.combine(day, time(12), tzinfo=_EASTERN)
                )
            except Exception as error:  # noqa: BLE001 - no guessed calendar
                raise PaperAutonomySupervisorError(
                    "the canonical schedule could not establish a trading day"
                ) from error
            try:
                mapped = _SESSION_MAP[session]
            except (KeyError, TypeError) as error:
                raise PaperAutonomySupervisorError(
                    "the canonical schedule returned an unmappable session"
                ) from error
            if mapped is PaperAutonomySessionWindow.REGULAR:
                return day
        raise PaperAutonomySupervisorError(
            "the canonical schedule did not identify a trading day within two weeks"
        )


__all__ = ["PaperAutonomyScheduleAdapter"]
