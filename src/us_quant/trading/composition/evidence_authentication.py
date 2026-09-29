"""Composition for research-evidence authentication.

Binds the verifier, the external trust root and the durable store.  There is
deliberately no signer here and no way to configure one: signing is research
tooling, and a trading composition that could sign would be able to mint the
very evidence it is supposed to be checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.evidence_signature import (
    Ed25519EvidenceSignatureVerifier,
)
from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.research_evidence import TargetedReviewArtifactSource
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.application.evidence_authentication import (
    EvidenceAuthenticationApplication,
)


@dataclass(frozen=True, slots=True)
class EvidenceAuthenticationComponents:
    authenticator: EvidenceAuthenticationApplication
    repository: SQLiteEvidenceAuthenticationRepository
    key_source: FileEvidenceVerificationKeySource
    signature_verifier: Ed25519EvidenceSignatureVerifier
    artifact_source: TargetedReviewArtifactSource


def build_evidence_authentication_components(
    *,
    database_path: str | Path,
    trust_store_path: str | Path,
) -> EvidenceAuthenticationComponents:
    """Bind only verification-side components and their independent store.

    This is the single place that knows both the authenticator and its concrete
    adapters -- the artifact loader, the trust root, the signature verifier and
    the store.  The application layer below sees only ports.
    """

    artifact_source = TargetedReviewArtifactSource()
    key_source = FileEvidenceVerificationKeySource(trust_store_path)
    signature_verifier = Ed25519EvidenceSignatureVerifier()
    return EvidenceAuthenticationComponents(
        authenticator=EvidenceAuthenticationApplication(
            artifact_source=artifact_source,
            key_source=key_source,
            signature_verifier=signature_verifier,
        ),
        repository=SQLiteEvidenceAuthenticationRepository(database_path),
        key_source=key_source,
        signature_verifier=signature_verifier,
        artifact_source=artifact_source,
    )


__all__ = [
    "EvidenceAuthenticationComponents",
    "build_evidence_authentication_components",
]
