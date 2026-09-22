"""The targeted evidence orchestration capability.

``TargetedEvidenceOrchestrator`` owns the Targeted workspace's *research
evidence* runtime: the seven result families and the two historical selections,
as one immutable snapshot; the replay and robustness requests; the two selection
intents; and the one evidence render entry point.

Why this is its own capability rather than part of a ``TargetedOrchestrator``:
the Targeted workspace held three unrelated things -- research evidence, the
target session/preflight, and the Shadow runtime.  Folding all three into one
class would have manufactured a second ``MainWindow`` rather than decomposing the
first.  Evidence is the part whose canonical truth is a research artifact and
whose only cross-capability consumer is the terminal export.

Its two halves are deliberately asymmetric in timing: the strategy version, the
target symbol and the research capital are frozen at *request* time, while the
minute evidence is read at *execution* time by the service.  The universe is read
at request time and only as an eligibility gate -- no universe is not a refusal.

Adjacent capabilities that are deliberately **not** here, and stay on the window
for now:

* the target symbol editor, the target status, the minute-evidence status and the
  preflight (v2O-C5B).  They depend on the universe, the market stream, the
  account, the strategy selection and the Shadow session -- a completely
  different dependency shape from research evidence;
* the Shadow engine, its start/stop and stream ingestion (v2O-D).
"""

from __future__ import annotations

from .orchestrator import (
    REPLAY_PURPOSE,
    REPLAY_START_MESSAGE,
    ROBUSTNESS_PURPOSE,
    ROBUSTNESS_START_MESSAGE,
    TARGETED_RESOURCE_GROUP,
    TargetedEvidenceOrchestrator,
)

__all__ = [
    "REPLAY_PURPOSE",
    "REPLAY_START_MESSAGE",
    "ROBUSTNESS_PURPOSE",
    "ROBUSTNESS_START_MESSAGE",
    "TARGETED_RESOURCE_GROUP",
    "TargetedEvidenceOrchestrator",
]
