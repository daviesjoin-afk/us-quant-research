from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from us_quant.paper_order_models import (
    PaperBrokerOrder,
    PaperOrderConnection,
    PaperReconciliationSnapshot,
    ReconciliationSummary,
)
from us_quant.trading.adapters.ibkr.portfolio_reconciliation_mapping import (
    broker_open_order_truth_from_snapshot,
)
from us_quant.trading.adapters.ibkr.portfolio_reconciliation import (
    IBKRPaperOpenOrderTruthSource,
)
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.application.portfolio_reconciliation import (
    PortfolioReconciliationApplication,
)
from us_quant.trading.application.portfolio_snapshot import _project_open_orders
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioSide,
    PortfolioVerdict,
)
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
    PortfolioExecutionContribution,
    execution_contributions_for_quantity,
)
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrder,
    BrokerOpenOrderTruth,
    PortfolioOrderTruth,
    PortfolioOrderTruthRecord,
    PortfolioReconciliationBlocker as Blocker,
    reconcile_portfolio_truth,
    portfolio_order_truth_for_reconciliation,
)
from us_quant.trading.domain.risk import RiskDecision


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
ACCOUNT_ALIAS = "DU***01"


def broker(*, quantities: dict[str, int], observed_at: datetime = NOW):
    account = BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias=ACCOUNT_ALIAS,
        net_liquidation=Decimal("10000"),
        cash=Decimal("9000"),
        available_funds=Decimal("9000"),
        buying_power=Decimal("18000"),
        gross_position_value=Decimal("1000"),
        excess_liquidity=Decimal("9000"),
        maintenance_margin=Decimal("100"),
        cushion=Decimal("0.9"),
        daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        observed_at=observed_at,
        pnl_source="test",
    )
    positions = tuple(
        BrokerPositionSnapshot(
            account_alias=ACCOUNT_ALIAS,
            con_id=index,
            symbol=symbol,
            local_symbol=symbol,
            security_type="STK",
            exchange="NASDAQ",
            currency="USD",
            quantity=Decimal(quantity),
            average_cost=Decimal("10"),
            market_value=Decimal(quantity * 10),
            daily_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            observed_at=observed_at,
        )
        for index, (symbol, quantity) in enumerate(sorted(quantities.items()), 1)
        if quantity
    )
    return BrokerAccountPortfolio(account=account, positions=positions)


def decision_record(
    decision_id: str,
    contributions: tuple[tuple[str, str, int], ...],
    *,
    order_id: str,
    side: PortfolioSide = PortfolioSide.BUY,
) -> PortfolioDecisionRecord:
    signed = sum(quantity for _, _, quantity in contributions)
    action_side = PortfolioSide.BUY if signed > 0 else PortfolioSide.SELL
    attrs = tuple(
        PortfolioOrderAttribution(
            portfolio_decision_id=decision_id,
            strategy_version_id=strategy,
            proposal_id=proposal,
            symbol="AAPL",
            signed_requested_quantity=quantity,
        )
        for strategy, proposal, quantity in contributions
    )
    decision = PortfolioDecision(
        decision_id=decision_id,
        decision=PortfolioVerdict.APPROVE,
        symbol="AAPL",
        strategy_version_ids=tuple(sorted({strategy for strategy, _, _ in contributions})),
        requested_quantity=sum(abs(quantity) for _, _, quantity in contributions),
        net_quantity=signed,
        blocker=None,
        attribution=attrs,
        action=PortfolioAction(
            symbol="AAPL",
            side=action_side,
            quantity=abs(signed),
            reference_price=Decimal("10"),
        ),
    )
    return PortfolioDecisionRecord(
        decision=decision,
        portfolio_cycle_id=f"cycle-{decision_id}",
        observed_at=NOW,
        policy_identity="policy",
        policy_revision="1",
        created_at=NOW,
        risk_outcome="approved",
        risk_decision=RiskDecision(
            approved=True,
            requested_quantity=abs(signed),
            approved_quantity=abs(signed),
        ),
        order_id=order_id,
    )


