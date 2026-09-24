"""Immutable facts the Runtime Events capability is composed with.

One value object and nothing else.  It is not state and it is not truth: it is
the *installation* the info panel describes, handed in frozen so the capability
never reaches for ``ApplicationPaths`` and the window never assembles the panel
text.  A capability that fetched its own roots would depend on the path layout;
a window that formatted the text would still be the display owner of a page it
no longer orchestrates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeEventsEnvironment:
    """The read-only environment facts the Runtime Events info panel prints.

    ``version`` is the application version the window composes, and the four
    roots are the paths a maintainer needs when a run is being diagnosed.  They
    are typed ``object`` on purpose: they are printed, never joined or stat-ed
    here, so the capability stays independent of whether the composition root
    passes a ``Path`` or the string form of one.
    """

    version: str
    resource_root: object
    state_root: object
    runtime_root: object
    exports_root: object


__all__ = ["RuntimeEventsEnvironment"]
