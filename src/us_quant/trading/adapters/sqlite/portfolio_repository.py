"""SQLite-backed durable portfolio decision and attribution ledger."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioBlocker,
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioSide,
    PortfolioVerdict,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
    PortfolioStoreUnreadable,
)


def _record_payload(record: PortfolioDecisionRecord) -> str:
    decision = record.decision
    payload = {
        "decision_id": decision.decision_id,
        "decision": decision.decision.value,
        "symbol": decision.symbol,
        "strategy_version_ids": list(decision.strategy_version_ids),
        "requested_quantity": decision.requested_quantity,
        "net_quantity": decision.net_quantity,
        "blocker": decision.blocker.value if decision.blocker else None,
        "attribution": [
            {
                "strategy_version_id": item.strategy_version_id,
                "proposal_id": item.proposal_id,
                "symbol": item.symbol,
                "signed_requested_quantity": item.signed_requested_quantity,
            }
            for item in decision.attribution
        ],
        "action": (
            {
                "symbol": decision.action.symbol,
                "side": decision.action.side.value,
                "quantity": decision.action.quantity,
                "reference_price": str(decision.action.reference_price),
            }
            if decision.action
            else None
        ),
        "portfolio_cycle_id": record.portfolio_cycle_id,
        "observed_at": record.observed_at.isoformat(),
        "snapshot_identity": record.snapshot_identity,
        "policy_identity": record.policy_identity,
        "policy_revision": record.policy_revision,
        "created_at": record.created_at.isoformat(),
        "revision": record.revision,
        "proposal_cutoff": (
            record.proposal_cutoff.isoformat() if record.proposal_cutoff else None
        ),
        "risk_outcome": record.risk_outcome,
        "risk_decision": (
            {
                "approved": record.risk_decision.approved,
                "requested_quantity": record.risk_decision.requested_quantity,
                "approved_quantity": record.risk_decision.approved_quantity,
                "reasons": list(record.risk_decision.reasons),
                "adjustments": list(record.risk_decision.adjustments),
            }
            if record.risk_decision is not None
            else None
        ),
        "order_id": record.order_id,
        "dispatch_outcome_recorded": record.dispatch_outcome_recorded,
        "dispatch_submitted": record.dispatch_submitted,
        "dispatch_halt": record.dispatch_halt,
        "dispatch_status": record.dispatch_status,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _execution_payload(item: PortfolioExecutionAttribution) -> str:
    return json.dumps(
        {
            "order_id": item.order_id,
            "portfolio_decision_id": item.portfolio_decision_id,
            "symbol": item.symbol,
            "side": item.side.value,
            "net_quantity": item.net_quantity,
            "attributions": [
                {
                    "strategy_version_id": entry.strategy_version_id,
                    "proposal_id": entry.proposal_id,
                    "signed_requested_quantity": entry.signed_requested_quantity,
                }
                for entry in item.attributions
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _from_execution_payload(raw: str, *, expected_order_id: str) -> PortfolioExecutionAttribution:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("order_id") != expected_order_id:
            raise ValueError("execution attribution key does not match its order ID")
        decision_id = value["portfolio_decision_id"]
        symbol = value["symbol"]
        attributions = tuple(
            PortfolioOrderAttribution(
                portfolio_decision_id=decision_id,
                strategy_version_id=entry["strategy_version_id"],
                proposal_id=entry["proposal_id"],
                symbol=symbol,
                signed_requested_quantity=entry["signed_requested_quantity"],
            )
            for entry in value["attributions"]
        )
        item = PortfolioExecutionAttribution(
            order_id=value["order_id"],
            portfolio_decision_id=decision_id,
            symbol=symbol,
            side=PortfolioSide(value["side"]),
            net_quantity=value["net_quantity"],
            attributions=attributions,
        )
        if _execution_payload(item) != json.dumps(value, sort_keys=True, separators=(",", ":")):
            raise ValueError("execution attribution contains non-canonical fields")
        return item
    except Exception as exc:
        raise PortfolioStoreUnreadable("stored portfolio execution attribution is unreadable") from exc


def _from_payload(
    raw: str, *, expected_decision_id: str | None = None
) -> PortfolioDecisionRecord:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("portfolio row must be a JSON object")
        # Stage 5-B rows predate these Stage 5-C observation facts.
        value.setdefault("snapshot_identity", "legacy-unrecorded")
        value.setdefault("proposal_cutoff", None)
        value.setdefault("risk_decision", None)
        value.setdefault("dispatch_outcome_recorded", False)
        value.setdefault("dispatch_submitted", False)
        value.setdefault("dispatch_halt", False)
        value.setdefault("dispatch_status", None)
        if (
            expected_decision_id is not None
            and value.get("decision_id") != expected_decision_id
        ):
            raise ValueError("portfolio row key does not match its decision ID")
        attribution_data = value["attribution"]
        if not isinstance(attribution_data, list) or not attribution_data:
            raise ValueError("decision attribution is missing")
        decision_id = value["decision_id"]
        symbol = value["symbol"]
        attribution = tuple(
            PortfolioOrderAttribution(
                portfolio_decision_id=decision_id,
                strategy_version_id=item["strategy_version_id"],
                proposal_id=item["proposal_id"],
                symbol=item["symbol"],
                signed_requested_quantity=item["signed_requested_quantity"],
            )
            for item in attribution_data
        )
        action_data = value["action"]
        action = (
            PortfolioAction(
                symbol=action_data["symbol"],
                side=PortfolioSide(action_data["side"]),
                quantity=action_data["quantity"],
                reference_price=Decimal(action_data["reference_price"]),
            )
            if action_data is not None
            else None
        )
        risk_data = value["risk_decision"]
        risk_decision = (
            RiskDecision(
                approved=risk_data["approved"],
                requested_quantity=risk_data["requested_quantity"],
                approved_quantity=risk_data["approved_quantity"],
                reasons=tuple(risk_data["reasons"]),
                adjustments=tuple(risk_data["adjustments"]),
            )
            if risk_data is not None
            else None
        )
        decision = PortfolioDecision(
            decision_id=decision_id,
            decision=PortfolioVerdict(value["decision"]),
            symbol=symbol,
            strategy_version_ids=tuple(value["strategy_version_ids"]),
            requested_quantity=value["requested_quantity"],
            net_quantity=value["net_quantity"],
            blocker=PortfolioBlocker(value["blocker"]) if value["blocker"] else None,
            attribution=attribution,
            action=action,
        )
        record = PortfolioDecisionRecord(
            decision=decision,
            portfolio_cycle_id=value["portfolio_cycle_id"],
            observed_at=datetime.fromisoformat(value["observed_at"]),
            snapshot_identity=value["snapshot_identity"],
            policy_identity=value["policy_identity"],
            policy_revision=value["policy_revision"],
            created_at=datetime.fromisoformat(value["created_at"]),
            proposal_cutoff=(
                datetime.fromisoformat(value["proposal_cutoff"])
                if value["proposal_cutoff"] is not None
                else None
            ),
            revision=value["revision"],
            risk_outcome=value["risk_outcome"],
            risk_decision=risk_decision,
            order_id=value["order_id"],
            dispatch_outcome_recorded=value["dispatch_outcome_recorded"],
            dispatch_submitted=value["dispatch_submitted"],
            dispatch_halt=value["dispatch_halt"],
            dispatch_status=value["dispatch_status"],
        )
        # Reject malformed or non-canonical persisted data rather than repairing it.
        if _record_payload(record) != json.dumps(value, sort_keys=True, separators=(",", ":")):
            raise ValueError("portfolio row has unknown or non-canonical fields")
        return record
    except PortfolioStoreUnreadable:
        raise
    except Exception as exc:
        raise PortfolioStoreUnreadable("stored portfolio decision is unreadable") from exc


class SQLitePortfolioRepository:
    """Durable append-only decisions with guarded risk/order linkage updates."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS portfolio_decision (
                        decision_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS portfolio_execution_attribution (
                        order_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL
                    )"""
                )

    def decision(self, decision_id: str) -> PortfolioDecisionRecord | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                "SELECT decision_id, payload FROM portfolio_decision WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return _from_payload(row[1], expected_decision_id=row[0]) if row else None

    def decisions(self) -> tuple[PortfolioDecisionRecord, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                "SELECT decision_id, payload FROM portfolio_decision ORDER BY decision_id"
            ).fetchall()
        return tuple(
            _from_payload(payload, expected_decision_id=decision_id)
            for decision_id, payload in rows
        )

    def execution_attribution(self, order_id: str) -> PortfolioExecutionAttribution | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                "SELECT order_id, payload FROM portfolio_execution_attribution WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return _from_execution_payload(row[1], expected_order_id=row[0]) if row else None

    def execution_attributions(self) -> tuple[PortfolioExecutionAttribution, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                "SELECT order_id, payload FROM portfolio_execution_attribution ORDER BY order_id"
            ).fetchall()
        return tuple(
            _from_execution_payload(payload, expected_order_id=order_id)
            for order_id, payload in rows
        )

    def record_decision(self, record: PortfolioDecisionRecord) -> PortfolioDecisionRecord:
        if not isinstance(record, PortfolioDecisionRecord):
            raise TypeError("record must be PortfolioDecisionRecord")
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                row = connection.execute(
                    "SELECT decision_id, payload FROM portfolio_decision WHERE decision_id = ?",
                    (record.decision.decision_id,),
                ).fetchone()
                if row:
                    stored = _from_payload(row[1], expected_decision_id=row[0])
                    # Risk/execution facts may have been appended after the
                    # original decision; retrying the original append remains
                    # idempotent and must preserve those later facts.
                    retry = _preserve_mutable_fields(record, stored)
                    if retry != stored:
                        raise ValueError("conflicting portfolio decision id")
                    return stored
                created = replace(record, revision=1)
                connection.execute(
                    "INSERT INTO portfolio_decision(decision_id, payload) VALUES (?, ?)",
                    (created.decision.decision_id, _record_payload(created)),
                )
                return created

    def update_decision(
        self,
        record: PortfolioDecisionRecord,
        *,
        expected_revision: int,
    ) -> PortfolioDecisionRecord:
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError("expected_revision must be a positive integer")
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                row = connection.execute(
                    "SELECT decision_id, payload FROM portfolio_decision WHERE decision_id = ?",
                    (record.decision.decision_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("portfolio decision does not exist")
                current = _from_payload(row[1], expected_decision_id=row[0])
                if current.revision != expected_revision:
                    raise ValueError("stale portfolio decision revision")
                if record.dispatch_outcome_recorded and not current.dispatch_outcome_recorded:
                    raise ValueError("dispatch outcomes require atomic execution attribution")
                if _preserve_mutable_fields(record, current) != current:
                    raise ValueError("decision facts are immutable after persistence")
                if current.risk_outcome is not None and record.risk_outcome != current.risk_outcome:
                    raise ValueError("risk outcome cannot be rewritten")
                if current.order_id is not None and record.order_id != current.order_id:
                    raise ValueError("order linkage cannot be rewritten")
                if current.risk_decision is not None and record.risk_decision != current.risk_decision:
                    raise ValueError("risk decision cannot be rewritten")
                if current.dispatch_outcome_recorded and (
                    record.dispatch_outcome_recorded != current.dispatch_outcome_recorded
                    or record.dispatch_submitted != current.dispatch_submitted
                    or record.dispatch_halt != current.dispatch_halt
                    or record.dispatch_status != current.dispatch_status
                ):
                    raise ValueError("dispatch outcome cannot be rewritten")
                updated = replace(record, revision=current.revision + 1)
                cursor = connection.execute(
                    "UPDATE portfolio_decision SET payload = ? WHERE decision_id = ? AND payload = ?",
                    (_record_payload(updated), record.decision.decision_id, row[1]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("stale portfolio decision revision")
                return updated

    def record_dispatch_outcome(
        self,
        record: PortfolioDecisionRecord,
        execution_attribution: PortfolioExecutionAttribution | None,
        *,
        expected_revision: int,
    ) -> PortfolioDecisionRecord:
        if not record.dispatch_outcome_recorded:
            raise ValueError("dispatch outcome must be recorded")
        if (record.order_id is None) != (execution_attribution is None):
            raise ValueError("order linkage and execution attribution must be recorded together")
        if execution_attribution is not None and (
            execution_attribution.order_id != record.order_id
            or execution_attribution.portfolio_decision_id != record.decision.decision_id
            or execution_attribution.symbol != record.decision.symbol
            or execution_attribution.attributions != record.decision.attribution
            or record.decision.action is None
            or execution_attribution.side is not record.decision.action.side
            or execution_attribution.net_quantity != record.decision.net_quantity
        ):
            raise ValueError("execution attribution does not match its portfolio decision")
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError("expected_revision must be a positive integer")
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                row = connection.execute(
                    "SELECT decision_id, payload FROM portfolio_decision WHERE decision_id = ?",
                    (record.decision.decision_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("portfolio decision does not exist")
                current = _from_payload(row[1], expected_decision_id=row[0])
                if current.revision != expected_revision:
                    raise ValueError("stale portfolio decision revision")
                if _preserve_mutable_fields(record, current) != current:
                    raise ValueError("decision facts are immutable after persistence")
                if current.risk_outcome is not None and record.risk_outcome != current.risk_outcome:
                    raise ValueError("risk outcome cannot be rewritten")
                if current.risk_decision is not None and record.risk_decision != current.risk_decision:
                    raise ValueError("risk decision cannot be rewritten")
                if current.dispatch_outcome_recorded:
                    raise ValueError("dispatch outcome cannot be rewritten")
                updated = replace(record, revision=current.revision + 1)
                cursor = connection.execute(
                    "UPDATE portfolio_decision SET payload = ? WHERE decision_id = ? AND payload = ?",
                    (_record_payload(updated), record.decision.decision_id, row[1]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("stale portfolio decision revision")
                if execution_attribution is not None:
                    try:
                        connection.execute(
                            "INSERT INTO portfolio_execution_attribution(order_id, payload) VALUES (?, ?)",
                            (execution_attribution.order_id, _execution_payload(execution_attribution)),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("order already has portfolio execution attribution") from exc
                return updated


def _preserve_mutable_fields(
    incoming: PortfolioDecisionRecord,
    stored: PortfolioDecisionRecord,
) -> PortfolioDecisionRecord:
    """Apply the store's mutable revision/outcome fields to an idempotent retry."""

    return replace(
        incoming,
        revision=stored.revision,
        risk_outcome=stored.risk_outcome,
        risk_decision=stored.risk_decision,
        order_id=stored.order_id,
        dispatch_outcome_recorded=stored.dispatch_outcome_recorded,
        dispatch_submitted=stored.dispatch_submitted,
        dispatch_halt=stored.dispatch_halt,
        dispatch_status=stored.dispatch_status,
    )