def linked_order(
    decision: PortfolioDecisionRecord,
    *,
    fills: tuple[ExecutionFill, ...],
    status: OrderStatus | None = None,
    account_alias: str = ACCOUNT_ALIAS,
) -> tuple[PortfolioOrderTruthRecord, PortfolioExecutionAttribution]:
    action = decision.decision.action
    assert action is not None
    side = Side.BUY if action.side is PortfolioSide.BUY else Side.SELL
    intent = OrderIntent.create(
        session_id="session",
        strategy_version_id="portfolio-runtime",
        signal_symbol=action.symbol,
        execution_symbol=action.symbol,
        side=side,
        quantity=action.quantity,
        limit_price=action.reference_price,
        reason="portfolio action",
    )
    # Use a stable supplied identifier so the durable order and portfolio link agree.
    intent = replace(intent, order_id=decision.order_id, client_order_id=f"uq-{decision.order_id}")
    fill_quantity = sum((item.quantity for item in fills), Decimal("0"))
    events = ()
    if status is not None or fills:
        events = (
            OrderEvent(
                order_id=decision.order_id or "",
                broker_order_id=7,
                status=status or (
                    OrderStatus.FILLED
                    if fill_quantity == Decimal(action.quantity)
                    else OrderStatus.PARTIALLY_FILLED
                ),
                filled=fill_quantity,
                remaining=Decimal(action.quantity) - fill_quantity,
                occurred_at=NOW,
            ),
        )
    execution = PortfolioExecutionAttribution(
        order_id=decision.order_id or "",
        portfolio_decision_id=decision.decision.decision_id,
        symbol=action.symbol,
        side=action.side,
        quantity=action.quantity,
        contributions=tuple(
            PortfolioExecutionContribution(
                portfolio_decision_id=decision.decision.decision_id,
                strategy_version_id=item.strategy_version_id,
                proposal_id=item.proposal_id,
                symbol=item.symbol,
                signed_quantity=item.signed_requested_quantity,
            )
            for item in decision.decision.attribution
        ),
    )
    return (
        PortfolioOrderTruthRecord(
            intent=intent,
            account_alias=account_alias,
            broker_order_id=7,
            events=events,
            fills=fills,
        ),
        execution,
    )


def fill(
    execution_id: str,
    order_id: str,
    quantity: int,
    *,
    side: Side = Side.BUY,
    price: str = "10",
    fee: str | None = "0",
    occurred_at: datetime = NOW,
) -> ExecutionFill:
    return ExecutionFill(
        execution_id=execution_id,
        order_id=order_id,
        broker_order_id=7,
        symbol="AAPL",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        occurred_at=occurred_at,
        fee=Decimal(fee) if fee is not None else None,
    )


def reconcile(
    quantities: dict[str, int],
    *,
    records: tuple[PortfolioDecisionRecord, ...] = (),
    orders: tuple[PortfolioOrderTruthRecord, ...] = (),
    attributions: tuple[PortfolioExecutionAttribution, ...] = (),
    observed_at: datetime = NOW,
    broker_orders: tuple[BrokerOpenOrder, ...] = (),
    broker_orders_complete: bool = True,
    broker_orders_observed_at: datetime = NOW,
):
    return reconcile_portfolio_truth(
        now=NOW,
        broker=broker(quantities=quantities, observed_at=observed_at),
        broker_order_truth=BrokerOpenOrderTruth(
            account_alias=ACCOUNT_ALIAS,
            observed_at=broker_orders_observed_at,
            snapshot_complete=broker_orders_complete,
            open_orders=broker_orders,
        ),
        order_truth=PortfolioOrderTruth(orders),
        decisions=records,
        execution_attributions=attributions,
    )


class _BrokerOrderSource:
    def __init__(self, truth: BrokerOpenOrderTruth):
        self.truth = truth
        self.calls = 0

    def broker_open_order_truth(self) -> BrokerOpenOrderTruth:
        self.calls += 1
        return self.truth


