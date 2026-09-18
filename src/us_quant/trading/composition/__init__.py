"""Composition root (skeleton).

Deliberately empty in this change.  This is where adapters will be chosen and
wired to the runtime.  Today the desktop ``MainWindow`` is still the
composition root, which is explicitly a temporary arrangement: as each
pipeline migrates, its wiring moves here and out of the window.
"""

from __future__ import annotations
