"""Shadow orchestration (v2O-D): the internal simulation's desktop owner.

The Shadow runtime -- the engine, its snapshot, the store and the execution lease
-- used to be held by ``MainWindow`` and driven from six places.  It is owned by
:class:`ShadowOrchestrator` now, and the window only constructs it, wires its
signals and routes the facts it publishes.

Three modules, one responsibility each:

``models.py``
    the immutable facts and the operator-facing wording.  Qt-free.
``queries.py``
    the pure start rules: which versions may run, which symbols the pool admits,
    which quote may price a run.  Qt-free, no I/O.
``orchestrator.py``
    the sequencing: read the facts once, gate, build the engine, start it, feed
    it snapshots, publish what it produced.

What is deliberately *not* here: the shadow engine, its trade logic, its marks
and its SQLite store.  Those stay in ``us_quant.shadow``, which this package
consumes through its public API and never reimplements.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.shadow.orchestrator import (
    ShadowOrchestrator,
)

__all__ = ["ShadowOrchestrator"]
