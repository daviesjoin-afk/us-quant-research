"""The canonical trading domain.

This package is the single home for the core trading types.  It deliberately
re-exports nothing: importers must name the module they mean, e.g.
``from us_quant.trading.domain.orders import OrderIntent``.  A package-level
star export would give every type a second import path and make it possible
to reintroduce the duplicate-definition problem this change removes.

Nothing in here may import a broker client, Qt, SQL, or any sibling layer.
"""

from __future__ import annotations
