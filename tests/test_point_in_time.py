from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from us_quant.point_in_time import (
    DelistingEvent,
    PointInTimeCoverageError,
    PointInTimeUniverse,
    UniverseMembership,
    delisting_settlement_value,
)


def membership(
    symbol: str,
    start: date,
    end: date | None,
    *,
    tier: int = 1,
    eligible: bool = True,
) -> UniverseMembership:
    return UniverseMembership(
        symbol=symbol,
        effective_from=start,
        effective_to=end,
        eligible_for_research=eligible,
        leader_tier=tier,
        sector="Technology",
        country_status="eligible_non_china",
        source_snapshot_hash="snapshot",
    )


def test_members_at_excludes_pre_listing_and_post_delisting_dates() -> None:
    universe = PointInTimeUniverse(
        memberships=(
            membership("OLD", date(2020, 1, 1), date(2022, 1, 1)),
            membership("NEW", date(2022, 1, 1), None),
        ),
        coverage_start=date(2020, 1, 1),
        coverage_end=date(2024, 12, 31),
        source_hash="history",
        complete_history=True,
    )

    assert [row.symbol for row in universe.members_at(date(2021, 6, 1))] == [
        "OLD"
    ]
    assert [row.symbol for row in universe.members_at(date(2022, 6, 1))] == [
        "NEW"
    ]


def test_members_at_uses_historical_leader_tier() -> None:
    universe = PointInTimeUniverse(
        memberships=(
            membership("AAA", date(2020, 1, 1), date(2023, 1, 1), tier=2),
            membership("AAA", date(2023, 1, 1), None, tier=3),
        ),
        coverage_start=date(2020, 1, 1),
        coverage_end=date(2024, 12, 31),
        source_hash="history",
        complete_history=True,
    )

    assert universe.members_at(
        date(2022, 6, 1), leader_tiers=frozenset({1, 2})
    )
    assert not universe.members_at(
        date(2023, 6, 1), leader_tiers=frozenset({1, 2})
    )


def test_current_only_universe_is_rejected_for_historical_research() -> None:
    universe = PointInTimeUniverse(
        memberships=(),
        coverage_start=date(2024, 1, 1),
        coverage_end=date(2024, 12, 31),
        source_hash="current",
        complete_history=False,
    )

    with pytest.raises(PointInTimeCoverageError, match="current-only"):
        universe.members_at(date(2024, 6, 1))


def test_delisting_requires_explicit_settlement_evidence() -> None:
    unknown = DelistingEvent(
        symbol="OLD",
        delisting_date=date(2022, 1, 1),
        settlement_price=None,
        reason="unknown",
        source="filing",
    )
    with pytest.raises(PointInTimeCoverageError):
        delisting_settlement_value(unknown, quantity=3)

    known = DelistingEvent(
        symbol="OLD",
        delisting_date=date(2022, 1, 1),
        settlement_price=Decimal("4.25"),
        reason="cash merger",
        source="filing",
    )
    assert delisting_settlement_value(known, quantity=3) == Decimal("12.75")
