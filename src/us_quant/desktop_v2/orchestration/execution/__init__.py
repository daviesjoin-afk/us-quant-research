"""The execution / AutoQuant orchestration capability (G2-B).

``ExecutionOrchestrator`` owns the desktop AutoQuant route's sequencing: the
runtime strategy selection and its combo, the preflight, the two-step candidate
preparation (scan, adopt, queue history, select), the order-channel probe, the
launch confirmation, the control state and the whole session render.

It owns exactly three route-local facts -- a launch-busy flag, a probe-in-flight
flag and the retained candidate shortlist -- and reads everything else through a
provider on every use.  It never touches Paper's lifecycle: the three
preparation transitions go through narrow delegated seams on
``PaperOrchestrator``, and this package imports no Paper type at all.

The Qt-free half lives beside it: :mod:`models` holds the route's immutable
facts, its dialog copy, the provider group and the structural ports;
:mod:`queries` holds the pure rules -- reference-symbol normalization, strategy
eligibility, capital bounding, the shortlist projection and the sentences the
route publishes.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.execution.models import (
    CandidateSelection,
    ExecutionProviders,
    MarketReadinessFact,
    PaperFactsPort,
)
from us_quant.desktop_v2.orchestration.execution.orchestrator import (
    ExecutionOrchestrator,
)

__all__ = [
    "CandidateSelection",
    "ExecutionOrchestrator",
    "ExecutionProviders",
    "MarketReadinessFact",
    "PaperFactsPort",
]
