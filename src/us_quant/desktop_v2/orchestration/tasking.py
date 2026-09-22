"""The type-level contract between a capability and the window's task boundary.

Every orchestration capability so far -- Account, Universe, History, Scanner and
now Backtest -- depends on ``MainWindow._start_task``, and every one of them
declared it as ``Callable[..., bool]``.  That signature is accurate and useless:
reading an orchestrator's constructor told a reviewer nothing about what it
expects, so answering "what does this capability need from the window?" meant
opening ``desktop.py`` and reading ``_start_task``.

This module is that answer, written down once.  It is deliberately *only* types:
a protocol and two callable aliases, no Qt, no worker, no lifecycle.

What it does not do, and must not start doing:

* **it does not run anything.**  The generic task lifecycle -- ``TaskThread``,
  ``DesktopTaskController``, the worker list, the closing admission gate, the
  busy dialog and the cancellation handling -- stays on ``MainWindow``.  A
  capability receives a ``TaskSubmitter`` and never learns which worker it
  started;
* **it does not own state.**  There is no resource-group registry and no worker
  bookkeeping here.  ``TaskAdmissionQuery`` is a *question*, and the window
  answers it from the controller it already owns.  An orchestrator may use it
  for a UI pre-check; the authoritative admission remains ``_start_task``'s own.

The distinction those two bullets protect is the one that makes a capability
testable: it can be driven with a lambda that records its arguments instead of a
running Qt event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

#: What a background task calls to write a progress line.
ProgressReporter = Callable[[str], None]

#: A unit of background work: it is handed a progress reporter and returns its
#: own result, which the window passes back to ``on_success`` unchanged.
BackgroundTask = Callable[[ProgressReporter], object]


class TaskSubmitter(Protocol):
    """Submit one background task and be told how it ended.

    ``MainWindow._start_task`` already satisfies this protocol; the protocol is
    not a wrapper around it and no adapter is constructed.  Its only job is to
    make the dependency legible at the call site.

    Returning ``False`` means the task was *refused* and never started -- the
    window is closing, or the resource group is already busy.  A capability that
    optimistically flipped its own busy flag before submitting must roll it back
    on ``False``; the two are not the same event as a task that started and
    failed, and the window reports them differently.
    """

    def __call__(
        self,
        task: BackgroundTask,
        *,
        on_success: Callable[[object], None],
        on_failure: Callable[[str], None] | None = None,
        start_message: str,
        resource_group: str = "research",
        suppress_busy_message: bool = False,
        shutdown_essential: bool = False,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        ...


#: Ask whether a resource group could accept a task right now.
#:
#: This is a *pre-check* for a capability that wants to refuse before it
#: validates anything else, so the operator sees the busy dialog rather than a
#: date-validation warning.  It is not admission: the answer can change before
#: the task is submitted, and only ``TaskSubmitter`` decides.
TaskAdmissionQuery = Callable[[str], bool]


__all__ = [
    "BackgroundTask",
    "ProgressReporter",
    "TaskAdmissionQuery",
    "TaskSubmitter",
]
