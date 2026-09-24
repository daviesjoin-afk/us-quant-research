"""Settings orchestration (v2O-F2): the System settings workspace's owner.

The Settings workspace is one capability with one runtime, one transaction and
one sequence, so it gets one owner and three modules:

``models.py``
    the immutable credential-action contract.  Qt-free.
``queries.py``
    the pure rules and projections: the credential-action decision, the status
    line, the draft/preferences mappings, the storage view.  Qt-free, no I/O.
``orchestrator.py``
    the sequencing: the page render, the credential save/clear paths, the
    preference transaction adapter, the provider syncs, the capability-toggle
    confirmations and the facts published to the composition root.

What is deliberately *not* here: the settings transaction
(``DesktopSettingsService`` owns validate -> derive -> preflight -> persist ->
apply), the credential semantics (``DesktopCredentialService``) and the
persisted preferences (``UserPreferencesStore``).  Neither is the application
configuration: ``AppConfig`` / ``UserPreferences`` stay the composition root's,
read through narrow callables, so Market, Paper, Risk and Research never have to
depend on Settings for the current configuration.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.system.settings.models import (
    CredentialSaveOutcome,
    CredentialSavePlan,
)
from us_quant.desktop_v2.orchestration.system.settings.orchestrator import (
    SettingsOrchestrator,
)

__all__ = [
    "CredentialSaveOutcome",
    "CredentialSavePlan",
    "SettingsOrchestrator",
]
