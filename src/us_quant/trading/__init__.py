"""Trading Core v2.

The package is layered so that dependencies only ever point inwards:

    Desktop / CLI
        -> Application Commands / Queries
            -> Trading Runtime
                -> Domain
                    ^
                  Ports
                    ^
                Adapters

``domain`` is pure Python and knows nothing about brokers, Qt or SQL.
``ports`` describe the boundaries the runtime needs and depend only on
``domain``.  ``application``, ``runtime``, ``adapters`` and ``composition``
are deliberately empty in this change: the first real migration is Market
Data v2, which lands in a following change.
"""

from __future__ import annotations
