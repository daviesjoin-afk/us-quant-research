from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3

from us_quant.portfolio_view import AccountView
from us_quant.sqlite_support import connect_sqlite


@dataclass(frozen=True, slots=True)
class EquityPoint:
    observed_at: str
    environment: str
    account_alias: str
    net_liquidation: Decimal | None
    cash: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


class AccountLedger:
    """Append-only, redacted account equity history."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append(self, account: AccountView) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO account_equity (
                        observed_at, environment, account_alias,
                        net_liquidation, cash, daily_pnl,
                        unrealized_pnl, realized_pnl, pnl_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account.observed_at,
                        account.environment,
                        account.account_alias,
                        _text(account.net_liquidation),
                        _text(account.cash),
                        _text(account.daily_pnl),
                        _text(account.unrealized_pnl),
                        _text(account.realized_pnl),
                        account.pnl_source,
                    ),
                )

    def list_points(
        self,
        *,
        environment: str,
        account_alias: str,
        limit: int = 500,
    ) -> tuple[EquityPoint, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT observed_at, environment, account_alias,
                       net_liquidation, cash, daily_pnl,
                       unrealized_pnl, realized_pnl
                FROM account_equity
                WHERE environment = ? AND account_alias = ?
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (environment, account_alias, limit),
            ).fetchall()
        return tuple(
            EquityPoint(
                observed_at=row[0],
                environment=row[1],
                account_alias=row[2],
                net_liquidation=_decimal(row[3]),
                cash=_decimal(row[4]),
                daily_pnl=_decimal(row[5]),
                unrealized_pnl=_decimal(row[6]),
                realized_pnl=_decimal(row[7]),
            )
            for row in reversed(rows)
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_equity (
                        observed_at TEXT NOT NULL,
                        environment TEXT NOT NULL,
                        account_alias TEXT NOT NULL,
                        net_liquidation TEXT,
                        cash TEXT,
                        daily_pnl TEXT,
                        unrealized_pnl TEXT,
                        realized_pnl TEXT,
                        pnl_source TEXT NOT NULL,
                        PRIMARY KEY (
                            observed_at, environment, account_alias
                        )
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)


def _text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None
