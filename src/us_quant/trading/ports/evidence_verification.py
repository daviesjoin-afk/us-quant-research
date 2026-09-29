"""Verification-side ports for research evidence authentication.

The trading runtime is a *verifier only*.  These ports express exactly what it
needs to verify a detached seal -- a public key lookup and a signature check --
and nothing that could produce a signature.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.evidence_auth import EvidenceVerificationKey


class EvidenceTrustRootUnavailable(RuntimeError):
    """The external trust root cannot be read; verification must fail closed."""


class UnsupportedEvidenceAlgorithm(RuntimeError):
    """The requested signature algorithm has no implementation here."""


class MalformedEvidenceSignature(RuntimeError):
    """The signature bytes are structurally wrong for the algorithm."""


class EvidenceVerificationKeySource(Protocol):
    """Resolve a ``key_id`` to public verification material.

    Implementations must return ``None`` for a key that is simply *unknown*,
    and raise :class:`EvidenceTrustRootUnavailable` when the trust root itself
    cannot be consulted.  Those two cases carry different blockers, so
    collapsing them would lose the distinction between "no such key" and "the
    trust root is broken".
    """

    def verification_key(self, key_id: str) -> EvidenceVerificationKey | None:
        """Return the key, ``None`` when unknown, or raise when unreadable."""


class EvidenceSignatureVerifier(Protocol):
    """Check a detached signature.  Never signs and never holds a private key."""

    def verify(
        self,
        *,
        algorithm: str,
        public_key: bytes,
        message: bytes,
        signature: bytes,
    ) -> bool:
        """Return True only for a well-formed signature over ``message``.

        Raises :class:`UnsupportedEvidenceAlgorithm` for an algorithm this
        implementation does not provide, and
        :class:`MalformedEvidenceSignature` when the key or signature bytes are
        the wrong shape.  A structurally valid but wrong signature returns
        False.
        """


__all__ = [
    "EvidenceSignatureVerifier",
    "EvidenceTrustRootUnavailable",
    "EvidenceVerificationKeySource",
    "MalformedEvidenceSignature",
    "UnsupportedEvidenceAlgorithm",
]
