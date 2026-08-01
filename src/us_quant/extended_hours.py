from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


class USEquitySession(StrEnum):
    CLOSED = "closed"
    OVERNIGHT = "overnight"
    MAINTENANCE = "maintenance"
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"


@dataclass(frozen=True, slots=True)
class PaperOrderRouting:
    """IBKR routing attributes for one US equity session.

    This is deliberately small: it only models whole-share US stock / ETF
    limit orders. The broker remains the final authority for a symbol's
    eligibility and holiday-specific trading schedule.
    """

    session: USEquitySession
    label: str
    exchange: str
    tif: str
    outside_rth: bool
    allowed: bool
    reason: str


def us_equity_session(
    now: datetime | None = None,
) -> USEquitySession:
    """Classify the normal US equity timetable in America/New_York.

    US-listed equities are not truly 24/7. IBKR's overnight venue is normally
    available Sunday evening through Friday morning, with a 03:50--04:00 ET
    maintenance gap. Full US-market holidays are blocked locally; IBKR's
    contract schedule remains the authoritative source for exceptional days.
    """

    current = _new_york_now(now)
    local_time = current.timetz().replace(tzinfo=None)
    weekday = current.weekday()
    if weekday == 5 or _is_us_equity_holiday(current.date()):
        return USEquitySession.CLOSED
    if weekday == 6:
        next_day = current.date() + timedelta(days=1)
        return (
            USEquitySession.OVERNIGHT
            if (
                local_time >= time(20, 0)
                and not _is_us_equity_holiday(next_day)
            )
            else USEquitySession.CLOSED
        )
    if weekday == 4 and local_time >= time(20, 0):
        return USEquitySession.CLOSED
    if local_time >= time(20, 0):
        next_day = current.date() + timedelta(days=1)
        if _is_us_equity_holiday(next_day):
            return USEquitySession.CLOSED
        return USEquitySession.OVERNIGHT
    if local_time < time(3, 50):
        return USEquitySession.OVERNIGHT
    if local_time < time(4, 0):
        return USEquitySession.MAINTENANCE
    if local_time < time(9, 30):
        return USEquitySession.PREMARKET
    if local_time < time(16, 0):
        return USEquitySession.REGULAR
    return USEquitySession.AFTER_HOURS


def paper_order_routing(
    *,
    extended_hours_enabled: bool,
    now: datetime | None = None,
) -> PaperOrderRouting:
    """Return the only safe IBKR Paper route for the current session."""

    session = us_equity_session(now)
    if session is USEquitySession.REGULAR:
        return PaperOrderRouting(
            session=session,
            label="常规时段",
            exchange="SMART",
            tif="DAY",
            outside_rth=False,
            allowed=True,
            reason="常规美股时段：SMART DAY 限价单",
        )
    if not extended_hours_enabled:
        return PaperOrderRouting(
            session=session,
            label="扩展时段未启用",
            exchange="SMART",
            tif="DAY",
            outside_rth=False,
            allowed=False,
            reason="5×24 Paper 扩展时段开关未启用",
        )
    if session is USEquitySession.PREMARKET:
        return PaperOrderRouting(
            session=session,
            label="盘前",
            exchange="SMART",
            tif="DAY",
            outside_rth=True,
            allowed=True,
            reason="盘前：SMART DAY 限价单，允许常规时段外成交",
        )
    if session is USEquitySession.AFTER_HOURS:
        return PaperOrderRouting(
            session=session,
            label="盘后",
            exchange="SMART",
            tif="DAY",
            outside_rth=True,
            allowed=True,
            reason="盘后：SMART DAY 限价单，允许常规时段外成交",
        )
    if session is USEquitySession.OVERNIGHT:
        return PaperOrderRouting(
            session=session,
            label="隔夜",
            exchange="OVERNIGHT",
            tif="DAY",
            outside_rth=False,
            allowed=True,
            reason="隔夜：IBKR OVERNIGHT 限价单，仅本隔夜会话有效",
        )
    if session is USEquitySession.MAINTENANCE:
        reason = "美东 03:50–04:00 的隔夜维护窗口，禁止提交订单"
    else:
        reason = "周末或美股全日休市，禁止提交订单"
    return PaperOrderRouting(
        session=session,
        label="不可交易",
        exchange="SMART",
        tif="DAY",
        outside_rth=False,
        allowed=False,
        reason=reason,
    )


def ibkr_market_data_exchange(
    now: datetime | None = None,
) -> str:
    """Choose the matching IBKR data venue for 5×24 monitoring.

    The OVERNIGHT venue must be requested directly for overnight quotes;
    SMART data is used in regular, pre-market and after-hours sessions.
    """

    return (
        "OVERNIGHT"
        if us_equity_session(now) is USEquitySession.OVERNIGHT
        else "SMART"
    )


def _new_york_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(NEW_YORK)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(NEW_YORK)


def _is_us_equity_holiday(day: date) -> bool:
    """Full NYSE-style holidays used as a conservative local guard."""

    year = day.year
    holidays = {
        _observed_fixed_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed_holiday(date(year, 6, 19)),
        _observed_fixed_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed_holiday(date(year, 12, 25)),
    }
    return day in holidays


def _observed_fixed_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(
    year: int, month: int, weekday: int, occurrence: int
) -> date:
    current = date(year, month, 1)
    current += timedelta(days=(weekday - current.weekday()) % 7)
    return current + timedelta(days=7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian computus, sufficient for the NYSE Good Friday guard."""

    century = year // 100
    remainder = year % 100
    leap_century = century // 4
    century_remainder = century % 4
    correction = (century + 8) // 25
    adjustment = (century - correction + 1) // 3
    epact = (
        19 * (year % 19)
        + century
        - leap_century
        - adjustment
        + 15
    ) % 30
    weekday_adjustment = remainder // 4
    remainder_weekday = remainder % 4
    weekday = (
        32 + 2 * century_remainder + 2 * weekday_adjustment - epact
        - remainder_weekday
    ) % 7
    index = (year % 19 + 11 * epact + 22 * weekday) // 451
    month = (epact + weekday - 7 * index + 114) // 31
    day = (epact + weekday - 7 * index + 114) % 31 + 1
    return date(year, month, day)