def test_restart_rebuilds_positions_from_durable_portfolio_and_order_facts(tmp_path):
    portfolio_db = tmp_path / "portfolio.sqlite"
    orders_db = tmp_path / "orders.sqlite"
    portfolio_writer = SQLitePortfolioRepository(portfolio_db)
    orders_writer = SQLiteOrderRepository(orders_db)
    record = decision_record("decision-1", (("strategy-a", "p-a", 7), ("strategy-b", "p-b", 3)), order_id="order-1")
    order, attribution = linked_order(record, fills=(fill("exec-1", "order-1", 4),))
    stored = portfolio_writer.record_decision(record)
    assert stored.risk_decision is not None
    portfolio_writer.record_dispatch_outcome(
        replace(
            stored,
            order_id="order-1",
            dispatch_outcome_recorded=True,
            dispatch_submitted=True,
            dispatch_status="submitted",
        ),
        replace(
            attribution,
            contributions=execution_contributions_for_quantity(record.decision, 10),
        ),
        expected_revision=stored.revision,
    )
    orders_writer.record_intent(order.intent, broker_order_id=7, account_alias=ACCOUNT_ALIAS)
    orders_writer.record_event(order.events[0])
    orders_writer.record_fill(order.fills[0])

    # New instances model a process restart: no in-memory holdings are supplied.
    broker_order_source = _BrokerOrderSource(
        BrokerOpenOrderTruth(
            ACCOUNT_ALIAS,
            NOW,
            True,
            (BrokerOpenOrder(7, ACCOUNT_ALIAS, "AAPL", Side.BUY, Decimal("10"), Decimal("6")),),
        )
    )
    recovered = PortfolioReconciliationApplication(
        portfolio_repository=SQLitePortfolioRepository(portfolio_db),
        order_truth=SQLiteOrderRepository(orders_db),
        broker_order_truth=broker_order_source,
    ).reconcile(
        broker=broker(quantities={"AAPL": 4}),
        now=NOW,
    )

    assert recovered.can_open_exposure
    assert broker_order_source.calls == 1
    assert recovered.positions[0].attributed_quantity == 4
    assert [(item.strategy_version_id, item.quantity) for item in recovered.strategy_accounting] == [
        ("strategy-a", 3), ("strategy-b", 1)
    ]


def test_matching_broker_position_passes_and_mismatch_blocks():
    record = decision_record("d", (("a", "pa", 7), ("b", "pb", 3)), order_id="o")
    order, attribution = linked_order(record, fills=(fill("e", "o", 4),))
    matched = reconcile(
        {"AAPL": 4}, records=(record,), orders=(order,), attributions=(attribution,),
        broker_orders=(
            BrokerOpenOrder(7, ACCOUNT_ALIAS, "AAPL", Side.BUY, Decimal("10"), Decimal("6")),
        ),
    )
    mismatched = reconcile({"AAPL": 5}, records=(record,), orders=(order,), attributions=(attribution,))
    assert matched.can_open_exposure
    assert Blocker.ATTRIBUTION_MISMATCH in mismatched.blockers
    assert not mismatched.can_open_exposure


def test_unknown_external_position_blocks_without_assigning_strategy():
    result = reconcile({"AAPL": 5})
    assert Blocker.UNEXPLAINED_POSITION in result.blockers
    assert result.positions[0].attributed_quantity == 0


def test_unknown_broker_open_order_blocks_even_when_local_ledger_is_empty():
    result = reconcile(
        {},
        broker_orders=(
            BrokerOpenOrder(
                broker_order_id=91,
                account_alias=ACCOUNT_ALIAS,
                symbol="AAPL",
                side=Side.BUY,
                quantity=Decimal("100"),
                remaining_quantity=Decimal("100"),
            ),
        ),
    )
    assert Blocker.UNEXPLAINED_ORDER in result.blockers
    assert not result.can_open_exposure


def test_local_and_broker_open_order_truth_must_match_all_identities_and_remaining():
    record = decision_record("d-open", (("a", "pa", 5),), order_id="open-order")
    order, attribution = linked_order(
        record,
        fills=(fill("open-fill", "open-order", 1),),
        status=OrderStatus.PARTIALLY_FILLED,
    )
    matching_broker_order = BrokerOpenOrder(
        broker_order_id=7,
        account_alias=ACCOUNT_ALIAS,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("5"),
        remaining_quantity=Decimal("4"),
    )
    matched = reconcile(
        {"AAPL": 1}, records=(record,), orders=(order,), attributions=(attribution,),
        broker_orders=(matching_broker_order,),
    )
    assert matched.can_open_exposure

    for altered in (
        replace(matching_broker_order, broker_order_id=8),
        replace(matching_broker_order, account_alias="DU***99"),
        replace(matching_broker_order, symbol="MSFT"),
        replace(matching_broker_order, side=Side.SELL),
        replace(matching_broker_order, quantity=Decimal("6")),
        replace(matching_broker_order, remaining_quantity=Decimal("3")),
        replace(matching_broker_order, remaining_quantity=None),
    ):
        result = reconcile(
            {"AAPL": 1}, records=(record,), orders=(order,), attributions=(attribution,),
            broker_orders=(altered,),
        )
        assert Blocker.UNEXPLAINED_ORDER in result.blockers
        assert not result.can_open_exposure


