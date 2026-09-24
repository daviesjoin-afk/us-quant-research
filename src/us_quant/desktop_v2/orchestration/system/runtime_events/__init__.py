"""Runtime Events orchestration (v2O-F1): the System event workspace's owner.

The Runtime Events workspace is one capability with one runtime and one
sequence, so it gets one owner and three files:

``models.py``
    the immutable environment facts the info panel prints.  Qt-free.
``orchestrator.py``
    the sequence: the single store write, the immediate-or-coalesced repaint,
    the resolve command, the terminal export's outcome, and the presentation
    messages the window shows.

What is deliberately *not* here: the store (``us_quant.runtime_events`` owns the
schema, the insert, the resolve and the redaction), the view models and the
widgets (the page renders and emits), the generic task lifecycle (the window
owns ``TaskThread`` and its controller), and the cross-capability facts a
terminal export carries -- those are gathered by the composition root and
arrive as one injected callable.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.system.runtime_events.models import (
    RuntimeEventsEnvironment,
)
from us_quant.desktop_v2.orchestration.system.runtime_events.orchestrator import (
    RuntimeEventsOrchestrator,
)

__all__ = ["RuntimeEventsEnvironment", "RuntimeEventsOrchestrator"]
