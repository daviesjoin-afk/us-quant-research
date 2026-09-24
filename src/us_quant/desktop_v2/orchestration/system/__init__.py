"""Desktop System orchestration (v2O-F1).

System is a *containment* route: ``RuntimeEventsPage`` and ``SettingsPage`` are
two unrelated capabilities that happen to share one page.  This package is where
those capabilities get their own owners, one at a time, and it exists as a
package for the same reason ``orchestration/research`` does -- so the route
aggregate is a directory of owners rather than one owner of the route.

``runtime_events/`` is the first and, so far, only capability here (v2O-F1): the
persisted runtime-event store write, the coalesced repaint, the resolve command
and the terminal-export sequencing used to live on ``MainWindow``.

What is deliberately absent, and must stay absent:

* **no ``SystemOrchestrator``, ``SystemManager`` or ``DesktopSystemManager``.**
  A route aggregate is navigation, not runtime truth.  An orchestrator owning
  both workspaces would have to hold the event store *and* the settings service
  *and* the credential service, which is the god object this decomposition
  exists to prevent -- and it would make "who owns runtime events?" a question
  about a System controller rather than about Runtime Events;
* **no Settings orchestration here.**  The settings half is v2O-F2 and remains
  on ``MainWindow`` until that round;
* **no Gateway/connection probe.**  ``_probe_gateway``, ``probe_ibkr_socket``
  and ``gateway_badge`` are connection-level facts, not Runtime Events; whether
  they get their own owner is decided after F2.
"""

from __future__ import annotations

__all__: list[str] = []
