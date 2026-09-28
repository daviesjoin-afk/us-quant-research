"""Atomic SQLite storage for operator portfolio plans and their audit trail."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.domain.portfolio import PortfolioCapitalPolicy, PortfolioStrategyAllocation
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan, PortfolioPlanAuditEvent
from us_quant.trading.ports.portfolio_operating_plan import PortfolioOperatingPlanConflict


class PortfolioOperatingPlanStoreUnreadable(RuntimeError):
    """Stored operating plan or audit cannot be trusted."""


def _policy_dict(policy: PortfolioCapitalPolicy) -> dict[str, object]:
    return {
        "total_capital_limit": str(policy.total_capital_limit),
        "max_gross_exposure": str(policy.max_gross_exposure),
        "max_net_exposure": str(policy.max_net_exposure),
        "max_single_position_notional": str(policy.max_single_position_notional),
        "max_symbol_concentration": str(policy.max_symbol_concentration),
        "max_strategy_concentration": str(policy.max_strategy_concentration),
        "max_positions": policy.max_positions,
        "max_open_orders": policy.max_open_orders,
        "allocations": [
            {
                "strategy_version_id": item.strategy_version_id,
                "capital_weight": str(item.capital_weight),
                "max_capital": str(item.max_capital),
                "max_gross_exposure": str(item.max_gross_exposure),
                "enabled": item.enabled,
            }
            for item in policy.allocations
        ],
    }


def _policy_from_dict(value: object) -> PortfolioCapitalPolicy:
    if not isinstance(value, dict):
        raise ValueError("policy is not an object")
    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal(value["total_capital_limit"]),
        max_gross_exposure=Decimal(value["max_gross_exposure"]),
        max_net_exposure=Decimal(value["max_net_exposure"]),
        max_single_position_notional=Decimal(value["max_single_position_notional"]),
        max_symbol_concentration=Decimal(value["max_symbol_concentration"]),
        max_strategy_concentration=Decimal(value["max_strategy_concentration"]),
        max_positions=value["max_positions"],
        max_open_orders=value["max_open_orders"],
        allocations=tuple(
            PortfolioStrategyAllocation(
                strategy_version_id=item["strategy_version_id"],
                capital_weight=Decimal(item["capital_weight"]),
                max_capital=Decimal(item["max_capital"]),
                max_gross_exposure=Decimal(item["max_gross_exposure"]),
                enabled=item["enabled"],
            )
            for item in value["allocations"]
        ),
    )


def _encode_plan(plan: PortfolioOperatingPlan) -> str:
    return json.dumps(
        {
            "plan_id": plan.plan_id,
            "revision": plan.revision,
            "selected_version_ids": list(plan.selected_version_ids),
            "policy": _policy_dict(plan.policy),
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_plan(raw: str) -> PortfolioOperatingPlan:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("plan payload is not an object")
        return PortfolioOperatingPlan(
            plan_id=value["plan_id"],
            revision=value["revision"],
            selected_version_ids=tuple(value["selected_version_ids"]),
            policy=_policy_from_dict(value["policy"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
        )
    except Exception as error:  # noqa: BLE001
        raise PortfolioOperatingPlanStoreUnreadable("stored portfolio operating plan is unreadable") from error


class SQLitePortfolioOperatingPlanRepository:
    """One current plan row plus append-only audit revisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_sqlite(self.path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS portfolio_operating_plan (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS portfolio_operating_plan_audit (plan_id TEXT NOT NULL, revision INTEGER NOT NULL, changed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(plan_id, revision))")

    def load(self) -> PortfolioOperatingPlan | None:
        try:
            with closing(connect_sqlite(self.path)) as connection:
                row = connection.execute("SELECT payload FROM portfolio_operating_plan WHERE key='current'").fetchone()
            return _decode_plan(row[0]) if row else None
        except PortfolioOperatingPlanStoreUnreadable:
            raise
        except sqlite3.Error as error:
            raise PortfolioOperatingPlanStoreUnreadable("portfolio operating plan could not be read") from error

    def save(self, plan: PortfolioOperatingPlan, *, expected_revision: int, audit: PortfolioPlanAuditEvent) -> PortfolioOperatingPlan:
        if type(expected_revision) is not int or expected_revision < 0 or plan.revision != expected_revision + 1:
            raise ValueError("plan revision must advance exactly once")
        if audit.plan_id != plan.plan_id or audit.revision != plan.revision or audit.changed_at != plan.updated_at:
            raise ValueError("audit event must describe the saved plan revision")
        try:
            with closing(connect_sqlite(self.path)) as connection:
                connection.isolation_level = None
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute("SELECT revision, payload FROM portfolio_operating_plan WHERE key='current'").fetchone()
                    current_revision = 0 if row is None else row[0]
                    if current_revision != expected_revision:
                        raise PortfolioOperatingPlanConflict("portfolio plan changed; stale update was refused")
                    if row is not None:
                        current = _decode_plan(row[1])
                        if current.plan_id != plan.plan_id or current.created_at != plan.created_at:
                            raise ValueError("plan identity and creation time are immutable")
                    connection.execute("INSERT INTO portfolio_operating_plan(key, revision, payload) VALUES('current', ?, ?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision, payload=excluded.payload", (plan.revision, _encode_plan(plan)))
                    audit_payload = json.dumps({
                        "selected_version_ids": list(audit.selected_version_ids),
                        "policy_limits": list(audit.policy_limits),
                        "allocation_limits": [list(item) for item in audit.allocation_limits],
                        "operator_reason": audit.operator_reason,
                    }, sort_keys=True, separators=(",", ":"))
                    connection.execute("INSERT INTO portfolio_operating_plan_audit(plan_id, revision, changed_at, payload) VALUES(?, ?, ?, ?)", (audit.plan_id, audit.revision, audit.changed_at.isoformat(), audit_payload))
                    connection.execute("COMMIT")
                except BaseException:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
            return plan
        except PortfolioOperatingPlanConflict:
            raise
        except sqlite3.Error as error:
            raise PortfolioOperatingPlanStoreUnreadable("portfolio operating plan could not be stored") from error

    def audit_events(self) -> tuple[PortfolioPlanAuditEvent, ...]:
        try:
            with closing(connect_sqlite(self.path)) as connection:
                rows = connection.execute("SELECT plan_id, revision, changed_at, payload FROM portfolio_operating_plan_audit ORDER BY revision").fetchall()
            result = []
            for plan_id, revision, changed_at, raw in rows:
                value = json.loads(raw)
                result.append(PortfolioPlanAuditEvent(
                    plan_id=plan_id,
                    revision=revision,
                    changed_at=datetime.fromisoformat(changed_at),
                    selected_version_ids=tuple(value["selected_version_ids"]),
                    policy_limits=tuple(value["policy_limits"]),
                    allocation_limits=tuple(tuple(item) for item in value["allocation_limits"]),
                    operator_reason=value["operator_reason"],
                ))
            return tuple(result)
        except Exception as error:  # noqa: BLE001
            raise PortfolioOperatingPlanStoreUnreadable("portfolio operating plan audit is unreadable") from error
