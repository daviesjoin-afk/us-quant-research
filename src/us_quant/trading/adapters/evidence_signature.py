"""Ed25519 signature verification for detached research-evidence seals.

The only place in the trading runtime that touches a cryptography primitive.
It verifies and nothing else: there is no signing function here, no private-key
type is imported, and no code path accepts private key material.

The dependency is pinned in ``pyproject.toml`` on purpose.  Hand-rolling
Ed25519 to avoid a dependency would be exactly the kind of self-made crypto
that turns a trust boundary into a liability.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from us_quant.trading.domain.evidence_auth import ED25519_ALGORITHM
from us_quant.trading.ports.evidence_verification import (
    MalformedEvidenceSignature,
    UnsupportedEvidenceAlgorithm,
)


#: An Ed25519 signature is exactly 64 bytes and a public key exactly 32.
_ED25519_SIGNATURE_BYTES = 64


class Ed25519EvidenceSignatureVerifier:
    """Verify Ed25519 signatures over canonical signed material."""

    algorithm = ED25519_ALGORITHM

    def verify(
        self,
        *,
        algorithm: str,
        public_key: bytes,
        message: bytes,
        signature: bytes,
    ) -> bool:
        if algorithm != self.algorithm:
            raise UnsupportedEvidenceAlgorithm(algorithm)
        if not isinstance(public_key, bytes) or not public_key:
            raise MalformedEvidenceSignature("public key must be nonempty bytes")
        if not isinstance(signature, bytes):
            raise MalformedEvidenceSignature("signature must be bytes")
        if len(signature) != _ED25519_SIGNATURE_BYTES:
            raise MalformedEvidenceSignature(
                "ed25519 signature must be exactly 64 bytes"
            )
        try:
            key = Ed25519PublicKey.from_public_bytes(public_key)
        except (TypeError, ValueError) as error:
            raise MalformedEvidenceSignature(
                "public key is not a valid ed25519 key"
            ) from error
        try:
            key.verify(signature, message)
        except InvalidSignature:
            return False
        return True


__all__ = ["Ed25519EvidenceSignatureVerifier"]
