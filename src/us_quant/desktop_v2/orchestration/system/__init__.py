"""Desktop System orchestration (v2O-F1 Runtime Events, v2O-F2 Settings).

System is a *containment* route: ``RuntimeEventsPage`` and ``SettingsPage`` are
two unrelated capabilities that happen to share one page.  This package is where
those capabilities get their own owners, one at a time, and it exists as a
package for the same reason ``orchestration/research`` does -- so the route
aggregate is a directory of owners rather than one owner of the route.

Two capabilities live here, and they are siblings rather than layers:

* ``runtime_events/`` (v2O-F1) owns the persisted runtime-event store write, the
  coalesced repaint, the resolve command and the terminal-export sequencing;
* ``settings/`` (v2O-F2) owns the settings view render, the credential
  save/clear sequencing, the preference transaction adapter, the provider syncs
  and the capability-toggle confirmations.

What is deliberately absent, and must stay absent:

* **no ``SystemOrchestrator``, ``SystemManager`` or ``DesktopSystemManager``.**
  A route aggregate is navigation, not runtime truth.  An orchestrator owning
  both workspaces would hold the event store *and* the settings service *and*
  the credential service at once, which is the god object this decomposition
  exists to prevent -- and it would make "who owns a runtime event?" or "who
  owns the settings transaction?" a question about a System controller rather
  than about the capability that answers it;
* **no shared state between the two.**  Runtime Events and Settings share the
  page and nothing else: no settings fact is read by the event capability, no
  event fact is read by the settings capability, and neither imports the other;
* **no Gateway/connection probe.**  ``_probe_gateway``, ``probe_ibkr_socket``
  and ``gateway_badge`` are connection-level facts that belong to neither
  workspace; whether they need an owner of their own is decided after F2, once
  the remaining System responsibilities have been re-audited.
"""

from __future__ import annotations

__all__: list[str] = []
