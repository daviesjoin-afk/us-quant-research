from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.application.portfolio_snapshot import (
    BrokerPortfolioSnapshotSource,
    PortfolioSnapshotUnavailable,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.orders import Side
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrder,
    BrokerOpenOrderTruth,
    PortfolioOrderTruth,
    PortfolioReconciliationBlocker,
    PortfolioOrderTruthRecord,
)
from us_quant.trading.domain.orders import OrderEvent, OrderIntent, OrderStatus
from us_quant.trading.domain.portfolio import PortfolioOpenOrder, PortfolioSide

ACCOUNT_ALIAS = "DU***01"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _broker(*, cash=Decimal("9000")):
    return BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER, account_alias=ACCOUNT_ALIAS,
            net_liquidation=Decimal("10000"), cash=cash,
            available_funds=Decimal("9000"), buying_power=Decimal("18000"),
            gross_position_value=Decimal("0"), excess_liquidity=Decimal("9000"),
            maintenance_margin=Decimal("100"), cushion=Decimal("0.9"),
            daily_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"), observed_at=NOW, pnl_source="test",
        ),
        positions=(),
    )


class _BrokerOrderTruth:
    def __init__(self, truth):
        self.truth = truth
        self.calls = 0

    def broker_open_order_truth(self):
        self.calls += 1
        return self.truth


class _OrderTruth:
    def __init__(self, truth=PortfolioOrderTruth(())):
        self.truth = truth

    def portfolio_order_truth(self):
        return self.truth


class _PortfolioRepository:
    def decisions(self):
        return ()

    def execution_attributions(self):
        return ()


def _source(account=None, *, open_orders=(), order_truth=None):
    truth = BrokerOpenOrderTruth(
        account_alias=ACCOUNT_ALIAS,
        observed_at=NOW,
        snapshot_complete=True,
        open_orders=tuple(open_orders),
    )
    provider = _BrokerOrderTruth(truth)
    source = BrokerPortfolioSnapshotSource(
        broker_portfolio=lambda: account or _broker(),
        broker_open_orders=provider,
        order_truth=_OrderTruth(order_truth or PortfolioOrderTruth(())),
        portfolio_repository=_PortfolioRepository(),
    )
    return source, provider


def test_snapshot_source_reads_fresh_broker_and_order_truth_each_cycle():
    source, provider = _source()
    first = source.snapshot(observed_at=NOW)
    second = source.snapshot(observed_at=NOW)
    assert first.cash == Decimal("9000")
    assert first.equity == Decimal("10000")
    assert second.observed_at == NOW
    assert provider.calls == 2
    assert source.last_reconciliation is not None
    assert source.last_reconciliation.can_open_exposure


def test_snapshot_source_fails_closed_when_broker_cash_is_unknown():
    source, _ = _source(_broker(cash=None))
    with pytest.raises(PortfolioSnapshotUnavailable, match="cash or equity is unknown"):
        source.snapshot(observed_at=NOW)


def test_unknown_broker_open_order_blocks_snapshot_and_exposure():
    open_order = BrokerOpenOrder(
        broker_order_id=91,
        account_alias=ACCOUNT_ALIAS,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        remaining_quantity=Decimal("10"),
    )
    source, _ = _source(open_orders=(open_order,))
    with pytest.raises(PortfolioSnapshotUnavailable) as raised:
        source.snapshot(observed_at=NOW)
    assert raised.value.reconciliation is not None
    assert PortfolioReconciliationBlocker.UNEXPLAINED_ORDER in raised.value.reconciliation.blockers


def test_completed_legacy_order_outside_portfolio_ledger_does_not_block_snapshot():
    intent = OrderIntent.create(
        session_id="legacy-session",
        strategy_version_id="legacy-strategy",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=1,
        limit_price=Decimal("10"),
        reason="legacy single-strategy order",
    )
    legacy = PortfolioOrderTruthRecord(
        intent=intent,
        account_alias=ACCOUNT_ALIAS,
        broker_order_id=77,
        events=(OrderEvent(
            order_id=intent.order_id,
            broker_order_id=77,
            status=OrderStatus.FILLED,
            filled=Decimal("1"),
            remaining=Decimal("0"),
            occurred_at=NOW,
        ),),
    )
    source, _ = _source(order_truth=PortfolioOrderTruth((legacy,)))

    snapshot = source.snapshot(observed_at=NOW)

    assert snapshot.positions == ()
    assert source.last_reconciliation is not None
    assert source.last_reconciliation.can_open_exposure


def test_unexplained_broker_position_blocks_snapshot_and_exposure():
    position = BrokerPositionSnapshot(
        account_alias=ACCOUNT_ALIAS,
        con_id=1,
        symbol="AAPL",
        local_symbol="AAPL",
        security_type="STK",
        exchange="SMART",
        currency="USD",
        quantity=Decimal("10"),
        average_cost=Decimal("10"),
        market_value=Decimal("100"),
        daily_pnl=None,
        unrealized_pnl=None,
        realized_pnl=None,
        observed_at=NOW,
    )
    source, _ = _source(replace(_broker(), positions=(position,)))
    with pytest.raises(PortfolioSnapshotUnavailable) as raised:
        source.snapshot(observed_at=NOW)
    assert raised.value.reconciliation is not None
    assert PortfolioReconciliationBlocker.UNEXPLAINED_POSITION in raised.value.reconciliation.blockers


def test_fresh_open_buy_reservation_counts_toward_net_exposure(monkeypatch):
    import us_quant.trading.application.portfolio_snapshot as module

    monkeypatch.setattr(
        module,
        "_project_open_orders",
        lambda **_kwargs: (
            PortfolioOpenOrder(
                "strategy-a", "AAPL", PortfolioSide.BUY, Decimal("100"), 10
            ),
        ),
    )
    source, _ = _source()
    snapshot = source.snapshot(observed_at=NOW)
    assert snapshot.gross_exposure == Decimal("100")
    assert snapshot.net_exposure == Decimal("100")