def test_missing_or_incomplete_broker_open_order_snapshot_blocks():
    record = decision_record("d-open", (("a", "pa", 5),), order_id="open-order")
    order, attribution = linked_order(record, fills=(), status=OrderStatus.ACKNOWLEDGED)
    missing = reconcile(
        {}, records=(record,), orders=(order,), attributions=(attribution,),
    )
    incomplete = reconcile({}, broker_orders_complete=False)
    stale = reconcile({}, broker_orders_observed_at=NOW - timedelta(minutes=6))
    for result in (missing, incomplete, stale):
        assert Blocker.UNEXPLAINED_ORDER in result.blockers or Blocker.STALE_SNAPSHOT in result.blockers
        assert not result.can_open_exposure


def test_ibkr_snapshot_projects_external_orders_and_requires_remaining_truth():
    summary = ReconciliationSummary("session", 0, 0, 0, 0, 0, 0, NOW.isoformat())
    complete_snapshot = PaperReconciliationSnapshot(
        account_fingerprint="opaque",
        connection_generation=2,
        reconciliation_generation=4,
        captured_at=NOW.isoformat(),
        broker_positions=(),
        open_broker_orders=(
            PaperBrokerOrder(
                91, "AAPL", "BUY", Decimal("100"), "Submitted", Decimal("60")
            ),
        ),
        completed_broker_orders=(),
        reconciliation_summary=summary,
        state_version=8,
        digest="digest",
    )
    truth = broker_open_order_truth_from_snapshot(
        complete_snapshot, account_alias=ACCOUNT_ALIAS
    )
    assert truth.snapshot_complete
    assert truth.open_orders == (
        BrokerOpenOrder(
            91, ACCOUNT_ALIAS, "AAPL", Side.BUY, Decimal("100"), Decimal("60")
        ),
    )
    assert not reconcile({}, broker_orders=truth.open_orders).can_open_exposure

    missing_remaining = replace(
        complete_snapshot,
        open_broker_orders=(
            replace(complete_snapshot.open_broker_orders[0], remaining_quantity=None),
        ),
    )
    incomplete_truth = broker_open_order_truth_from_snapshot(
        missing_remaining, account_alias=ACCOUNT_ALIAS
    )
    assert not incomplete_truth.snapshot_complete
    result = reconcile(
        {},
        broker_orders=incomplete_truth.open_orders,
        broker_orders_complete=incomplete_truth.snapshot_complete,
    )
    assert Blocker.UNEXPLAINED_ORDER in result.blockers
    assert not result.can_open_exposure


def test_ibkr_order_truth_source_rejects_connection_generation_drift():
    snapshot = PaperReconciliationSnapshot(
        account_fingerprint="opaque",
        connection_generation=2,
        reconciliation_generation=4,
        captured_at=NOW.isoformat(),
        broker_positions=(),
        open_broker_orders=(),
        completed_broker_orders=(),
        reconciliation_summary=ReconciliationSummary(
            "session", 0, 0, 0, 0, 0, 0, NOW.isoformat()
        ),
        state_version=8,
        digest="digest",
    )
    connection = PaperOrderConnection(
        True, ACCOUNT_ALIAS, 176, "connected", 1,
        connection_generation=2, snapshot_complete=True,
    )

    class OrderPort:
        current = True

        def refresh_reconciliation_snapshot(self, session_id):
            assert session_id == "session"
            return snapshot

        def connection_snapshot(self):
            return connection

        def reconciliation_snapshot_is_current(self, value):
            assert value is snapshot
            return self.current

    orders = OrderPort()
    source = IBKRPaperOpenOrderTruthSource(orders=orders, session_id="session")
    assert source.broker_open_order_truth().snapshot_complete
    orders.current = False
    assert not source.broker_open_order_truth().snapshot_complete


