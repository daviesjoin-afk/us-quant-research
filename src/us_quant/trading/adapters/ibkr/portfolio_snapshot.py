"""One fresh IBKR Paper observation shared by account and open-order truth."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from us_quant.paper_order_models import (
    PaperBrokerState,
    PaperOrderConnection,
    PaperReconciliationSnapshot,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrderTruth
from us_quant.trading.runtime.paper_contracts import PaperOrderPort
from us_quant.trading.adapters.ibkr.portfolio_reconciliation_mapping import (
    broker_open_order_truth_from_snapshot,
)


class IBKRPaperPortfolioObservationSource:
    """Refresh once, then expose account and open orders from that same generation."""

    def __init__(self, *, orders: PaperOrderPort, session_id: str, market_snapshot: MarketSnapshot | None = None) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        self._orders = orders
        self._session_id = session_id
        self._market_snapshot = market_snapshot
        self._snapshot: PaperReconciliationSnapshot | None = None
        self._connection: PaperOrderConnection | None = None
        self._state: PaperBrokerState | None = None

    def set_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        if not isinstance(snapshot, MarketSnapshot):
            raise TypeError("market observation must be a MarketSnapshot")
        self._market_snapshot = snapshot

    def broker_portfolio(self) -> BrokerAccountPortfolio:
        snapshot = self._orders.refresh_reconciliation_snapshot(self._session_id)
        connection = self._orders.connection_snapshot()
        state = self._orders.broker_state()
        if not isinstance(snapshot, PaperReconciliationSnapshot):
            raise TypeError("Paper order port returned an invalid reconciliation snapshot")
        if not isinstance(connection, PaperOrderConnection) or not isinstance(state, PaperBrokerState):
            raise TypeError("Paper order port returned invalid account observations")
        if (
            not snapshot.snapshot_complete
            or not connection.connected
            or not connection.snapshot_complete
            or connection.connection_generation != snapshot.connection_generation
            or not self._orders.reconciliation_snapshot_is_current(snapshot)
            or connection.account_alias != state.account_alias
        ):
            raise RuntimeError("fresh Paper account/open-order observation is incomplete")
        observed_at = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
        quotes = {
            quote.symbol.strip().upper(): quote
            for quote in (self._market_snapshot.quotes if self._market_snapshot else ())
            if quote.realtime_ready
            and quote.age_seconds is not None
            and quote.age_seconds <= 30
            and quote.bid is not None
            and quote.ask is not None
        }
        positions = []
        market_values = []
        state_positions = {item.symbol.strip().upper(): item for item in state.positions}
        for item in snapshot.broker_positions:
            symbol = item.symbol.strip().upper()
            state_position = state_positions.get(symbol)
            if state_position is None:
                raise RuntimeError("broker account and reconciliation position sets disagree")
            quote = quotes.get(symbol)
            market_value = (
                (quote.bid + quote.ask) / Decimal("2") * item.quantity
                if quote is not None
                else None
            )
            if market_value is not None:
                market_values.append(market_value)
            positions.append(BrokerPositionSnapshot(
                account_alias=connection.account_alias,
                con_id=0,
                symbol=symbol,
                local_symbol=symbol,
                security_type="STK",
                exchange="SMART",
                currency="USD",
                quantity=item.quantity,
                average_cost=state_position.average_cost,
                market_value=market_value,
                daily_pnl=None,
                unrealized_pnl=None,
                realized_pnl=None,
                observed_at=observed_at,
            ))
        fully_valued = len(market_values) == len(positions)
        account = BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias=connection.account_alias,
            net_liquidation=state.net_liquidation,
            cash=state.cash,
            available_funds=state.available_funds,
            buying_power=state.buying_power,
            gross_position_value=(sum(market_values, Decimal("0")) if fully_valued else None),
            excess_liquidity=None,
            maintenance_margin=None,
            cushion=None,
            daily_pnl=state.daily_pnl,
            unrealized_pnl=state.unrealized_pnl,
            realized_pnl=state.realized_pnl,
            observed_at=observed_at,
            pnl_source="IBKR Paper reconciliation snapshot",
        )
        self._snapshot, self._connection, self._state = snapshot, connection, state
        return BrokerAccountPortfolio(account=account, positions=tuple(positions))

    def broker_open_order_truth(self) -> BrokerOpenOrderTruth:
        snapshot, connection = self._snapshot, self._connection
        if snapshot is None or connection is None:
            raise RuntimeError("broker portfolio must be refreshed before open-order truth")
        truth = broker_open_order_truth_from_snapshot(
            snapshot, account_alias=connection.account_alias
        )
        complete = (
            truth.snapshot_complete
            and self._orders.reconciliation_snapshot_is_current(snapshot)
            and connection.connection_generation == snapshot.connection_generation
        )
        return truth if complete else BrokerOpenOrderTruth(
            account_alias=truth.account_alias,
            observed_at=truth.observed_at,
            snapshot_complete=False,
            open_orders=truth.open_orders,
        )


__all__ = ["IBKRPaperPortfolioObservationSource"]
