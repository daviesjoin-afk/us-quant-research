"""Natively-built Desktop UI v2 pages.

A module here is a page that v2 owns outright: it renders domain state, knows
no application service or adapter, and has no legacy builder behind it.  As
each main chain migrates, its old ``MainWindow`` builder is deleted and its
replacement lands here.
"""

from __future__ import annotations

__all__: list[str] = []
