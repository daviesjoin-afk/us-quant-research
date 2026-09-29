"""Research-side sealing tool: signs a TargetedReview artifact.  Never runtime.

This module is the *only* place that touches a private key.  It lives outside
``us_quant.trading`` on purpose, and the Stage 6-B1 architecture guard asserts
that no risk, execution, portfolio, dispatch, broker or desktop module imports
it.  A trading composition that could sign would be able to mint the evidence
it is supposed to be verifying, which would make the whole trust boundary
decorative.

The signer is also not the verifier's counterpart by accident: it refuses to
sign with a key the trust root does not list as ``ACTIVE``, and it refuses to
sign with a private key that sits inside the artifact store or inside a git
working tree.  Those are the two places a private key must never be.

Private keys are stored as base64 text (a single line, optional trailing
newline).  Public keys and trust stores are JSON.
"""

from __future__ import annotations

import argparse
from base64 import b64decode, b64encode
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from us_quant.trading.adapters.research_evidence import load_targeted_review_artifact
from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EVIDENCE_SEAL_SCHEMA_VERSION,
    EvidenceKeyTrustStatus,
    EvidenceTrustStoreMalformed,
    ResearchEvidenceSeal,
    canonical_artifact_payload_digest,
    canonical_signed_material,
    default_seal_path,
    seal_to_payload,
    trust_store_from_payload,
    trust_store_to_payload,
)


class EvidenceSealingError(RuntimeError):
    """The artifact cannot be sealed as requested."""


def generate_signing_key() -> tuple[bytes, bytes]:
    """Return ``(private_key_bytes, public_key_bytes)`` for a fresh Ed25519 key."""

    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private_bytes, public_bytes


def load_private_key(path: str | Path) -> tuple[Ed25519PrivateKey, bytes]:
    """Read a base64 private key file and return the key and its public bytes."""

    key_path = Path(path)
    try:
        text = key_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise EvidenceSealingError(f"private key cannot be read: {key_path}") from error
    try:
        raw = b64decode(text, validate=True)
    except (TypeError, ValueError) as error:
        raise EvidenceSealingError("private key is not valid base64") from error
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    except (TypeError, ValueError) as error:
        raise EvidenceSealingError("private key is not a valid ed25519 key") from error
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private_key, public_bytes


def read_trust_store(path: str | Path) -> tuple:
    """Read and validate the operator trust store."""

    store_path = Path(path)
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise EvidenceSealingError(f"trust store cannot be read: {store_path}") from error
    except (TypeError, ValueError) as error:
        raise EvidenceSealingError("trust store is not valid JSON") from error
    try:
        return trust_store_from_payload(payload)
    except EvidenceTrustStoreMalformed as error:
        raise EvidenceSealingError(f"trust store is malformed: {error}") from error


def assert_private_key_is_outside(
    path: str | Path, *, artifact_path: str | Path | None = None
) -> None:
    """Refuse a private key inside a git tree, or inside the artifact store.

    Two separate rules, both about where a private key must never be.  The git
    rule always applies; the artifact-store rule applies when sealing a
    specific artifact, because a key sitting next to the evidence it signs is
    not an independent trust root.
    """

    key_path = Path(path).resolve()
    for candidate in (key_path, *key_path.parents):
        if (candidate / ".git").exists():
            raise EvidenceSealingError(
                "private key must not live inside a git working tree"
            )
    if artifact_path is not None:
        artifact_dir = Path(artifact_path).resolve().parent
        if key_path == artifact_dir or artifact_dir in key_path.parents:
            raise EvidenceSealingError(
                "private key must not live inside the research artifact store"
            )


