"""Detached authentication of research evidence against an external trust root.

Stage 6-A could prove a review artifact was structurally consistent with
itself.  It could not prove *who* produced it, and it had no way to bind
``universe_hash`` / ``code_hash`` -- fields the review artifact does not carry
-- to a governed :class:`StrategyVersion`.  This module closes that boundary
with a *detached* seal signed by a key the trading runtime never holds.

Three properties are load-bearing.

**The seal is detached.**  It lives beside the artifact rather than inside it,
and it signs the artifact's canonical payload digest.  Editing any semantic
field of the artifact -- ``passed``, ``observed``, ``generated_at``, a
parameter hash -- changes that digest and invalidates the seal.  A digest alone
would only prove *content identity*; the signature is what proves origin.

**The runtime verifies and never signs.**  Signing lives in research-side
tooling (``us_quant.research_evidence_sealing``), which the trading composition
must not import.  Nothing here constructs a signer or reads a private key.

**Unknown fails closed.**  A missing seal, an unknown key, a revoked key, an
unsupported algorithm or an unreadable trust root all produce a FAIL verdict
with an explicit blocker; none of them is treated as "probably fine".

A PASS here is still not lifecycle authority.  It answers exactly one question:
was this research evidence produced by a trusted identity?
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from us_quant.trading.domain.research_evidence import StrategyResearchEvidence
from us_quant.trading.domain.strategy import StrategyVersion


#: Bumped only when the signed field set changes; it is part of the signature.
EVIDENCE_SEAL_SCHEMA_VERSION = "research-evidence-seal-v1"

#: Format of the external trust root the runtime reads public keys from.
EVIDENCE_TRUST_STORE_SCHEMA_VERSION = "evidence-trust-store-v1"

ED25519_ALGORITHM = "ed25519"
SUPPORTED_SIGNATURE_ALGORITHMS: tuple[str, ...] = (ED25519_ALGORITHM,)

#: Convention for the detached seal that sits beside a review artifact.
DETACHED_SEAL_SUFFIX = ".seal.json"


class EvidenceAuthenticationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class EvidenceAuthenticationBlocker(StrEnum):
    """Every reason authentication can refuse.  Unknown always fails closed."""

    AUTHENTICATION_MISSING = "AUTHENTICATION_MISSING"
    TRUST_ROOT_UNAVAILABLE = "TRUST_ROOT_UNAVAILABLE"
    SIGNATURE_MALFORMED = "SIGNATURE_MALFORMED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    KEY_REVOKED = "KEY_REVOKED"
    UNSUPPORTED_ALGORITHM = "UNSUPPORTED_ALGORITHM"
    STRATEGY_IDENTITY_MISMATCH = "STRATEGY_IDENTITY_MISMATCH"
    PARAMETER_HASH_MISMATCH = "PARAMETER_HASH_MISMATCH"
    CODE_HASH_MISMATCH = "CODE_HASH_MISMATCH"
    UNIVERSE_HASH_MISMATCH = "UNIVERSE_HASH_MISMATCH"
    REVIEW_RUN_MISMATCH = "REVIEW_RUN_MISMATCH"
    DATA_HASH_MISMATCH = "DATA_HASH_MISMATCH"
    SIGNED_AT_INVALID = "SIGNED_AT_INVALID"


class EvidenceKeyTrustStatus(StrEnum):
    """Trust lifecycle of one verification key.

    ``ACTIVE`` may verify new evidence *and* is the only status the research
    sealing tool will sign with.  ``VERIFY_ONLY`` keeps already-issued evidence
    verifiable while forbidding new signatures -- that is what makes rotation
    possible without orphaning history.  ``REVOKED`` fails closed for every
    artifact that key ever signed.
    """

    ACTIVE = "ACTIVE"
    VERIFY_ONLY = "VERIFY_ONLY"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class EvidenceVerificationKey:
    """One public verification key and its trust status.

    Carries no private material and no signing capability, so it is safe to
    hand to the trading runtime.
    """

    key_id: str
    algorithm: str
    public_key: bytes
    trust_status: EvidenceKeyTrustStatus

    def __post_init__(self) -> None:
        _require_text(self.key_id, "key_id")
        _require_text(self.algorithm, "algorithm")
        if not isinstance(self.public_key, bytes) or not self.public_key:
            raise ValueError("public_key must be nonempty bytes")
        if not isinstance(self.trust_status, EvidenceKeyTrustStatus):
            raise TypeError("trust_status must be EvidenceKeyTrustStatus")

    @property
    def may_sign(self) -> bool:
        return self.trust_status is EvidenceKeyTrustStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class ResearchEvidenceSeal:
    """The detached, signed statement about one research artifact.

    ``signed_at`` is deliberately *not* required to be timezone-aware here.  A
    naive timestamp must be a verification failure with an explicit blocker,
    not a construction error, because the verifier's job is to report it rather
    than to refuse to look.
    """

    schema_version: str
    review_run_id: str
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    universe_hash: str
    code_hash: str
    symbol: str
    data_hash: str
    artifact_payload_sha256: str
    algorithm: str
    key_id: str
    signed_at: datetime
    signature: bytes

    def __post_init__(self) -> None:
        for name in (
            "schema_version", "review_run_id", "strategy_version_id",
            "strategy_semver", "parameter_hash", "universe_hash", "code_hash",
            "symbol", "data_hash", "artifact_payload_sha256", "algorithm",
            "key_id",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.signed_at, datetime):
            raise TypeError("signed_at must be a datetime")
        if not isinstance(self.signature, bytes) or not self.signature:
            raise ValueError("signature must be nonempty bytes")

    @property
    def signed_material(self) -> bytes:
        """The exact bytes the signature covers.  Never includes ``signature``."""

        return canonical_signed_material(self)

    @property
    def signature_digest(self) -> str:
        return sha256(self.signature).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceAuthenticationPolicy:
    authenticator_version: str = "evidence-authentication-v1"
    policy_version: str = "evidence-trust-v1"
    supported_algorithms: tuple[str, ...] = SUPPORTED_SIGNATURE_ALGORITHMS
    maximum_clock_skew: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        _require_text(self.authenticator_version, "authenticator_version")
        _require_text(self.policy_version, "policy_version")
        if not isinstance(self.supported_algorithms, tuple) or not self.supported_algorithms:
            raise ValueError("supported_algorithms must be a nonempty tuple")
        for algorithm in self.supported_algorithms:
            _require_text(algorithm, "supported algorithm")
        if not isinstance(self.maximum_clock_skew, timedelta):
            raise TypeError("maximum_clock_skew must be a timedelta")
        if self.maximum_clock_skew < timedelta(0):
            raise ValueError("maximum_clock_skew must not be negative")


@dataclass(frozen=True, slots=True)
class EvidenceAuthenticationResult:
    """The durable record of one verification attempt.

    A PASS requires a fully identified artifact; a FAIL is allowed to carry
    ``None`` wherever the input never got far enough to produce a value.
    """

    authentication_id: str
    strategy_version_id: str
    review_run_id: str | None
    artifact_digest: str | None
    key_id: str | None
    algorithm: str | None
    signature_digest: str | None
    verdict: EvidenceAuthenticationVerdict
    blockers: tuple[EvidenceAuthenticationBlocker, ...]
    authenticator_version: str
    policy_version: str
    verified_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "authentication_id", "strategy_version_id",
            "authenticator_version", "policy_version",
        ):
            _require_text(getattr(self, name), name)
        for name in (
            "review_run_id", "artifact_digest", "key_id", "algorithm",
            "signature_digest",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        if not isinstance(self.verdict, EvidenceAuthenticationVerdict):
            raise TypeError("verdict must be EvidenceAuthenticationVerdict")
        if not isinstance(self.blockers, tuple):
            raise TypeError("blockers must be a tuple")
        if any(not isinstance(item, EvidenceAuthenticationBlocker) for item in self.blockers):
            raise TypeError("blockers must contain EvidenceAuthenticationBlocker values")
        object.__setattr__(
            self, "blockers",
            tuple(sorted(set(self.blockers), key=lambda item: item.value)),
        )
        if (self.verdict is EvidenceAuthenticationVerdict.PASS) != (not self.blockers):
            raise ValueError("PASS requires no blockers and FAIL requires blockers")
        if self.verdict is EvidenceAuthenticationVerdict.PASS:
            for name in (
                "review_run_id", "artifact_digest", "key_id", "algorithm",
                "signature_digest",
            ):
                if getattr(self, name) is None:
                    raise ValueError(f"PASS requires {name}")
        _require_aware(self.verified_at, "verified_at")


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedStrategyResearchEvidence:
    """Evidence that passed authentication, carrying the now-bound hashes.

    ``universe_hash`` and ``code_hash`` come from the signed seal, which is the
    only reason Stage 6-B1 can bind them to a governed version at all: the
    review artifact itself does not carry them.

    Construction is gated on a private token so that only the authenticator can
    mint this type.  That is what makes "only authenticated evidence may reach
    lifecycle authority" a structural property rather than a convention.
    """

    evidence: StrategyResearchEvidence
    authentication: EvidenceAuthenticationResult
    seal: ResearchEvidenceSeal
    universe_hash: str
    code_hash: str

    def __init__(
        self,
        evidence: StrategyResearchEvidence,
        authentication: EvidenceAuthenticationResult,
        seal: ResearchEvidenceSeal,
        *,
        _authenticator_token: object,
    ) -> None:
        if _authenticator_token is not _AUTHENTICATOR_TOKEN:
            raise TypeError(
                "AuthenticatedStrategyResearchEvidence must come from the authenticator"
            )
        if not isinstance(evidence, StrategyResearchEvidence):
            raise TypeError("evidence must be StrategyResearchEvidence")
        if not isinstance(authentication, EvidenceAuthenticationResult):
            raise TypeError("authentication must be EvidenceAuthenticationResult")
        if not isinstance(seal, ResearchEvidenceSeal):
            raise TypeError("seal must be ResearchEvidenceSeal")
        if authentication.verdict is not EvidenceAuthenticationVerdict.PASS:
            raise ValueError("authenticated evidence requires a PASS verdict")
        if seal.review_run_id != evidence.review_run_id:
            raise ValueError("seal and evidence must describe the same review")
        if seal.artifact_payload_sha256 != authentication.artifact_digest:
            raise ValueError("seal digest must match the authentication result")
        if seal.key_id != authentication.key_id:
            raise ValueError("seal key must match the authentication result")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "authentication", authentication)
        object.__setattr__(self, "seal", seal)
        object.__setattr__(self, "universe_hash", seal.universe_hash)
        object.__setattr__(self, "code_hash", seal.code_hash)

    @property
    def review_run_id(self) -> str:
        return self.evidence.review_run_id

    @property
    def strategy_version_id(self) -> str:
        return self.evidence.identity.strategy_version_id

    @property
    def strategy_semver(self) -> str:
        return self.evidence.identity.strategy_semver

    @property
    def parameter_hash(self) -> str:
        return self.evidence.identity.parameter_hash

    @property
    def symbol(self) -> str:
        return self.evidence.identity.symbol

    @property
    def data_hash(self) -> str:
        return self.evidence.identity.data_hash

    @property
    def key_id(self) -> str:
        return self.seal.key_id

    @property
    def signed_at(self) -> datetime:
        return self.seal.signed_at

    @property
    def artifact_digest(self) -> str:
        return self.seal.artifact_payload_sha256


class EvidenceSealMalformed(ValueError):
    """A detached seal exists but cannot be read as a seal."""


class EvidenceTrustStoreMalformed(ValueError):
    """The external trust root exists but cannot be read as a trust store."""


_AUTHENTICATOR_TOKEN = object()


def trust_store_to_payload(keys: tuple[EvidenceVerificationKey, ...]) -> dict[str, Any]:
    """Serialise a trust store.  Public material only -- never a private key."""

    if not isinstance(keys, tuple):
        raise TypeError("keys must be a tuple")
    seen: set[str] = set()
    entries = []
    for key in keys:
        if not isinstance(key, EvidenceVerificationKey):
            raise TypeError("keys must contain EvidenceVerificationKey values")
        if key.key_id in seen:
            raise ValueError(f"duplicate key_id in trust store: {key.key_id}")
        seen.add(key.key_id)
        entries.append(
            {
                "key_id": key.key_id,
                "algorithm": key.algorithm,
                "public_key": b64encode(key.public_key).decode("ascii"),
                "trust_status": key.trust_status.value,
            }
        )
    entries.sort(key=lambda entry: entry["key_id"])
    return {"schema_version": EVIDENCE_TRUST_STORE_SCHEMA_VERSION, "keys": entries}


def trust_store_from_payload(payload: Mapping[str, Any]) -> tuple[EvidenceVerificationKey, ...]:
    """Parse a trust store, raising on anything that is not exactly well formed."""

    if not isinstance(payload, Mapping):
        raise EvidenceTrustStoreMalformed("trust store must be an object")
    row = dict(payload)
    if row.get("schema_version") != EVIDENCE_TRUST_STORE_SCHEMA_VERSION:
        raise EvidenceTrustStoreMalformed("trust store schema_version is unsupported")
    entries = row.get("keys")
    if not isinstance(entries, list):
        raise EvidenceTrustStoreMalformed("trust store keys must be a list")
    keys: list[EvidenceVerificationKey] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise EvidenceTrustStoreMalformed("trust store entry must be an object")
        item = dict(entry)
        key_id = item.get("key_id")
        algorithm = item.get("algorithm")
        public_key_text = item.get("public_key")
        status_text = item.get("trust_status")
        if not isinstance(key_id, str) or not key_id.strip():
            raise EvidenceTrustStoreMalformed("trust store entry key_id is invalid")
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise EvidenceTrustStoreMalformed("trust store entry algorithm is invalid")
        if not isinstance(public_key_text, str) or not public_key_text.strip():
            raise EvidenceTrustStoreMalformed("trust store entry public_key is invalid")
        try:
            trust_status = EvidenceKeyTrustStatus(status_text)
        except (TypeError, ValueError) as error:
            raise EvidenceTrustStoreMalformed(
                "trust store entry trust_status is invalid"
            ) from error
        try:
            public_key = b64decode(public_key_text, validate=True)
        except (TypeError, ValueError) as error:
            raise EvidenceTrustStoreMalformed(
                "trust store entry public_key is not valid base64"
            ) from error
        if key_id in seen:
            raise EvidenceTrustStoreMalformed(f"duplicate key_id in trust store: {key_id}")
        seen.add(key_id)
        keys.append(
            EvidenceVerificationKey(
                key_id=key_id,
                algorithm=algorithm,
                public_key=public_key,
                trust_status=trust_status,
            )
        )
    return tuple(sorted(keys, key=lambda key: key.key_id))


def _seal_common_fields(seal: ResearchEvidenceSeal) -> dict[str, Any]:
    """The fields a seal carries, defined exactly once.

    Both the signed material and the stored form are built from this.  If the
    two were written out separately they could drift, and drift here is silent
    and severe: the research signer and the runtime verifier would disagree
    about what was signed, so every seal would fail -- or, worse, a field added
    to the file but not to the signature would become tamperable.
    """

    return {
        "schema_version": seal.schema_version,
        "review_run_id": seal.review_run_id,
        "strategy_version_id": seal.strategy_version_id,
        "strategy_semver": seal.strategy_semver,
        "parameter_hash": seal.parameter_hash,
        "universe_hash": seal.universe_hash,
        "code_hash": seal.code_hash,
        "symbol": seal.symbol,
        "data_hash": seal.data_hash,
        "artifact_payload_sha256": seal.artifact_payload_sha256,
        "algorithm": seal.algorithm,
        "key_id": seal.key_id,
        "signed_at": canonical_timestamp(seal.signed_at),
    }


def canonical_signed_material(seal: ResearchEvidenceSeal) -> bytes:
    """The single canonical byte string a seal signature covers.

    Sort keys, no insignificant whitespace, no ASCII escaping -- and the
    ``signature`` field is absent, because a signature cannot cover itself.
    Both the research-side signer and the runtime verifier call *this*
    function, which is what keeps the two sides in agreement.
    """

    return _canonical_json(_seal_common_fields(seal)).encode("utf-8")


def canonical_artifact_payload_digest(payload: Mapping[str, Any]) -> str:
    """The canonical digest of a review artifact's parsed payload.

    Taken over the canonical form rather than the raw bytes on purpose: the
    pretty-printed formatting of a JSON file is not evidence, and binding it
    would make a harmless re-indent look like tampering.  Every *semantic*
    change still moves the digest.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return sha256(_canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def canonical_timestamp(value: datetime) -> str:
    """Deterministic text for one instant.

    A naive value is rendered as-is instead of being assigned a timezone, so
    that signature verification stays deterministic and the *policy* layer is
    the thing that rejects it.
    """

    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.isoformat()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def default_seal_path(artifact_path: str | Path) -> Path:
    """The conventional detached seal location for one review artifact."""

    path = Path(artifact_path)
    return path.with_name(path.stem + DETACHED_SEAL_SUFFIX)


def seal_to_payload(seal: ResearchEvidenceSeal) -> dict[str, Any]:
    """Serialise a seal for storage as a detached file.

    Identical to the signed material plus the signature itself, so what is
    written and what is covered cannot disagree.
    """

    if not isinstance(seal, ResearchEvidenceSeal):
        raise TypeError("seal must be ResearchEvidenceSeal")
    return {
        **_seal_common_fields(seal),
        "signature": b64encode(seal.signature).decode("ascii"),
    }


def seal_from_payload(payload: Mapping[str, Any]) -> ResearchEvidenceSeal:
    """Parse a detached seal, raising :class:`EvidenceSealMalformed` on refuse."""

    if not isinstance(payload, Mapping):
        raise EvidenceSealMalformed("seal payload must be an object")
    row = dict(payload)
    schema_version = row.get("schema_version")
    if schema_version != EVIDENCE_SEAL_SCHEMA_VERSION:
        raise EvidenceSealMalformed("seal schema_version is unsupported")
    values: dict[str, str] = {}
    for key in (
        "review_run_id", "strategy_version_id", "strategy_semver",
        "parameter_hash", "universe_hash", "code_hash", "symbol", "data_hash",
        "artifact_payload_sha256", "algorithm", "key_id", "signed_at",
    ):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise EvidenceSealMalformed(f"seal {key} is missing or invalid")
        values[key] = value
    signature_text = row.get("signature")
    if not isinstance(signature_text, str) or not signature_text.strip():
        raise EvidenceSealMalformed("seal signature is missing")
    try:
        signature = b64decode(signature_text, validate=True)
    except (ValueError, TypeError) as error:
        raise EvidenceSealMalformed("seal signature is not valid base64") from error
    try:
        signed_at = datetime.fromisoformat(values["signed_at"])
    except ValueError as error:
        raise EvidenceSealMalformed("seal signed_at is not a timestamp") from error
    try:
        return ResearchEvidenceSeal(
            schema_version=schema_version,
            review_run_id=values["review_run_id"],
            strategy_version_id=values["strategy_version_id"],
            strategy_semver=values["strategy_semver"],
            parameter_hash=values["parameter_hash"],
            universe_hash=values["universe_hash"],
            code_hash=values["code_hash"],
            symbol=values["symbol"],
            data_hash=values["data_hash"],
            artifact_payload_sha256=values["artifact_payload_sha256"],
            algorithm=values["algorithm"],
            key_id=values["key_id"],
            signed_at=signed_at,
            signature=signature,
        )
    except (TypeError, ValueError) as error:
        raise EvidenceSealMalformed(f"seal is invalid: {error}") from error


def stable_evidence_authentication_id(
    *,
    strategy_version_id: str,
    review_run_id: str | None,
    artifact_digest: str | None,
    key_id: str | None,
    algorithm: str | None,
    signature_digest: str | None,
    verdict: EvidenceAuthenticationVerdict,
    blockers: tuple[EvidenceAuthenticationBlocker, ...],
    policy_version: str,
    authenticator_version: str,
) -> str:
    """Deterministic identity of one *semantic* verification outcome.

    ``verified_at`` is excluded, so re-verifying the same evidence is
    idempotent.  A revocation that flips the verdict produces a different id,
    which is what lets the earlier PASS row survive as audit history instead of
    being overwritten.
    """

    material = {
        "strategy_version_id": strategy_version_id,
        "review_run_id": review_run_id,
        "artifact_digest": artifact_digest,
        "key_id": key_id,
        "algorithm": algorithm,
        "signature_digest": signature_digest,
        "verdict": verdict.value,
        "blockers": sorted({item.value for item in blockers}),
        "policy_version": policy_version,
        "authenticator_version": authenticator_version,
    }
    digest = sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"sea-{digest}"


def version_binding_blockers(
    *,
    seal: ResearchEvidenceSeal,
    version: StrategyVersion,
) -> set[EvidenceAuthenticationBlocker]:
    """Bind the sealed hashes to the governed version they claim to describe."""

    blockers: set[EvidenceAuthenticationBlocker] = set()
    if (
        seal.strategy_version_id != version.version_id
        or seal.strategy_semver != version.semver
    ):
        blockers.add(EvidenceAuthenticationBlocker.STRATEGY_IDENTITY_MISMATCH)
    if seal.parameter_hash != version.parameter_hash:
        blockers.add(EvidenceAuthenticationBlocker.PARAMETER_HASH_MISMATCH)
    if seal.code_hash != version.code_hash:
        blockers.add(EvidenceAuthenticationBlocker.CODE_HASH_MISMATCH)
    if seal.universe_hash != version.universe_hash:
        blockers.add(EvidenceAuthenticationBlocker.UNIVERSE_HASH_MISMATCH)
    return blockers


def artifact_binding_blockers(
    *,
    seal: ResearchEvidenceSeal,
    evidence: StrategyResearchEvidence,
    artifact_digest: str,
) -> set[EvidenceAuthenticationBlocker]:
    """Bind the seal to the exact artifact it claims to have signed."""

    blockers: set[EvidenceAuthenticationBlocker] = set()
    identity = evidence.identity
    if seal.artifact_payload_sha256 != artifact_digest:
        blockers.add(EvidenceAuthenticationBlocker.ARTIFACT_DIGEST_MISMATCH)
    if seal.review_run_id != evidence.review_run_id:
        blockers.add(EvidenceAuthenticationBlocker.REVIEW_RUN_MISMATCH)
    if (
        seal.strategy_version_id != identity.strategy_version_id
        or seal.strategy_semver != identity.strategy_semver
        or seal.symbol != identity.symbol
    ):
        blockers.add(EvidenceAuthenticationBlocker.STRATEGY_IDENTITY_MISMATCH)
    if seal.parameter_hash != identity.parameter_hash:
        blockers.add(EvidenceAuthenticationBlocker.PARAMETER_HASH_MISMATCH)
    if seal.data_hash != identity.data_hash:
        blockers.add(EvidenceAuthenticationBlocker.DATA_HASH_MISMATCH)
    return blockers


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "AuthenticatedStrategyResearchEvidence",
    "DETACHED_SEAL_SUFFIX",
    "ED25519_ALGORITHM",
    "EVIDENCE_SEAL_SCHEMA_VERSION",
    "EVIDENCE_TRUST_STORE_SCHEMA_VERSION",
    "EvidenceAuthenticationBlocker",
    "EvidenceAuthenticationPolicy",
    "EvidenceAuthenticationResult",
    "EvidenceAuthenticationVerdict",
    "EvidenceKeyTrustStatus",
    "EvidenceSealMalformed",
    "EvidenceTrustStoreMalformed",
    "EvidenceVerificationKey",
    "ResearchEvidenceSeal",
    "SUPPORTED_SIGNATURE_ALGORITHMS",
    "artifact_binding_blockers",
    "canonical_artifact_payload_digest",
    "canonical_signed_material",
    "canonical_timestamp",
    "default_seal_path",
    "seal_from_payload",
    "seal_to_payload",
    "stable_evidence_authentication_id",
    "trust_store_from_payload",
    "trust_store_to_payload",
    "version_binding_blockers",
]
