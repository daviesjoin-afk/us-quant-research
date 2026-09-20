"""Unit tests for the pure reconciliation proofs.

The coordinator's own tests cover the flows; these cover the facts a proof is
made of, because a proof that is missing an identity field, or that outlives the
broker state it describes, is a resume nothing else in the suite would catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from us_quant.trading.runtime.reconciliation import (
    EVIDENCE_TTL_SECONDS,
    CoordinatorReconciliationEvidence,
    EvidenceBinding,
    OneShotEvidence,
    ReconciliationEvidenceError,
    build_finalization_evidence,
    build_reconciliation_evidence,
    engine_snapshot_digest,
    proof_is_safe_and_current,
    require_finalization_evidence,
    require_reconciliation_evidence,
    require_unchanged_reconciliation_proof,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
BINDING = EvidenceBinding(runtime_id="runtime", service_identity=7, session_id="session")


@dataclass(frozen=True)
class Position:
    symbol: str = "AAA"
    quantity: int = 10
    average_price: str = "100.5"


@dataclass(frozen=True)
class Pending:
    order_id: str = "order-1"
    execution_symbol: str = "AAA"
    side: str = "BUY"
    quantity: int = 10
    limit_price: str = "100.5"


@dataclass(frozen=True)
class Snapshot:
    session_id: str | None = "session"
    positions: tuple[object, ...] = (Position(),)
    pending_orders: tuple[object, ...] = (Pending(),)


@dataclass(frozen=True)
class Health:
    safe_to_continue: bool = True
    status: str = "HEALTHY"


@dataclass(frozen=True)
class BrokerSnapshot:
    account_fingerprint: str = "DU***17"
    connection_generation: int = 1
    reconciliation_generation: int = 2
    state_version: int = 3
    digest: str = "broker-digest"
    snapshot_complete: bool = True
    broker_positions: tuple[object, ...] = ()
    open_broker_orders: tuple[object, ...] = ()


def test_the_engine_digest_is_exact_about_local_state() -> None:
    assert engine_snapshot_digest(Snapshot()) == engine_snapshot_digest(Snapshot())

    changed = Snapshot(positions=(Position(quantity=11),))
    assert engine_snapshot_digest(changed) != engine_snapshot_digest(Snapshot())


def test_the_engine_digest_does_not_depend_on_row_order() -> None:
    """Two orderings of the same book are the same book."""

    first = Snapshot(
        positions=(Position(symbol="AAA"), Position(symbol="BBB")),
        pending_orders=(Pending(order_id="a"), Pending(order_id="b")),
    )
    second = Snapshot(
        positions=(Position(symbol="BBB"), Position(symbol="AAA")),
        pending_orders=(Pending(order_id="b"), Pending(order_id="a")),
    )
    assert engine_snapshot_digest(first) == engine_snapshot_digest(second)


def test_reconciliation_evidence_carries_identity_generations_and_ttl() -> None:
    snapshot = BrokerSnapshot()
    built = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=snapshot,
        armed_account_fingerprint="armed-account-a",
        health=Health(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )

    assert built.evidence_id
    assert built.runtime_id == "runtime"
    assert built.service_identity == 7
    assert built.session_id == "session"
    assert built.account_fingerprint == "DU***17"
    assert built.armed_account_fingerprint == "armed-account-a"
    assert built.connection_generation == 1
    assert built.reconciliation_generation == 2
    assert built.state_version == 3
    assert built.broker_digest == "broker-digest"
    assert built.engine_digest == "engine-digest"
    assert built.health_status == "HEALTHY"
    assert built.safe_to_continue is True
    assert built.captured_at == NOW.isoformat()
    assert built.expires_at == (NOW + timedelta(seconds=30)).isoformat()
    assert EVIDENCE_TTL_SECONDS == 30


def test_reconciliation_evidence_needs_a_session_id() -> None:
    with pytest.raises(ReconciliationEvidenceError, match="no session ID"):
        build_reconciliation_evidence(
            binding=EvidenceBinding("runtime", 7, None),
            broker_snapshot=BrokerSnapshot(),
            armed_account_fingerprint="armed-account-a",
            health=Health(),
            engine_digest="engine-digest",
            observed_at=NOW,
        )


def test_evidence_from_another_runtime_service_or_session_is_rejected() -> None:
    built = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        armed_account_fingerprint="armed-account-a",
        health=Health(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )

    for binding, message in (
        (EvidenceBinding("other", 7, "session"), "another runtime"),
        (EvidenceBinding("runtime", 8, "session"), "another service"),
        (EvidenceBinding("runtime", 7, "other"), "another session"),
    ):
        with pytest.raises(ReconciliationEvidenceError, match=message):
            require_reconciliation_evidence(
                built, binding=binding, observed_at=NOW
            )

    require_reconciliation_evidence(built, binding=BINDING, observed_at=NOW)


def test_expired_evidence_is_rejected() -> None:
    built = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        armed_account_fingerprint="armed-account-a",
        health=Health(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )

    require_reconciliation_evidence(
        built, binding=BINDING, observed_at=NOW + timedelta(seconds=29)
    )
    with pytest.raises(ReconciliationEvidenceError, match="expired"):
        require_reconciliation_evidence(
            built, binding=BINDING, observed_at=NOW + timedelta(seconds=31)
        )


def test_unhealthy_evidence_is_rejected() -> None:
    built = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        armed_account_fingerprint="armed-account-a",
        health=Health(False, "HALT"),
        engine_digest="engine-digest",
        observed_at=NOW,
    )

    with pytest.raises(ReconciliationEvidenceError, match="not healthy"):
        require_reconciliation_evidence(built, binding=BINDING, observed_at=NOW)


def test_one_shot_evidence_can_be_consumed_exactly_once() -> None:
    consumed = OneShotEvidence()

    consumed.consume("evidence-1", label="Reconciliation")

    with pytest.raises(ReconciliationEvidenceError, match="already consumed"):
        consumed.consume("evidence-1", label="Reconciliation")
    consumed.consume("evidence-2", label="Reconciliation")


def test_a_proof_requires_a_complete_current_snapshot_and_a_healthy_verdict() -> None:
    @dataclass(frozen=True)
    class Incomplete:
        snapshot_complete: bool = False

    assert proof_is_safe_and_current(
        BrokerSnapshot(),
        health=Health(),
        armed_account_binding_is_valid=True,
        snapshot_is_current=True,
    )
    assert not proof_is_safe_and_current(
        BrokerSnapshot(),
        health=Health(False, "HALT"),
        armed_account_binding_is_valid=True,
        snapshot_is_current=True,
    )
    assert not proof_is_safe_and_current(
        BrokerSnapshot(),
        health=None,
        armed_account_binding_is_valid=True,
        snapshot_is_current=True,
    )
    assert not proof_is_safe_and_current(
        BrokerSnapshot(),
        health=Health(),
        armed_account_binding_is_valid=False,
        snapshot_is_current=True,
    )
    assert not proof_is_safe_and_current(
        BrokerSnapshot(),
        health=Health(),
        armed_account_binding_is_valid=True,
        snapshot_is_current=False,
    )
    assert not proof_is_safe_and_current(
        Incomplete(),
        health=Health(),
        armed_account_binding_is_valid=True,
        snapshot_is_current=True,
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("account_fingerprint", "DU***99"),
        ("armed_account_fingerprint", "armed-account-b"),
        ("connection_generation", 2),
        ("broker_digest", "other-digest"),
        ("engine_digest", "other-engine-digest"),
    ),
)
def test_a_changed_broker_or_account_proof_blocks_confirmation(
    field: str, value: object
) -> None:
    captured = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        armed_account_fingerprint="armed-account-a",
        health=Health(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )
    current = build_reconciliation_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        armed_account_fingerprint="armed-account-a",
        health=Health(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )
    current = replace_field(current, field, value)

    with pytest.raises(ReconciliationEvidenceError, match="changed"):
        require_unchanged_reconciliation_proof(captured, current)


def replace_field(
    evidence: CoordinatorReconciliationEvidence, field: str, value: object
) -> CoordinatorReconciliationEvidence:
    """Rebuild a frozen proof with one field changed, for the comparison test."""

    values = {
        name: getattr(evidence, name)
        for name in evidence.__slots__  # type: ignore[attr-defined]
    }
    values[field] = value
    return CoordinatorReconciliationEvidence(**values)  # type: ignore[arg-type]


def test_finalization_evidence_requires_matching_identity_and_a_disconnect() -> None:
    built = build_finalization_evidence(
        binding=BINDING,
        broker_snapshot=BrokerSnapshot(),
        engine_digest="engine-digest",
        observed_at=NOW,
    )

    assert built.connection_generation == 1
    assert built.captured_at == NOW.isoformat()
    require_finalization_evidence(built, binding=BINDING, connected=False)
    with pytest.raises(ReconciliationEvidenceError, match="disconnected"):
        require_finalization_evidence(built, binding=BINDING, connected=True)
    with pytest.raises(ReconciliationEvidenceError, match="identity mismatch"):
        require_finalization_evidence(
            built,
            binding=EvidenceBinding("other", 7, "session"),
            connected=False,
        )