"""The single source of truth for Desktop UI v2 first-level navigation.

Routes are defined once, here.  The shell, the tests and the layout audit all
read this tuple rather than repeating route strings, so adding or reordering a
section is a one-line change and cannot drift between call sites.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NavigationItem:
    """One first-level navigation entry: a stable route id and its label."""

    route: str
    label: str


NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    NavigationItem(route="dashboard", label="总览"),
    NavigationItem(route="market", label="行情"),
    NavigationItem(route="account", label="账户"),
    NavigationItem(route="strategy", label="策略"),
    NavigationItem(route="risk", label="风控"),
    NavigationItem(route="execution", label="订单与成交"),
    NavigationItem(route="research", label="研究"),
    NavigationItem(route="system", label="系统"),
)

ROUTES: tuple[str, ...] = tuple(item.route for item in NAVIGATION_ITEMS)

DEFAULT_ROUTE: str = NAVIGATION_ITEMS[0].route


def label_for(route: str) -> str:
    """Return the display label for a route, or raise on an unknown route."""
    for item in NAVIGATION_ITEMS:
        if item.route == route:
            return item.label
    raise KeyError(route)
