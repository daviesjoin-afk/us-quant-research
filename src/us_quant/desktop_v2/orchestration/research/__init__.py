"""Research orchestration: one subpackage per workspace, deliberately no aggregate.

Research is a *route aggregate* in the navigation sense -- it groups Universe,
History, Scanner, Backtest/Cross-Section and Targeted research behind one
workspace switcher -- and that grouping is not a reason to build a single object
that owns all of them.  A ``ResearchOrchestrator`` holding every workspace would
be a second ``MainWindow``: the whole point of this decomposition is that
changing one workspace means reading one directory, and an aggregate controller
puts every workspace back in one file.

So this package is a namespace, not an owner.  It has no ``orchestrator.py``, and
``tests/test_desktop_research_foundations_architecture.py`` fails if one appears
or if any of ``ResearchOrchestrator`` / ``ResearchManager`` / ``ResearchContext``
/ ``ResearchServices`` / ``ResearchController`` is declared anywhere under here.

Each workspace owns its own runtime under its own subpackage:

* ``universe`` -- the official universe snapshot and its refresh lifecycle;
* ``history``  -- the local history queue's task intent and progress.

The remaining Research workspaces are later v2O-C slices; they get their own
subpackages when they are extracted, not a shared parent.

The two capabilities here are deliberately ignorant of each other.  History needs
a universe to schedule against, so it takes a
``Callable[[], UniverseSnapshot | None]`` rather than a ``UniverseOrchestrator``:
the provider is a stable fact-shaped boundary that survives any change to how the
universe is implemented.
"""

from __future__ import annotations

__all__: list[str] = []
