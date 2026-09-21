"""Stable identity for System secondary navigation.

The order and labels below are the single source of truth for the System
aggregate.  This module is deliberately Qt-free so tooling and tests can use
the semantic workspace keys without importing a widget toolkit: a route that
keyed off an integer tab index would silently break the moment a tab moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SystemWorkspace(str, Enum):
    """The two workspaces the System route owns, in frozen display order."""

    RUNTIME_EVENTS = "runtime_events"
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class SystemNavigationItem:
    """One System workspace: a stable semantic key and the label shown."""

    workspace: SystemWorkspace
    label: str


SYSTEM_NAVIGATION_ITEMS: tuple[SystemNavigationItem, ...] = (
    SystemNavigationItem(SystemWorkspace.RUNTIME_EVENTS, "运行事件"),
    SystemNavigationItem(SystemWorkspace.SETTINGS, "系统设置"),
)
