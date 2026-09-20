"""Proofs that a halted Paper session is safe to resume, and finished when it ends.

Everything here is a pure function of broker and session facts: it builds
evidence, checks the identity and age of evidence, and compares a captured proof
with a fresher one.  It never submits, cancels, connects, resumes or changes a
workflow phase -- a proof is data a human presents, not an action this module
takes.

The identity fields are not decoration.  A resume is only as trustworthy as the
statement "this is the same runtime, the same service, the same session, the same
account binding and the same broker state a human just looked at", so every one
of them is carried in the evidence and compared again after the second broker
refresh rather than trusted from the first capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Mapping
from uuid import uuid4

from us_quant.trading.runtime.paper_contracts import (
    EngineSnapshot,
    PaperHealth,
)

#: How long a captured reconciliation proof stays valid.  Short on purpose: a
#: proof describes one broker state, and a human who needs longer must look
#: again instead of resuming against a snapshot that has since moved.
EVIDENCE_TTL_SECONDS = 30


class ReconciliationEvidenceError(RuntimeError):
    """A proof was missing, stale, expired, already used or issued elsewhere."""


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """The runtime, service and session a proof is issued for."""

    runtime_id: str
    service_identity: int
    session_id: str | None


@dataclass(frozen=True, slots=True)
class CoordinatorReconciliationEvidence:
    """Proof that a halted session was healthy and current at capture time."""

    evidence_id: str
    runtime_id: str
    service_identity: int
    session_id: str
    account_fingerprint: str
    armed_account_fingerprint: str
    connection_generation: int
    reconciliation_generation: int
    state_version: int
    captured_at: str
    expires_at: str
    broker_digest: str
    engine_digest: str
    health_status: str
    safe_to_continue: bool


@dataclass(frozen=True, slots=True)
class CoordinatorFinalizationEvidence:
    """Proof of the broker zero-state captured before the service disconnects."""

    evidence_id: str
    runtime_id: str
    service_identity: int
    session_id: str
    connection_generation: int
    captured_at: str
    broker_digest: str
    engine_digest: str


class OneShotEvidence:
    """Remembers consumed evidence ids so a proof is usable at most once.

    Consumption happens before the second broker refresh, so an ambiguous
    confirmation costs a fresh human reconciliation instead of a second resume.
    """

    def __init__(self) -> None:
        self._consumed: set[str] = set()

    def consume(self, evidence_id: str, *, label: str) -> None:
        if evidence_id in self._consumed:
            raise ReconciliationEvidenceError(
                f"{label} evidence was already consumed."
            )
        self._consumed.add(evidence_id)


def utc(value: datetime) -> datetime:
    """Read a naive timestamp as UTC and normalise an aware one."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def row_value(row: object, key: str, default: object) -> object:
    """Read one field from a dataclass-like row or a mapping."""

    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def engine_snapshot_digest(snapshot: EngineSnapshot) -> str:
    """Hash exact local execution state without relying on display counts."""

    positions = [
        {
            "symbol": str(row_value(row, "symbol", "")),
            "quantity": str(row_value(row, "quantity", "")),
            "average_price": str(
                row_value(row, "average_price", row_value(row, "average_cost", ""))
            ),
        }
        for row in snapshot.positions
    ]
    pending = [
        {
            "order_id": str(row_value(row, "order_id", "")),
            "symbol": str(row_value(row, "execution_symbol", "")),
            "side": str(row_value(row, "side", "")),
            "quantity": str(row_value(row, "quantity", "")),
            "limit_price": str(row_value(row, "limit_price", "")),
        }
        for row in snapshot.pending_orders
    ]
    payload = {
        "session_id": snapshot.session_id,
        "positions": sorted(positions, key=lambda row: (row["symbol"], row["quantity"])),
        "pending_orders": sorted(
            pending, key=lambda row: (row["order_id"], row["symbol"])
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def proof_is_safe_and_current(
    broker_snapshot: object,
    *,
    health: PaperHealth | None,
    armed_account_binding_is_valid: bool,
    snapshot_is_current: bool,
) -> bool:
    """Whether a refreshed snapshot proves this session may resume right now."""

    return bool(
        getattr(broker_snapshot, "snapshot_complete", False)
        and armed_account_binding_is_valid
        and health is not None
        and health.safe_to_continue
        and getattr(health, "status", "") == "HEALTHY"
        and snapshot_is_current
    )


def _require_session_id(binding: EvidenceBinding) -> str:
    if not binding.session_id:
        raise ReconciliationEvidenceError("Reconciliation evidence has no session ID.")
    return binding.session_id


def build_reconciliation_evidence(
    *,
    binding: EvidenceBinding,
    broker_snapshot: object,
    armed_account_fingerprint: str,
    health: PaperHealth | None,
    engine_digest: str,
    observed_at: datetime,
) -> CoordinatorReconciliationEvidence:
    """Describe the broker state a human is being asked to approve."""

    return CoordinatorReconciliationEvidence(
        evidence_id=uuid4().hex,
        runtime_id=binding.runtime_id,
        service_identity=binding.service_identity,
        session_id=_require_session_id(binding),
        account_fingerprint=str(getattr(broker_snapshot, "account_fingerprint", "")),
        armed_account_fingerprint=armed_account_fingerprint,
        connection_generation=int(getattr(broker_snapshot, "connection_generation", 0)),
        reconciliation_generation=int(
            getattr(broker_snapshot, "reconciliation_generation", 0)
        ),
        state_version=int(getattr(broker_snapshot, "state_version", 0)),
        captured_at=observed_at.isoformat(),
        expires_at=(observed_at + timedelta(seconds=EVIDENCE_TTL_SECONDS)).isoformat(),
        broker_digest=str(getattr(broker_snapshot, "digest", "")),
        engine_digest=engine_digest,
        health_status=str(getattr(health, "status", "")),
        safe_to_continue=bool(health and health.safe_to_continue),
    )


def build_finalization_evidence(
    *,
    binding: EvidenceBinding,
    broker_snapshot: object,
    engine_digest: str,
    observed_at: datetime,
) -> CoordinatorFinalizationEvidence:
    """Describe the broker zero-state the disconnect must not invalidate."""

    return CoordinatorFinalizationEvidence(
        evidence_id=uuid4().hex,
        runtime_id=binding.runtime_id,
        service_identity=binding.service_identity,
        session_id=_require_session_id(binding),
        connection_generation=int(getattr(broker_snapshot, "connection_generation", 0)),
        captured_at=observed_at.isoformat(),
        broker_digest=str(getattr(broker_snapshot, "digest", "")),
        engine_digest=engine_digest,
    )


def require_reconciliation_evidence(
    evidence: CoordinatorReconciliationEvidence,
    *,
    binding: EvidenceBinding,
    observed_at: datetime,
) -> None:
    """Reject a proof issued elsewhere, already expired, or no longer healthy."""

    if evidence.runtime_id != binding.runtime_id:
        raise ReconciliationEvidenceError(
            "Reconciliation evidence belongs to another runtime."
        )
    if evidence.service_identity != binding.service_identity:
        raise ReconciliationEvidenceError(
            "Reconciliation evidence belongs to another service."
        )
    if evidence.session_id != binding.session_id:
        raise ReconciliationEvidenceError(
            "Reconciliation evidence belongs to another session."
        )
    expires_at = datetime.fromisoformat(evidence.expires_at.replace("Z", "+00:00"))
    if observed_at > utc(expires_at):
        raise ReconciliationEvidenceError("Reconciliation evidence has expired.")
    if not evidence.safe_to_continue or evidence.health_status != "HEALTHY":
        raise ReconciliationEvidenceError("Reconciliation evidence is not healthy.")


def require_unchanged_reconciliation_proof(
    captured: CoordinatorReconciliationEvidence,
    current: CoordinatorReconciliationEvidence,
) -> None:
    """Reject a resume whose second broker refresh no longer matches the proof."""

    if (
        current.account_fingerprint != captured.account_fingerprint
        or current.armed_account_fingerprint != captured.armed_account_fingerprint
        or current.connection_generation != captured.connection_generation
        or current.broker_digest != captured.broker_digest
        or current.engine_digest != captured.engine_digest
    ):
        raise ReconciliationEvidenceError(
            "Reconciliation evidence changed before confirmation."
        )


def require_finalization_evidence(
    evidence: CoordinatorFinalizationEvidence,
    *,
    binding: EvidenceBinding,
    connected: bool,
) -> None:
    """Reject zero-state proof issued elsewhere, or used before disconnecting."""

    if (
        evidence.runtime_id != binding.runtime_id
        or evidence.service_identity != binding.service_identity
        or evidence.session_id != binding.session_id
    ):
        raise ReconciliationEvidenceError("Finalization evidence identity mismatch.")
    if connected:
        raise ReconciliationEvidenceError(
            "Paper service must be disconnected before finalization."
        )