def test_opposing_contributor_slippage_uses_signed_strategy_direction():
    buy_seed = decision_record("buy-seed", (("b", "seed", 4),), order_id="seed-b")
    buy_seed_order, buy_seed_link = linked_order(
        buy_seed,
        fills=(fill("seed-fill", "seed-b", 4, price="10"),),
    )
    net_buy = decision_record(
        "net-buy", (("a", "buy-a", 6), ("b", "sell-b", -4)), order_id="net-buy-order"
    )
    buy_fills = (
        fill("buy-fill-2", "net-buy-order", 1, price="11", occurred_at=NOW),
        fill("buy-fill-1", "net-buy-order", 1, price="11", occurred_at=NOW - timedelta(seconds=1)),
    )
    buy_order, buy_link = linked_order(
        net_buy, fills=buy_fills, status=OrderStatus.FILLED
    )
    buy_results = (
        reconcile(
            {"AAPL": 6}, records=(buy_seed, net_buy),
            orders=(buy_seed_order, buy_order),
            attributions=(buy_seed_link, buy_link),
        ),
        reconcile(
            {"AAPL": 6}, records=(net_buy, buy_seed),
            orders=(buy_order, buy_seed_order),
            attributions=(buy_link, buy_seed_link),
        ),
    )
    for result in buy_results:
        slippage = {item.strategy_version_id: item.slippage for item in result.strategy_accounting}
        assert slippage == {"a": Decimal("6"), "b": Decimal("-4")}
    assert buy_results[0].strategy_accounting == buy_results[1].strategy_accounting

    sell_seed = decision_record("sell-seed", (("a", "seed", 6),), order_id="seed-a")
    sell_seed_order, sell_seed_link = linked_order(
        sell_seed,
        fills=(fill("seed-fill-a", "seed-a", 6, price="10"),),
    )
    net_sell = decision_record(
        "net-sell", (("a", "sell-a", -6), ("b", "buy-b", 4)), order_id="net-sell-order"
    )
    sell_order, sell_link = linked_order(
        net_sell,
        fills=(
            fill("sell-fill-2", "net-sell-order", 1, side=Side.SELL, price="9", occurred_at=NOW),
            fill("sell-fill-1", "net-sell-order", 1, side=Side.SELL, price="9", occurred_at=NOW - timedelta(seconds=1)),
        ),
        status=OrderStatus.FILLED,
    )
    sell_result = reconcile(
        {"AAPL": 4}, records=(sell_seed, net_sell),
        orders=(sell_seed_order, sell_order),
        attributions=(sell_seed_link, sell_link),
    )
    sell_slippage = {item.strategy_version_id: item.slippage for item in sell_result.strategy_accounting}
    assert sell_slippage == {"a": Decimal("6"), "b": Decimal("-4")}


def test_completed_unowned_legacy_order_is_scoped_out_but_open_local_order_is_retained():
    legacy_intent = OrderIntent.create(
        session_id="legacy-session",
        strategy_version_id="legacy-strategy",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=1,
        limit_price=Decimal("10"),
        reason="legacy order",
    )
    terminal = PortfolioOrderTruthRecord(
        intent=legacy_intent,
        account_alias=ACCOUNT_ALIAS,
        broker_order_id=19,
        events=(OrderEvent(
            order_id=legacy_intent.order_id,
            broker_order_id=19,
            status=OrderStatus.FILLED,
            filled=Decimal("1"),
            remaining=Decimal("0"),
            occurred_at=NOW,
        ),),
    )
    unresolved = replace(terminal, events=(replace(
        terminal.events[0], status=OrderStatus.UNKNOWN
    ),))
    broker_truth = BrokerOpenOrderTruth(
        account_alias=ACCOUNT_ALIAS,
        observed_at=NOW,
        snapshot_complete=True,
        open_orders=(),
    )

    scoped = portfolio_order_truth_for_reconciliation(
        order_truth=PortfolioOrderTruth((terminal, unresolved)),
        broker_order_truth=broker_truth,
        decisions=(),
        execution_attributions=(),
    )

    assert scoped.orders == (unresolved,)