def seal_review_artifact(
    *,
    artifact_path: str | Path,
    private_key_path: str | Path,
    trust_store_path: str | Path,
    key_id: str,
    universe_hash: str,
    code_hash: str,
    seal_path: str | Path | None = None,
    signed_at: datetime | None = None,
) -> tuple[ResearchEvidenceSeal, Path]:
    """Produce a detached seal for one review artifact.

    ``universe_hash`` and ``code_hash`` are explicit inputs because the review
    artifact does not carry them.  The signing authority is *asserting* which
    universe and which code revision produced this evidence; the runtime later
    binds that assertion to the governed ``StrategyVersion`` and fails closed if
    the two disagree.  That binding is the whole reason the seal exists.

    Returns the seal and the path it was written to.  The artifact itself is
    never modified -- a seal that rewrote its own subject would not be detached.
    """

    if not isinstance(key_id, str) or not key_id.strip():
        raise EvidenceSealingError("key_id must be nonblank text")
    for name, value in (("universe_hash", universe_hash), ("code_hash", code_hash)):
        if not isinstance(value, str) or not value.strip():
            raise EvidenceSealingError(f"{name} must be nonblank text")

    artifact_file = Path(artifact_path)
    try:
        artifact = load_targeted_review_artifact(artifact_file)
    except Exception as error:  # noqa: BLE001 - any load failure refuses to seal
        raise EvidenceSealingError(
            f"artifact cannot be sealed: {artifact_file}"
        ) from error
    result = artifact.result

    assert_private_key_is_outside(private_key_path, artifact_path=artifact_file)
    private_key, public_bytes = load_private_key(private_key_path)

    keys = read_trust_store(trust_store_path)
    matching = next((key for key in keys if key.key_id == key_id), None)
    if matching is None:
        raise EvidenceSealingError(f"trust store does not list key {key_id}")
    if matching.trust_status is not EvidenceKeyTrustStatus.ACTIVE:
        raise EvidenceSealingError(
            f"key {key_id} is {matching.trust_status.value} and may not sign new evidence"
        )
    if matching.algorithm != ED25519_ALGORITHM:
        raise EvidenceSealingError(
            f"key {key_id} algorithm {matching.algorithm} is not supported for signing"
        )
    if matching.public_key != public_bytes:
        raise EvidenceSealingError(
            f"private key does not correspond to the public key registered for {key_id}"
        )

    moment = signed_at if signed_at is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise EvidenceSealingError("signed_at must be timezone-aware")

    unsigned = ResearchEvidenceSeal(
        schema_version=EVIDENCE_SEAL_SCHEMA_VERSION,
        review_run_id=result.run_id,
        strategy_version_id=result.strategy_version_id,
        strategy_semver=result.strategy_semver,
        parameter_hash=result.base_parameter_hash,
        universe_hash=universe_hash,
        code_hash=code_hash,
        symbol=result.symbol,
        data_hash=result.data_hash,
        artifact_payload_sha256=canonical_artifact_payload_digest(artifact.payload),
        algorithm=ED25519_ALGORITHM,
        key_id=key_id,
        signed_at=moment,
        signature=b"\x00" * 64,
    )
    signature = private_key.sign(canonical_signed_material(unsigned))
    seal = ResearchEvidenceSeal(
        schema_version=unsigned.schema_version,
        review_run_id=unsigned.review_run_id,
        strategy_version_id=unsigned.strategy_version_id,
        strategy_semver=unsigned.strategy_semver,
        parameter_hash=unsigned.parameter_hash,
        universe_hash=unsigned.universe_hash,
        code_hash=unsigned.code_hash,
        symbol=unsigned.symbol,
        data_hash=unsigned.data_hash,
        artifact_payload_sha256=unsigned.artifact_payload_sha256,
        algorithm=unsigned.algorithm,
        key_id=unsigned.key_id,
        signed_at=unsigned.signed_at,
        signature=signature,
    )

    destination = (
        Path(seal_path) if seal_path is not None else default_seal_path(artifact_file)
    )
    try:
        destination.write_text(
            json.dumps(seal_to_payload(seal), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeError) as error:
        raise EvidenceSealingError(f"seal cannot be written: {destination}") from error
    return seal, destination


def write_signing_key(path: str | Path) -> bytes:
    """Generate a key, write the private half, and return the public half.

    The git-tree check is applied here too: generating a fresh private key
    straight into a checkout is the mistake this tool exists to prevent.
    """

    destination = Path(path)
    assert_private_key_is_outside(destination)
    private_bytes, public_bytes = generate_signing_key()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            b64encode(private_bytes).decode("ascii") + "\n", encoding="utf-8"
        )
    except (OSError, UnicodeError) as error:
        raise EvidenceSealingError(f"private key cannot be written: {destination}") from error
    return public_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="us-quant-evidence-sealing",
        description="Sign TargetedReview artifacts with a detached evidence seal.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate-key", help="create an Ed25519 signing key and print its trust entry"
    )
    generate.add_argument("--private-key", required=True)
    generate.add_argument("--key-id", required=True)
    generate.add_argument("--trust-store", required=False)

    seal = subparsers.add_parser("seal", help="write a detached seal for one artifact")
    seal.add_argument("--artifact", required=True)
    seal.add_argument("--private-key", required=True)
    seal.add_argument("--trust-store", required=True)
    seal.add_argument("--key-id", required=True)
    seal.add_argument(
        "--universe-hash",
        required=True,
        help="the universe the evidence was produced under; asserted and signed",
    )
    seal.add_argument(
        "--code-hash",
        required=True,
        help="the code revision the evidence was produced under; asserted and signed",
    )
    seal.add_argument("--out", required=False, default=None)
    seal.add_argument("--signed-at", required=False, default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "generate-key":
            public_bytes = write_signing_key(args.private_key)
            entry = {
                "key_id": args.key_id,
                "algorithm": ED25519_ALGORITHM,
                "public_key": b64encode(public_bytes).decode("ascii"),
                "trust_status": EvidenceKeyTrustStatus.ACTIVE.value,
            }
            if args.trust_store:
                store_path = Path(args.trust_store)
                existing: tuple = ()
                if store_path.exists():
                    existing = read_trust_store(store_path)
                keys = tuple(key for key in existing if key.key_id != args.key_id)
                from us_quant.trading.domain.evidence_auth import EvidenceVerificationKey

                merged = keys + (
                    EvidenceVerificationKey(
                        key_id=args.key_id,
                        algorithm=ED25519_ALGORITHM,
                        public_key=public_bytes,
                        trust_status=EvidenceKeyTrustStatus.ACTIVE,
                    ),
                )
                store_path.parent.mkdir(parents=True, exist_ok=True)
                store_path.write_text(
                    json.dumps(trust_store_to_payload(merged), indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
            print(json.dumps(entry, sort_keys=True))
            return 0

        signed_at = None
        if args.signed_at:
            signed_at = datetime.fromisoformat(args.signed_at)
        seal_value, destination = seal_review_artifact(
            artifact_path=args.artifact,
            private_key_path=args.private_key,
            trust_store_path=args.trust_store,
            key_id=args.key_id,
            universe_hash=args.universe_hash,
            code_hash=args.code_hash,
            seal_path=args.out,
            signed_at=signed_at,
        )
        print(
            json.dumps(
                {
                    "seal_path": str(destination),
                    "review_run_id": seal_value.review_run_id,
                    "artifact_payload_sha256": seal_value.artifact_payload_sha256,
                    "key_id": seal_value.key_id,
                },
                sort_keys=True,
            )
        )
        return 0
    except EvidenceSealingError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EvidenceSealingError",
    "assert_private_key_is_outside",
    "generate_signing_key",
    "load_private_key",
    "main",
    "read_trust_store",
    "seal_review_artifact",
    "write_signing_key",
]
