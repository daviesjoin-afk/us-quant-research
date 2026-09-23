"""The targeted session and preflight orchestration capability.

``TargetedSessionOrchestrator`` owns the Targeted workspace's session half: the
operator's current target draft, that target's status, the local minute-evidence
status, the last preflight result, and the one session render entry point.

This is the second half of the C5 split.  The Targeted workspace held three
unrelated things, and only the first moved in v2O-C5A:

* the research evidence -- seven artifact families, two run selections, the replay
  and robustness requests -- is ``targeted/evidence/`` (v2O-C5A);
* the target session and preflight -- the target draft, its status, the minute
  evidence and the preflight that gates an internal simulation -- is here
  (v2O-C5B);
* the Shadow runtime -- the engine, its snapshot as mutable truth, start/stop and
  stream ingestion -- stays on the window until v2O-D.

**"Session" here means a workspace presentation session, not a trading session.**
That is why the class is not ``ShadowSessionOrchestrator``: it does not own the
Shadow session, it *renders* one.  The Shadow snapshot arrives through a provider
at paint time and is never stored, so shadow start/stop, stream ingress and
position changes all reach the page by calling ``render_current()`` rather than by
writing a field here.

The timing and safety rules the capability owns, each frozen and tested:

* the target draft is the operator's *input*.  Typing a symbol and not applying it
  still lets Replay and Robustness read it, because that is the existing
  behaviour -- so the name says "draft", not "applied target";
* the draft signal records the draft and nothing else.  No preflight, no store
  read, no Market write, no start, no log, no repaint: one keystroke is one
  assignment;
* 应用标的 refuses while an internal simulation runs, and restores both the page
  and the canonical draft to the symbol the engine is actually trading;
* 订阅该标的行情 refuses while the feed is live, and refreshes the preflight
  *before* requesting the start;
* the preflight is derived.  It is committed only when the evaluator returned, and
  a failure **propagates** with the previous verdict untouched -- never a silently
  swallowed error leaving a stale verdict looking current;
* ``broker_orders_available`` is hard-disabled in the service, so no caller can
  give Research Targeted broker execution authority.
"""

from __future__ import annotations

from .models import (
    REFUSAL_INFORMATION,
    REFUSAL_WARNING,
    TargetedSessionSnapshot,
)
from .orchestrator import TargetedSessionOrchestrator

__all__ = [
    "REFUSAL_INFORMATION",
    "REFUSAL_WARNING",
    "TargetedSessionOrchestrator",
    "TargetedSessionSnapshot",
]