def test_open_order_reservation_preserves_each_strategy_contribution_side():
    record = decision_record(
        "net-buy-open", (("a", "buy-a", 6), ("b", "sell-b", -4)),
        order_id="net-buy-order",
    )
    local_order, attribution = linked_order(record, fills=())
    broker_truth = BrokerOpenOrderTruth(
        account_alias=ACCOUNT_ALIAS,
        observed_at=NOW,
        snapshot_complete=True,
        open_orders=(BrokerOpenOrder(
            broker_order_id=7,
            account_alias=ACCOUNT_ALIAS,
            symbol="AAPL",
            side=Side.BUY,
            quantity=Decimal("2"),
            remaining_quantity=Decimal("2"),
        ),),
    )

    orders = _project_open_orders(
        broker_truth=broker_truth,
        durable_order_truth=PortfolioOrderTruth((local_order,)),
        decisions=(record,),
        execution_attributions=(attribution,),
    )

    by_strategy = {item.strategy_version_id: item for item in orders}
    assert by_strategy["a"].side is PortfolioSide.BUY
    assert by_strategy["a"].quantity == 6
    assert by_strategy["a"].notional == Decimal("60")
    assert by_strategy["b"].side is PortfolioSide.SELL
    assert by_strategy["b"].quantity == 4
    assert by_strategy["b"].notional == Decimal("0")


def test_unknown_order_and_fill_block():
    order = PortfolioOrderTruthRecord(
        intent=None,
        account_alias=None,
        events=(OrderEvent(order_id="orphan", status=OrderStatus.UNKNOWN),),
        fills=(fill("orphan-fill", "orphan", 1),),
    )
    result = reconcile({}, orders=(order,))
    assert Blocker.UNEXPLAINED_ORDER in result.blockers
    assert Blocker.UNEXPLAINED_FILL in result.blockers


def test_partial_fill_largest_remainder_is_callback_order_independent():
    record = decision_record("d", (("a", "pa", 1), ("b", "pb", 2)), order_id="o")
    first = fill("e1", "o", 1, price="10", occurred_at=NOW - timedelta(seconds=1))
    second = fill("e2", "o", 1, price="20", occurred_at=NOW)
    allocations = []
    for fills in ((first, second), (second, first)):
        order, attribution = linked_order(record, fills=fills)
        result = reconcile({"AAPL": 2}, records=(record,), orders=(order,), attributions=(attribution,))
        allocations.append({
            item.strategy_version_id: (item.quantity, item.average_cost)
            for item in result.strategy_accounting
        })
    expected = {"a": (1, Decimal("20")), "b": (1, Decimal("10"))}
    assert allocations == [expected, expected]


def test_partial_sell_reduces_only_selling_strategy():
    buy_a = decision_record("buy-a", (("a", "ba", 10),), order_id="buy-a-order")
    buy_b = decision_record("buy-b", (("b", "bb", 10),), order_id="buy-b-order")
    sell_a = decision_record("sell-a", (("a", "sa", -6),), order_id="sell-a-order")
    links = [
        linked_order(buy_a, fills=(fill("ea", "buy-a-order", 10),)),
        linked_order(buy_b, fills=(fill("eb", "buy-b-order", 10),)),
        linked_order(sell_a, fills=(fill("es", "sell-a-order", 6, side=Side.SELL, price="12"),)),
    ]
    result = reconcile(
        {"AAPL": 14},
        records=(buy_a, buy_b, sell_a),
        orders=tuple(item[0] for item in links),
        attributions=tuple(item[1] for item in links),
    )
    quantities = {item.strategy_version_id: item.quantity for item in result.strategy_accounting}
    assert quantities == {"a": 4, "b": 10}
    assert result.can_open_exposure


