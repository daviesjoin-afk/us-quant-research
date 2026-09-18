"""Application layer (skeleton).

Deliberately empty in this change.  Application services -- commands and
queries that the UI and CLI call -- land with Market Data v2, which is the
next change.  Creating them now would mean inventing orchestration before the
first real use case exists.

No ``TradingManager``, ``GlobalAppState``, ``ServiceLocator`` or
``ApplicationContext`` belongs in this package: the runtime is composed
explicitly at the composition root, not resolved through a global registry.
"""

from __future__ import annotations
