"""Convert IBKR Paper reconciliation DTOs into provider-neutral domain truth."""

from __future__ import annotations

from datetime import datetime

from us_quant.paper_order_models import PaperReconciliationSnapshot
from us_quant.trading.domain.orders import Side
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrder,
    BrokerOpenOrderTruth,
)


def broker_open_order_truth_from_snapshot(
    snapshot: PaperReconciliationSnapshot,
    *,
    account_alias: str,
) -> BrokerOpenOrderTruth:
    """Keep missing or malformed broker remaining quantities incomplete."""

    observed_at = datetime.fromisoformat(snapshot.captured_at)
    valid_orders = all(
        item.side.casefold() in {"buy", "sell"}
        and item.symbol.strip()
        and item.remaining_quantity is not None
        and item.remaining_quantity.is_finite()
        and item.quantity.is_finite()
        and item.remaining_quantity > 0
        and item.remaining_quantity <= item.quantity
        for item in snapshot.open_broker_orders
    )
    orders = tuple(
        BrokerOpenOrder(
            broker_order_id=item.broker_order_id,
            account_alias=account_alias,
            symbol=item.symbol,
            side=Side.BUY if item.side.casefold() == "buy" else Side.SELL,
            quantity=item.quantity,
            remaining_quantity=item.remaining_quantity,
        )
        for item in snapshot.open_broker_orders
    )
    return BrokerOpenOrderTruth(
        account_alias=account_alias,
        observed_at=observed_at,
        snapshot_complete=(
            snapshot.snapshot_complete and bool(account_alias.strip()) and valid_orders
        ),
        open_orders=orders,
    )


__all__ = ["broker_open_order_truth_from_snapshot"]