def test_strategy_daily_trade_count_comes_from_durable_close_fills():
    buy = decision_record("daily-buy", (("a", "ba", 4),), order_id="daily-buy-order")
    sell = decision_record("daily-sell", (("a", "sa", -4),), order_id="daily-sell-order")
    buy_order, buy_link = linked_order(
        buy,
        fills=(fill("daily-buy-fill", "daily-buy-order", 4, occurred_at=NOW - timedelta(days=1)),),
    )
    sell_order, sell_link = linked_order(
        sell,
        fills=(fill("daily-sell-fill", "daily-sell-order", 4, side=Side.SELL, occurred_at=NOW),),
    )

    result = reconcile(
        {},
        records=(buy, sell),
        orders=(buy_order, sell_order),
        attributions=(buy_link, sell_link),
    )

    assert result.can_open_exposure
    assert result.strategy_accounting[0].quantity == 0
    assert result.strategy_accounting[0].trades_today == 1


def test_fee_and_realized_pnl_are_deterministic():
    buy = decision_record("buy", (("a", "ba", 7), ("b", "bb", 3)), order_id="buy-o")
    sell = decision_record("sell", (("a", "sa", -1),), order_id="sell-o")
    links = (
        linked_order(buy, fills=(fill("buy-e", "buy-o", 10, fee="1.00"),)),
        linked_order(sell, fills=(fill("sell-e", "sell-o", 1, side=Side.SELL, price="12", fee="0.10"),)),
    )
    result = reconcile(
        {"AAPL": 9},
        records=(buy, sell),
        orders=tuple(item[0] for item in links),
        attributions=tuple(item[1] for item in links),
    )
    accounting = {item.strategy_version_id: item for item in result.strategy_accounting}
    assert accounting["a"].fees == Decimal("0.80")
    assert accounting["b"].fees == Decimal("0.30")
    assert accounting["a"].realized_pnl == Decimal("2")
    assert accounting["a"].fees_complete and accounting["b"].fees_complete


def test_uncertain_execution_and_stale_snapshot_require_reconciliation():
    uncertain = replace(
        decision_record("d", (("a", "pa", 1),), order_id="o"),
        risk_outcome="approved",
        order_id=None,
    )
    uncertain_result = reconcile({}, records=(uncertain,))
    stale_result = reconcile({}, observed_at=NOW - timedelta(minutes=6))
    assert Blocker.PENDING_UNKNOWN_EXECUTION in uncertain_result.blockers
    assert Blocker.STALE_SNAPSHOT in stale_result.blockers


def test_recorded_non_halt_refusal_has_no_unknown_execution():
    refused = replace(
        decision_record("d", (("a", "pa", 1),), order_id="o"),
        order_id=None,
        dispatch_outcome_recorded=True,
        dispatch_submitted=False,
        dispatch_halt=False,
        dispatch_status="explicit refusal before submission",
    )
    result = reconcile({}, records=(refused,))
    assert result.can_open_exposure
    assert Blocker.PENDING_UNKNOWN_EXECUTION not in result.blockers


def test_durable_intent_without_broker_status_blocks_as_unknown_execution():
    record = decision_record("d", (("a", "pa", 1),), order_id="o")
    order, attribution = linked_order(record, fills=())
    result = reconcile({}, records=(record,), orders=(order,), attributions=(attribution,))
    assert Blocker.PENDING_UNKNOWN_EXECUTION in result.blockers
    assert result.open_order_ids == ("o",)
    assert not result.can_open_exposure


def test_local_order_without_portfolio_attribution_blocks():
    record = decision_record("d-no-link", (("a", "pa", 1),), order_id="o-no-link")
    order, _ = linked_order(record, fills=(), status=OrderStatus.CANCELED)
    result = reconcile({}, records=(replace(record, order_id=None),), orders=(order,), attributions=())
    assert Blocker.UNEXPLAINED_ORDER in result.blockers
    assert not result.can_open_exposure


def test_decision_order_reference_without_attribution_blocks():
    record = decision_record("d-missing-link", (("a", "pa", 1),), order_id="o-missing-link")
    result = reconcile({}, records=(record,), orders=(), attributions=())
    assert Blocker.PENDING_UNKNOWN_EXECUTION in result.blockers
    assert not result.can_open_exposure


