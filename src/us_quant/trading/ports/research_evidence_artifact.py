"""Port for loading one persisted research-evidence artifact.

The application layer decides whether evidence is *authentic*; it must not know
how evidence is *stored*.  This port is that seam.  Without it the
authenticator would have to import the artifact adapter directly, which is the
one edge the inward-layer architecture forbids -- and it would also mean the
authentication decision could be re-pointed at a different loader by whoever
assembles the composition, which is exactly where that choice belongs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from us_quant.trading.domain.research_evidence import LoadedResearchEvidence


class ResearchEvidenceArtifactUnavailable(RuntimeError):
    """The artifact is missing, malformed, or inconsistent with its own identity.

    Callers must treat this as fail-closed.  It is deliberately a single error
    rather than a family: from the application's point of view every one of
    those cases means the same thing -- there is no evidence here to
    authenticate.
    """


class ResearchEvidenceArtifactSourcePort(Protocol):
    """Load a validated artifact payload and its evidence projection."""

    def load(self, artifact_path: str | Path) -> LoadedResearchEvidence:
        """Return the parsed payload and its projection from one read.

        Raises :class:`ResearchEvidenceArtifactUnavailable` when the artifact
        cannot be read as evidence.
        """


__all__ = [
    "ResearchEvidenceArtifactSourcePort",
    "ResearchEvidenceArtifactUnavailable",
]
