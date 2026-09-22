"""The targeted research orchestration capabilities.

C5A is split in two on purpose, and only the first half is here:

* **evidence** -- the research evidence truth, its two requests, its two
  selections and its render.  This round;
* **session/preflight** -- the target symbol, the target status, the minute
  evidence status and the preflight.  Deliberately *not* created yet: it depends
  on the universe, the market stream, the account, the strategy selection and the
  Shadow session, so it needs its own round rather than being bundled here.

There is deliberately no ``targeted/orchestrator.py``, no ``TargetedOrchestrator``
and no shared ``TargetedContext``/``TargetedState``/``TargetedServices``.  One
object owning evidence *and* session *and* Shadow is the failure mode this split
exists to prevent.
"""

__all__: list[str] = []