def test_partial_fill_event_totals_must_match_durable_fills():
    record = decision_record("d-fill-total", (("a", "pa", 2),), order_id="o-fill-total")
    order, attribution = linked_order(record, fills=(fill("e-fill-total", "o-fill-total", 2),))
    bad_event = replace(order.events[0], filled=Decimal(1), remaining=Decimal(1))
    result = reconcile(
        {"AAPL": 2}, records=(record,), orders=(replace(order, events=(bad_event,)),),
        attributions=(attribution,),
    )
    assert Blocker.PENDING_UNKNOWN_EXECUTION in result.blockers
    assert not result.can_open_exposure


def test_callback_with_wrong_broker_order_id_is_unexplained():
    record = decision_record("d", (("a", "pa", 1),), order_id="o")
    order, attribution = linked_order(
        record,
        fills=(fill("e", "o", 1),),
        status=OrderStatus.FILLED,
    )
    bad_event = replace(order.events[0], broker_order_id=8)
    result = reconcile(
        {"AAPL": 1},
        records=(record,),
        orders=(replace(order, events=(bad_event,)),),
        attributions=(attribution,),
    )
    assert Blocker.UNEXPLAINED_ORDER in result.blockers
    assert not result.can_open_exposure


def test_fill_with_wrong_broker_order_id_is_unexplained():
    record = decision_record("d", (("a", "pa", 1),), order_id="o")
    order, attribution = linked_order(
        record,
        fills=(replace(fill("e", "o", 1), broker_order_id=8),),
        status=OrderStatus.FILLED,
    )
    result = reconcile(
        {"AAPL": 1}, records=(record,), orders=(order,), attributions=(attribution,)
    )
    assert Blocker.UNEXPLAINED_FILL in result.blockers
    assert not result.can_open_exposure


def test_fill_history_replays_globally_by_time_not_random_order_id():
    buy = decision_record("buy", (("a", "ba", 6),), order_id="z-buy")
    sell = decision_record("sell", (("a", "sa", -4),), order_id="a-sell")
    buy_order, buy_link = linked_order(
        buy,
        fills=(fill("buy-e", "z-buy", 6, occurred_at=NOW - timedelta(minutes=2)),),
    )
    sell_order, sell_link = linked_order(
        sell,
        fills=(fill("sell-e", "a-sell", 4, side=Side.SELL, price="12", occurred_at=NOW - timedelta(minutes=1)),),
    )
    result = reconcile(
        {"AAPL": 2},
        records=(buy, sell),
        orders=(buy_order, sell_order),
        attributions=(buy_link, sell_link),
    )
    assert result.can_open_exposure
    assert result.strategy_accounting[0].quantity == 2
    assert result.strategy_accounting[0].realized_pnl == Decimal("8")


def test_unknown_fee_is_reported_as_incomplete_evidence():
    record = decision_record("d", (("a", "pa", 1),), order_id="o")
    order, attribution = linked_order(record, fills=(fill("e", "o", 1, fee=None),))
    result = reconcile({"AAPL": 1}, records=(record,), orders=(order,), attributions=(attribution,))
    assert result.strategy_accounting[0].fees == Decimal("0")
    assert result.strategy_accounting[0].fees_complete is False


def test_sqlite_order_repository_round_trips_fill_fee(tmp_path):
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite")
    intent = OrderIntent.create(
        session_id="session", strategy_version_id="a", signal_symbol="AAPL",
        execution_symbol="AAPL", side=Side.BUY, quantity=2,
        limit_price=Decimal("10"), reason="test",
    )
    repository.record_intent(intent, broker_order_id=7, account_alias=ACCOUNT_ALIAS)
    recorded = fill("exec", intent.order_id, 1, fee="0.25")
    assert repository.record_fill(recorded)
    assert repository.fills(intent.order_id)[0].fee == Decimal("0.25")


def test_existing_order_database_grows_fee_column_without_losing_fill(tmp_path):
    path = tmp_path / "pre-fee-orders.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE paper_execution(
                execution_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL,
                broker_order_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO paper_execution(
                execution_id, intent_id, broker_order_id, symbol, side,
                quantity, price, occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old-exec", "old-order", 9, "AAPL", "BUY", "1", "10",
             NOW.isoformat(), NOW.isoformat()),
        )
    repository = SQLiteOrderRepository(path)
    loaded = repository.fills("old-order")
    assert len(loaded) == 1
    assert loaded[0].execution_id == "old-exec"
    assert loaded[0].fee is None
