from __future__ import annotations

from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from us_quant.extended_hours import (
    USEquitySession,
    ibkr_market_data_exchange,
    paper_order_routing,
    us_equity_session,
)


NEW_YORK = ZoneInfo("America/New_York")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, minute, tzinfo=NEW_YORK)


class ExtendedHoursTests(TestCase):
    def test_weekday_sessions_cover_pre_regular_after_and_overnight(self):
        self.assertEqual(us_equity_session(_at(2, 0)), USEquitySession.OVERNIGHT)
        self.assertEqual(us_equity_session(_at(3, 50)), USEquitySession.MAINTENANCE)
        self.assertEqual(us_equity_session(_at(4, 0)), USEquitySession.PREMARKET)
        self.assertEqual(us_equity_session(_at(9, 30)), USEquitySession.REGULAR)
        self.assertEqual(us_equity_session(_at(16, 0)), USEquitySession.AFTER_HOURS)
        self.assertEqual(us_equity_session(_at(20, 0)), USEquitySession.OVERNIGHT)

    def test_weekend_and_observed_holiday_are_closed(self):
        saturday = datetime(2026, 8, 1, 22, 0, tzinfo=NEW_YORK)
        observed_july_fourth = datetime(
            2026, 7, 3, 10, 0, tzinfo=NEW_YORK
        )
        self.assertEqual(us_equity_session(saturday), USEquitySession.CLOSED)
        self.assertEqual(
            us_equity_session(observed_july_fourth),
            USEquitySession.CLOSED,
        )

    def test_sunday_evening_starts_the_overnight_week(self):
        sunday = datetime(2026, 8, 2, 20, 0, tzinfo=NEW_YORK)
        friday_night = datetime(2026, 7, 31, 20, 0, tzinfo=NEW_YORK)
        self.assertEqual(us_equity_session(sunday), USEquitySession.OVERNIGHT)
        self.assertEqual(us_equity_session(friday_night), USEquitySession.CLOSED)

    def test_extended_routing_requires_switch_outside_regular_hours(self):
        disabled = paper_order_routing(
            extended_hours_enabled=False,
            now=_at(8, 0),
        )
        enabled = paper_order_routing(
            extended_hours_enabled=True,
            now=_at(8, 0),
        )
        self.assertFalse(disabled.allowed)
        self.assertTrue(enabled.allowed)
        self.assertEqual(enabled.exchange, "SMART")
        self.assertTrue(enabled.outside_rth)

    def test_overnight_uses_direct_overnight_route_and_data_venue(self):
        routing = paper_order_routing(
            extended_hours_enabled=True,
            now=_at(22, 0),
        )
        self.assertTrue(routing.allowed)
        self.assertEqual(routing.exchange, "OVERNIGHT")
        self.assertEqual(routing.tif, "DAY")
        self.assertFalse(routing.outside_rth)
        self.assertEqual(ibkr_market_data_exchange(_at(22, 0)), "OVERNIGHT")
        self.assertEqual(ibkr_market_data_exchange(_at(8, 0)), "SMART")

    def test_maintenance_is_never_routable(self):
        routing = paper_order_routing(
            extended_hours_enabled=True,
            now=_at(3, 55),
        )
        self.assertFalse(routing.allowed)
        self.assertEqual(routing.session, USEquitySession.MAINTENANCE)
