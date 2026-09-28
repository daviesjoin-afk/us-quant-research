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
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
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
        "policy_identity": record.policy_identity,
        "policy_revision": record.policy_revision,
        "created_at": record.created_at.isoformat(),
        "revision": record.revision,
        "risk_outcome": record.risk_outcome,
        "order_id": record.order_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _from_payload(raw: str) -> PortfolioDecisionRecord:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("portfolio row must be a JSON object")
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
            policy_identity=value["policy_identity"],
            policy_revision=value["policy_revision"],
            created_at=datetime.fromisoformat(value["created_at"]),
            revision=value["revision"],
            risk_outcome=value["risk_outcome"],
            order_id=value["order_id"],
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

    def decision(self, decision_id: str) -> PortfolioDecisionRecord | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                "SELECT payload FROM portfolio_decision WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return _from_payload(row[0]) if row else None

    def decisions(self) -> tuple[PortfolioDecisionRecord, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                "SELECT payload FROM portfolio_decision ORDER BY decision_id"
            ).fetchall()
        return tuple(_from_payload(row[0]) for row in rows)

    def record_decision(self, record: PortfolioDecisionRecord) -> PortfolioDecisionRecord:
        if not isinstance(record, PortfolioDecisionRecord):
            raise TypeError("record must be PortfolioDecisionRecord")
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                row = connection.execute(
                    "SELECT payload FROM portfolio_decision WHERE decision_id = ?",
                    (record.decision.decision_id,),
                ).fetchone()
                if row:
                    stored = _from_payload(row[0])
                    # Risk/execution facts may have been appended after the
                    # original decision; retrying the original append remains
                    # idempotent and must preserve those later facts.
                    retry = replace(
                        record,
                        revision=stored.revision,
                        risk_outcome=stored.risk_outcome,
                        order_id=stored.order_id,
                    )
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
                    "SELECT payload FROM portfolio_decision WHERE decision_id = ?",
                    (record.decision.decision_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("portfolio decision does not exist")
                current = _from_payload(row[0])
                if current.revision != expected_revision:
                    raise ValueError("stale portfolio decision revision")
                if (
                    replace(record, revision=current.revision, risk_outcome=current.risk_outcome,
                            order_id=current.order_id)
                    != current
                ):
                    raise ValueError("decision facts are immutable after persistence")
                if current.risk_outcome is not None and record.risk_outcome != current.risk_outcome:
                    raise ValueError("risk outcome cannot be rewritten")
                if current.order_id is not None and record.order_id != current.order_id:
                    raise ValueError("order linkage cannot be rewritten")
                updated = replace(record, revision=current.revision + 1)
                cursor = connection.execute(
                    "UPDATE portfolio_decision SET payload = ? WHERE decision_id = ? AND payload = ?",
                    (_record_payload(updated), record.decision.decision_id, row[0]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("stale portfolio decision revision")
                return updated
