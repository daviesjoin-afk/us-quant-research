from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from us_quant.extended_hours import USEquitySession
from us_quant.trading.adapters import paper_autonomy_schedule
from us_quant.trading.adapters.paper_autonomy_schedule import (
    PaperAutonomyScheduleAdapter,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyPolicy,
    PaperAutonomySessionWindow,
    PaperAutonomySupervisorError,
    PaperAutonomySupervisorViolation,
)

ET = ZoneInfo("America/New_York")


@pytest.fixture
def policy() -> PaperAutonomyPolicy:
    return PaperAutonomyPolicy(
        prepare_not_before_et=time(9, 0),
        start_not_before_et=time(9, 35),
        latest_start_et=time(15, 30),
        orderly_stop_at_et=time(15, 50),
        candidate_limit=8,
        requested_capital_limit=Decimal("25000"),
        tick_interval_seconds=30,
    )


def _schedule(policy: PaperAutonomyPolicy, moment: datetime):
    return PaperAutonomyScheduleAdapter(policy).schedule(now=moment)


def test_aware_utc_is_converted_to_eastern_wall_time(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 13, 15, tzinfo=timezone.utc))

    assert facts.session is PaperAutonomySessionWindow.PREMARKET
    assert facts.trading_day == date(2026, 9, 28)
    assert facts.preparation_allowed
    assert not facts.start_allowed


def test_naive_datetime_is_refused(policy):
    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(policy, datetime(2026, 9, 28, 9, 30))


def test_premarket_allows_preparation_but_not_start(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 9, 20, tzinfo=ET))

    assert facts.preparation_allowed
    assert not facts.start_allowed


def test_regular_before_start_boundary_does_not_allow_start(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 9, 34, tzinfo=ET))

    assert facts.session is PaperAutonomySessionWindow.REGULAR
    assert not facts.start_allowed


def test_regular_inside_start_window_allows_start(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 10, 0, tzinfo=ET))

    assert facts.start_allowed


def test_regular_after_latest_start_does_not_allow_start(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 15, 31, tzinfo=ET))

    assert not facts.start_allowed


@pytest.mark.parametrize(
    ("moment", "session"),
    [
        (datetime(2026, 9, 28, 17, 0, tzinfo=ET), PaperAutonomySessionWindow.AFTER_HOURS),
        (datetime(2026, 9, 29, 2, 0, tzinfo=ET), PaperAutonomySessionWindow.OVERNIGHT),
        (datetime(2026, 9, 28, 3, 55, tzinfo=ET), PaperAutonomySessionWindow.MAINTENANCE),
        (datetime(2026, 9, 27, 12, 0, tzinfo=ET), PaperAutonomySessionWindow.CLOSED),
    ],
)
def test_non_prepare_and_non_regular_windows_close_permissions(policy, moment, session):
    facts = _schedule(policy, moment)

    assert facts.session is session
    assert not facts.preparation_allowed
    assert not facts.start_allowed


def test_orderly_stop_boundary_is_due_and_closes_new_work(policy):
    facts = _schedule(policy, datetime(2026, 9, 28, 15, 50, tzinfo=ET))

    assert facts.orderly_stop_due
    assert not facts.preparation_allowed
    assert not facts.start_allowed


def test_exceptional_calendar_result_is_uncertain_and_closed(policy, monkeypatch):
    original = paper_autonomy_schedule.us_equity_session
    actual_instant = datetime(2026, 9, 28, 10, 0, tzinfo=ET)

    def classify(moment=None):
        if moment == actual_instant:
            raise RuntimeError("calendar unavailable")
        return original(moment)

    monkeypatch.setattr(paper_autonomy_schedule, "us_equity_session", classify)
    facts = _schedule(policy, actual_instant)

    assert facts.exceptional_schedule_uncertain
    assert facts.session is None
    assert not facts.preparation_allowed
    assert not facts.start_allowed


def test_unmappable_canonical_session_is_uncertain_and_never_permits(policy, monkeypatch):
    original = paper_autonomy_schedule.us_equity_session
    actual_instant = datetime(2026, 9, 28, 10, 0, tzinfo=ET)

    def classify(moment=None):
        if moment == actual_instant:
            return "unknown"
        return original(moment)

    monkeypatch.setattr(paper_autonomy_schedule, "us_equity_session", classify)
    facts = _schedule(policy, actual_instant)

    assert facts.exceptional_schedule_uncertain
    assert facts.session is None
    assert not facts.preparation_allowed
    assert not facts.start_allowed


def test_persistent_calendar_failure_returns_uncertain_facts_without_retry(
    policy, monkeypatch
):
    calls = []

    def unavailable(_moment=None):
        calls.append(_moment)
        raise RuntimeError("unavailable")

    monkeypatch.setattr(
        paper_autonomy_schedule,
        "us_equity_session",
        unavailable,
    )

    moment = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    facts = _schedule(policy, moment)

    assert calls == [moment]
    assert facts.trading_day is None
    assert facts.action_day == date(2026, 9, 28)
    assert facts.session is None
    assert facts.exceptional_schedule_uncertain
    assert not facts.preparation_allowed
    assert not facts.start_allowed


def test_session_mapping_is_exact_and_complete():
    assert {
        session: paper_autonomy_schedule._SESSION_MAP[session]
        for session in USEquitySession
    } == {
        USEquitySession.CLOSED: PaperAutonomySessionWindow.CLOSED,
        USEquitySession.OVERNIGHT: PaperAutonomySessionWindow.OVERNIGHT,
        USEquitySession.MAINTENANCE: PaperAutonomySessionWindow.MAINTENANCE,
        USEquitySession.PREMARKET: PaperAutonomySessionWindow.PREMARKET,
        USEquitySession.REGULAR: PaperAutonomySessionWindow.REGULAR,
        USEquitySession.AFTER_HOURS: PaperAutonomySessionWindow.AFTER_HOURS,
    }
