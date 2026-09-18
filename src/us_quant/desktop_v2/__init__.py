"""Desktop UI v2.

The v2 shell is a view container and nothing else: it knows a fixed list of
routes, which widget is registered for each, and which one is showing.  It has
no broker connection, no market-data stream and no order path, so it cannot
become the orchestrator that ``MainWindow`` is today.

``MainWindow`` remains the composition root for now -- it builds the existing
page widgets and hands them to the shell -- and gives up that role as each
page is rewritten.
"""

from __future__ import annotations
