"""Read-only access to the external verification trust root.

The trust root is a JSON file that lives *outside* the review artifact store
and outside the SQLite database, provisioned by the operator.  It carries
public keys and trust status only -- never a private key, never a shared
secret.  A colocated HMAC secret would not be an independent trust root: anyone
who can rewrite the artifact could rewrite the secret beside it.

The file is re-read on every lookup rather than cached, so revoking a key takes
effect on the next verification instead of at the next process restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from us_quant.trading.domain.evidence_auth import (
    EvidenceTrustStoreMalformed,
    EvidenceVerificationKey,
    trust_store_from_payload,
)
from us_quant.trading.ports.evidence_verification import EvidenceTrustRootUnavailable


class FileEvidenceVerificationKeySource:
    """Resolve verification keys from an operator-provisioned trust store file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def verification_key(self, key_id: str) -> EvidenceVerificationKey | None:
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError("key_id must be nonblank text")
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise EvidenceTrustRootUnavailable(
                f"trust root cannot be read: {self.path}"
            ) from error
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise EvidenceTrustRootUnavailable(
                "trust root is not valid JSON"
            ) from error
        try:
            keys = trust_store_from_payload(payload)
        except EvidenceTrustStoreMalformed as error:
            raise EvidenceTrustRootUnavailable(
                f"trust root is malformed: {error}"
            ) from error
        for key in keys:
            if key.key_id == key_id:
                return key
        return None


__all__ = ["FileEvidenceVerificationKeySource"]
