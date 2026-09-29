"""The one authority that decides whether research evidence is authenticated.

This service is deliberately narrow.  It reads a review artifact and its
detached seal, checks the signature against a key resolved from an *external*
trust root, binds the sealed hashes to the governed version, and reports a
verdict.  It owns no repository, writes no lifecycle state, and cannot sign.

Every failure mode returns a FAIL verdict with an explicit blocker.  There is
no path here that turns an unknown key, an unreadable trust root, a malformed
seal or a digest mismatch into a PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from us_quant.trading.domain import evidence_auth as _evidence_auth
from us_quant.trading.domain.evidence_auth import (
    AuthenticatedStrategyResearchEvidence,
    EvidenceAuthenticationBlocker,
    EvidenceAuthenticationPolicy,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
    EvidenceSealMalformed,
    ResearchEvidenceSeal,
    artifact_binding_blockers,
    canonical_artifact_payload_digest,
    default_seal_path,
    seal_from_payload,
    stable_evidence_authentication_id,
    version_binding_blockers,
)
from us_quant.trading.domain.research_evidence import StrategyResearchEvidence
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.ports.evidence_verification import (
    EvidenceTrustRootUnavailable,
    MalformedEvidenceSignature,
    UnsupportedEvidenceAlgorithm,
)
from us_quant.trading.ports.research_evidence_artifact import (
    ResearchEvidenceArtifactUnavailable,
)


@dataclass(frozen=True, slots=True)
class EvidenceAuthenticationOutcome:
    """One evaluation, both projections.

    The durable record is always present.  Authenticated evidence is present
    only on PASS, so a caller cannot accidentally treat a FAIL as usable
    evidence by forgetting to check the verdict.
    """

    result: EvidenceAuthenticationResult
    authenticated_evidence: AuthenticatedStrategyResearchEvidence | None


class EvidenceAuthenticationApplication:
    """Verify a detached seal; never sign, never mutate lifecycle state."""

    def __init__(self, *, artifact_source, key_source, signature_verifier) -> None:
        if artifact_source is None or key_source is None or signature_verifier is None:
            raise TypeError(
                "artifact_source, key_source and signature_verifier are required"
            )
        self._artifact_source = artifact_source
        self._key_source = key_source
        self._signature_verifier = signature_verifier

    def authenticate(
        self,
        *,
        version: StrategyVersion,
        artifact_path: str | Path,
        policy: EvidenceAuthenticationPolicy,
        verified_at: datetime,
        seal_path: str | Path | None = None,
    ) -> EvidenceAuthenticationOutcome:
        if not isinstance(version, StrategyVersion):
            raise TypeError("version must be StrategyVersion")
        if not isinstance(policy, EvidenceAuthenticationPolicy):
            raise TypeError("policy must be EvidenceAuthenticationPolicy")
        if not isinstance(verified_at, datetime):
            raise TypeError("verified_at must be a datetime")
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")

        blockers: set[EvidenceAuthenticationBlocker] = set()

        try:
            loaded = self._artifact_source.load(artifact_path)
        except ResearchEvidenceArtifactUnavailable:
            return self._fail(
                version=version,
                policy=policy,
                verified_at=verified_at,
                blockers={EvidenceAuthenticationBlocker.AUTHENTICATION_MISSING},
            )
        evidence = loaded.evidence
        artifact_digest = canonical_artifact_payload_digest(loaded.payload)

        seal = self._load_seal(artifact_path, seal_path, blockers)
        if seal is None:
            return self._fail(
                version=version,
                policy=policy,
                verified_at=verified_at,
                blockers=blockers,
                review_run_id=evidence.review_run_id,
                artifact_digest=artifact_digest,
            )

        blockers |= artifact_binding_blockers(
            seal=seal, evidence=evidence, artifact_digest=artifact_digest
        )
        blockers |= version_binding_blockers(seal=seal, version=version)
        self._evaluate_signed_at(seal, policy, verified_at, blockers)
        self._evaluate_signature(seal, policy, blockers)

        if blockers:
            return self._fail(
                version=version,
                policy=policy,
                verified_at=verified_at,
                blockers=blockers,
                review_run_id=evidence.review_run_id,
                artifact_digest=artifact_digest,
                seal=seal,
            )
        return self._pass(
            version=version,
            evidence=evidence,
            seal=seal,
            policy=policy,
            verified_at=verified_at,
            artifact_digest=artifact_digest,
        )

    # -- steps -----------------------------------------------------------

    @staticmethod
    def _load_seal(
        artifact_path: str | Path,
        seal_path: str | Path | None,
        blockers: set[EvidenceAuthenticationBlocker],
    ) -> ResearchEvidenceSeal | None:
        path = Path(seal_path) if seal_path is not None else default_seal_path(artifact_path)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            blockers.add(EvidenceAuthenticationBlocker.AUTHENTICATION_MISSING)
            return None
        except (OSError, UnicodeError):
            blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_MALFORMED)
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_MALFORMED)
            return None
        try:
            return seal_from_payload(payload)
        except EvidenceSealMalformed:
            blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_MALFORMED)
            return None

    @staticmethod
    def _evaluate_signed_at(
        seal: ResearchEvidenceSeal,
        policy: EvidenceAuthenticationPolicy,
        verified_at: datetime,
        blockers: set[EvidenceAuthenticationBlocker],
    ) -> None:
        signed_at = seal.signed_at
        if signed_at.tzinfo is None or signed_at.utcoffset() is None:
            blockers.add(EvidenceAuthenticationBlocker.SIGNED_AT_INVALID)
            return
        if (
            signed_at.astimezone(timezone.utc)
            > verified_at.astimezone(timezone.utc) + policy.maximum_clock_skew
        ):
            blockers.add(EvidenceAuthenticationBlocker.SIGNED_AT_INVALID)

    def _evaluate_signature(
        self,
        seal: ResearchEvidenceSeal,
        policy: EvidenceAuthenticationPolicy,
        blockers: set[EvidenceAuthenticationBlocker],
    ) -> None:
        if seal.algorithm not in policy.supported_algorithms:
            blockers.add(EvidenceAuthenticationBlocker.UNSUPPORTED_ALGORITHM)
            return

        try:
            key = self._key_source.verification_key(seal.key_id)
        except EvidenceTrustRootUnavailable:
            blockers.add(EvidenceAuthenticationBlocker.TRUST_ROOT_UNAVAILABLE)
            return
        if key is None:
            blockers.add(EvidenceAuthenticationBlocker.UNKNOWN_KEY)
            return
        if key.algorithm != seal.algorithm:
            blockers.add(EvidenceAuthenticationBlocker.UNSUPPORTED_ALGORITHM)
            return
        # A revoked key fails closed for everything it ever signed, before the
        # signature is even considered.  Rotation must not silently re-admit it.
        if key.trust_status is EvidenceKeyTrustStatus.REVOKED:
            blockers.add(EvidenceAuthenticationBlocker.KEY_REVOKED)
            return

        try:
            valid = self._signature_verifier.verify(
                algorithm=seal.algorithm,
                public_key=key.public_key,
                message=seal.signed_material,
                signature=seal.signature,
            )
        except UnsupportedEvidenceAlgorithm:
            blockers.add(EvidenceAuthenticationBlocker.UNSUPPORTED_ALGORITHM)
            return
        except MalformedEvidenceSignature:
            blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_MALFORMED)
            return
        if not valid:
            blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_INVALID)

    # -- result construction ---------------------------------------------

    def _fail(
        self,
        *,
        version: StrategyVersion,
        policy: EvidenceAuthenticationPolicy,
        verified_at: datetime,
        blockers: set[EvidenceAuthenticationBlocker],
        review_run_id: str | None = None,
        artifact_digest: str | None = None,
        seal: ResearchEvidenceSeal | None = None,
    ) -> EvidenceAuthenticationOutcome:
        resolved = set(blockers)
        if not resolved:
            # Defensive: a FAIL must always name a reason.
            resolved.add(EvidenceAuthenticationBlocker.AUTHENTICATION_MISSING)
        blockers_tuple = tuple(sorted(resolved, key=lambda item: item.value))
        result = EvidenceAuthenticationResult(
            authentication_id=stable_evidence_authentication_id(
                strategy_version_id=version.version_id,
                review_run_id=review_run_id,
                artifact_digest=artifact_digest,
                key_id=seal.key_id if seal else None,
                algorithm=seal.algorithm if seal else None,
                signature_digest=seal.signature_digest if seal else None,
                verdict=EvidenceAuthenticationVerdict.FAIL,
                blockers=blockers_tuple,
                policy_version=policy.policy_version,
                authenticator_version=policy.authenticator_version,
            ),
            strategy_version_id=version.version_id,
            review_run_id=review_run_id,
            artifact_digest=artifact_digest,
            key_id=seal.key_id if seal else None,
            algorithm=seal.algorithm if seal else None,
            signature_digest=seal.signature_digest if seal else None,
            verdict=EvidenceAuthenticationVerdict.FAIL,
            blockers=blockers_tuple,
            authenticator_version=policy.authenticator_version,
            policy_version=policy.policy_version,
            verified_at=verified_at,
        )
        return EvidenceAuthenticationOutcome(result=result, authenticated_evidence=None)

    def _pass(
        self,
        *,
        version: StrategyVersion,
        evidence: StrategyResearchEvidence,
        seal: ResearchEvidenceSeal,
        policy: EvidenceAuthenticationPolicy,
        verified_at: datetime,
        artifact_digest: str,
    ) -> EvidenceAuthenticationOutcome:
        result = EvidenceAuthenticationResult(
            authentication_id=stable_evidence_authentication_id(
                strategy_version_id=version.version_id,
                review_run_id=evidence.review_run_id,
                artifact_digest=artifact_digest,
                key_id=seal.key_id,
                algorithm=seal.algorithm,
                signature_digest=seal.signature_digest,
                verdict=EvidenceAuthenticationVerdict.PASS,
                blockers=(),
                policy_version=policy.policy_version,
                authenticator_version=policy.authenticator_version,
            ),
            strategy_version_id=version.version_id,
            review_run_id=evidence.review_run_id,
            artifact_digest=artifact_digest,
            key_id=seal.key_id,
            algorithm=seal.algorithm,
            signature_digest=seal.signature_digest,
            verdict=EvidenceAuthenticationVerdict.PASS,
            blockers=(),
            authenticator_version=policy.authenticator_version,
            policy_version=policy.policy_version,
            verified_at=verified_at,
        )
        authenticated = AuthenticatedStrategyResearchEvidence(
            evidence,
            result,
            seal,
            _authenticator_token=_evidence_auth._AUTHENTICATOR_TOKEN,
        )
        return EvidenceAuthenticationOutcome(
            result=result, authenticated_evidence=authenticated
        )


__all__ = [
    "EvidenceAuthenticationApplication",
    "EvidenceAuthenticationOutcome",
]
