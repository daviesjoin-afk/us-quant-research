"""Broker, durable order and portfolio attribution reconciliation values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
)


class PortfolioReconciliationBlocker(StrEnum):
    UNEXPLAINED_POSITION = "unexplained_position"
    UNEXPLAINED_ORDER = "unexplained_order"
    UNEXPLAINED_FILL = "unexplained_fill"
    ATTRIBUTION_MISMATCH = "attribution_mismatch"
    PENDING_UNKNOWN_EXECUTION = "pending_unknown_execution"
    STALE_SNAPSHOT = "stale_snapshot"
    MISSING_PORTFOLIO_DECISION = "missing_portfolio_decision"
    DUPLICATE_EXECUTION_LINK = "duplicate_execution_link"


@dataclass(frozen=True, slots=True)
class PortfolioOrderTruthRecord:
    """One durable order and its stored observations, scoped to a masked account."""

    intent: OrderIntent | None
    account_alias: str | None
    broker_order_id: int | None = None
    events: tuple[OrderEvent, ...] = ()
    fills: tuple[ExecutionFill, ...] = ()


@dataclass(frozen=True, slots=True)
class PortfolioOrderTruth:
    orders: tuple[PortfolioOrderTruthRecord, ...]


@dataclass(frozen=True, slots=True)
class BrokerOpenOrder:
    """Provider-neutral broker observation for one currently open order."""

    broker_order_id: int
    account_alias: str
    symbol: str
    side: Side
    quantity: Decimal
    remaining_quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class BrokerOpenOrderTruth:
    """A complete, timestamped observation of the broker's open-order set."""

    account_alias: str
    observed_at: datetime
    snapshot_complete: bool
    open_orders: tuple[BrokerOpenOrder, ...]


@dataclass(frozen=True, slots=True)
class PortfolioReconciledPosition:
    symbol: str
    broker_quantity: Decimal
    attributed_quantity: int


@dataclass(frozen=True, slots=True)
class PortfolioStrategyAccounting:
    strategy_version_id: str
    symbol: str
    quantity: int
    average_cost: Decimal | None
    filled_quantity: int
    realized_pnl: Decimal
    fees: Decimal
    fees_complete: bool
    slippage: Decimal
    slippage_complete: bool


@dataclass(frozen=True, slots=True)
class PortfolioReconciliationResult:
    observed_at: datetime
    blockers: tuple[PortfolioReconciliationBlocker, ...]
    positions: tuple[PortfolioReconciledPosition, ...]
    strategy_accounting: tuple[PortfolioStrategyAccounting, ...]
    open_order_ids: tuple[str, ...]

    @property
    def can_open_exposure(self) -> bool:
        """Only a fresh, fully explained account can authorize new exposure."""

        return not self.blockers


@dataclass(slots=True)
class _MutableAccounting:
    quantity: int = 0
    cost_basis: Decimal = Decimal("0")
    filled_quantity: int = 0
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    fees_complete: bool = True
    slippage: Decimal = Decimal("0")
    slippage_complete: bool = True


def reconcile_portfolio_truth(
    *,
    now: datetime,
    broker: BrokerAccountPortfolio,
    broker_order_truth: BrokerOpenOrderTruth,
    order_truth: PortfolioOrderTruth,
    decisions: tuple[PortfolioDecisionRecord, ...],
    execution_attributions: tuple[PortfolioExecutionAttribution, ...],
    max_snapshot_age: timedelta = timedelta(minutes=5),
) -> PortfolioReconciliationResult:
    """Rebuild position ownership and accounting from durable broker/order facts."""

    if not _aware(now) or not isinstance(broker, BrokerAccountPortfolio):
        raise ValueError("reconciliation requires an aware time and broker portfolio")
    if not isinstance(order_truth, PortfolioOrderTruth):
        raise TypeError("order_truth must be PortfolioOrderTruth")
    if not isinstance(broker_order_truth, BrokerOpenOrderTruth):
        raise TypeError("broker_order_truth must be BrokerOpenOrderTruth")
    if max_snapshot_age <= timedelta(0):
        raise ValueError("max_snapshot_age must be positive")

    blockers: set[PortfolioReconciliationBlocker] = set()
    _check_freshness(now, broker, max_snapshot_age, blockers)
    _check_open_order_freshness(
        now, broker, broker_order_truth, max_snapshot_age, blockers
    )
    decision_by_id = {item.decision.decision_id: item for item in decisions}
    if len(decision_by_id) != len(decisions):
        blockers.add(PortfolioReconciliationBlocker.MISSING_PORTFOLIO_DECISION)
    attribution_by_order: dict[str, PortfolioExecutionAttribution] = {}
    decision_links: dict[str, list[str]] = {}
    for item in execution_attributions:
        if item.order_id in attribution_by_order:
            blockers.add(PortfolioReconciliationBlocker.DUPLICATE_EXECUTION_LINK)
            continue
        attribution_by_order[item.order_id] = item
        decision_links.setdefault(item.portfolio_decision_id, []).append(item.order_id)
    if any(len(order_ids) > 1 for order_ids in decision_links.values()):
        blockers.add(PortfolioReconciliationBlocker.DUPLICATE_EXECUTION_LINK)
    for item in execution_attributions:
        if item.portfolio_decision_id not in decision_by_id:
            blockers.add(PortfolioReconciliationBlocker.MISSING_PORTFOLIO_DECISION)

    order_by_id: dict[str, PortfolioOrderTruthRecord] = {}
    for order in order_truth.orders:
        if order.intent is None:
            if order.fills:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_FILL)
            if order.events:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
            continue
        if order.intent.order_id in order_by_id:
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
            continue
        if order.account_alias != broker.account.account_alias:
            continue
        order_by_id[order.intent.order_id] = order
        if order.intent.order_id not in attribution_by_order:
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)

    for order_id in attribution_by_order:
        if order_id not in order_by_id:
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)

    broker_open_by_id: dict[int, BrokerOpenOrder] = {}
    for broker_order in broker_order_truth.open_orders:
        if (
            not _valid_broker_open_order(broker_order)
            or broker_order.broker_order_id in broker_open_by_id
            or broker_order_truth.account_alias != broker.account.account_alias
        ):
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
            continue
        broker_open_by_id[broker_order.broker_order_id] = broker_order

    local_open_by_broker_id: dict[
        int, tuple[str, PortfolioOrderTruthRecord, OrderEvent | None]
    ] = {}
    for local_order_id, local_order in order_by_id.items():
        local_event = _latest_event(local_order.events)
        if local_event is None or not local_event.status.is_terminal:
            if local_order.broker_order_id is None:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
                continue
            if local_order.broker_order_id in local_open_by_broker_id:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
                continue
            local_open_by_broker_id[local_order.broker_order_id] = (
                local_order_id,
                local_order,
                local_event,
            )
    if broker_order_truth.snapshot_complete:
        if set(broker_open_by_id) != set(local_open_by_broker_id):
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
        for broker_id in broker_open_by_id.keys() & local_open_by_broker_id.keys():
            broker_order = broker_open_by_id[broker_id]
            local_order_id, local_order, local_event = local_open_by_broker_id[
                broker_id
            ]
            intent = local_order.intent
            if (
                intent is None
                or local_order.account_alias != broker_order.account_alias
                or local_order.broker_order_id != broker_id
                or intent.execution_symbol != broker_order.symbol.strip().upper()
                or intent.side is not broker_order.side
                or Decimal(intent.quantity) != broker_order.quantity
                or local_event is None
                or local_event.broker_order_id != broker_id
                or local_event.remaining != broker_order.remaining_quantity
            ):
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
    else:
        blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
    for record in decisions:
        if record.order_id is not None and record.order_id not in attribution_by_order:
            blockers.add(PortfolioReconciliationBlocker.PENDING_UNKNOWN_EXECUTION)

    accounting: dict[tuple[str, str], _MutableAccounting] = {}
    for record in decisions:
        if (
            record.risk_outcome == "approved"
            and record.order_id is None
            and (
                not record.dispatch_outcome_recorded
                or record.dispatch_submitted
                or record.dispatch_halt
            )
        ):
            blockers.add(PortfolioReconciliationBlocker.PENDING_UNKNOWN_EXECUTION)

    seen_execution_ids: set[str] = set()
    replay_batches = []
    replay_state = {}
    for order_id in sorted(order_by_id):
        order = order_by_id[order_id]
        intent = order.intent
        assert intent is not None
        attribution = attribution_by_order.get(order_id)
        fills = order.fills
        event = _latest_event(order.events)
        if event is None or event.status is OrderStatus.UNKNOWN:
            blockers.add(PortfolioReconciliationBlocker.PENDING_UNKNOWN_EXECUTION)
        if event is not None and event.broker_order_id != order.broker_order_id:
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
        if attribution is None:
            if fills:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_FILL)
            if event is not None and not event.status.is_terminal:
                blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_ORDER)
            continue
        decision_record = decision_by_id.get(attribution.portfolio_decision_id)
        if decision_record is None:
            blockers.add(PortfolioReconciliationBlocker.MISSING_PORTFOLIO_DECISION)
            continue
        decision = decision_record.decision
        action = decision.action
        if (
            action is None
            or decision_record.order_id != order_id
            or decision_record.risk_outcome != "approved"
            or decision_record.risk_decision is None
            or decision_record.risk_decision.approved_quantity != attribution.quantity
            or intent.execution_symbol != attribution.symbol
            or intent.side.value != attribution.side.value
            or intent.quantity != attribution.quantity
            or order.broker_order_id is None
            or order.broker_order_id <= 0
            or action.symbol != attribution.symbol
            or action.side.value != attribution.side.value
        ):
            blockers.add(PortfolioReconciliationBlocker.ATTRIBUTION_MISMATCH)
            continue
        filled_total = sum((fill.quantity for fill in fills), Decimal("0"))
        if (
            any(
                fill.order_id != order_id
                or not isinstance(fill.symbol, str)
                or not fill.symbol.strip()
                or not isinstance(fill.quantity, Decimal)
                or fill.symbol.strip().upper() != attribution.symbol
                or fill.side is not intent.side
                or fill.broker_order_id != order.broker_order_id
                or not fill.quantity.is_finite()
                or fill.quantity <= 0
                or fill.quantity != fill.quantity.to_integral_value()
                or not isinstance(fill.price, Decimal)
                or not fill.price.is_finite()
                or fill.price <= 0
                for fill in fills
            )
            or filled_total > Decimal(attribution.quantity)
        ):
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_FILL)
            continue
        if event is not None and (
            event.filled != filled_total
            or event.remaining != Decimal(intent.quantity) - filled_total
            or (event.status is OrderStatus.FILLED and filled_total != Decimal(intent.quantity))
        ):
            blockers.add(PortfolioReconciliationBlocker.PENDING_UNKNOWN_EXECUTION)
        if decision_record.dispatch_halt and (
            event is None
            or not event.status.is_terminal
            or event.filled != filled_total
        ):
            blockers.add(PortfolioReconciliationBlocker.PENDING_UNKNOWN_EXECUTION)
        if len({fill.execution_id for fill in fills}) != len(fills):
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_FILL)
            continue
        if any(fill.execution_id in seen_execution_ids for fill in fills):
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_FILL)
            continue
        seen_execution_ids.update(fill.execution_id for fill in fills)
        contribution_by_key = {
            (item.strategy_version_id, item.proposal_id): item.signed_quantity
            for item in attribution.contributions
        }
        replay_state[order_id] = (
            attribution,
            action,
            intent,
            0,
            {key: 0 for key in contribution_by_key},
        )
        replay_batches.extend((fill.occurred_at, fill.execution_id, order_id, fill) for fill in fills)

    # A sell's strategy ownership depends on earlier fills from every order,
    # not on UUID order. Replay the complete durable fill stream globally.
    for _occurred_at, _execution_id, order_id, fill in sorted(replay_batches):
        attribution, action, intent, cumulative_fill, prior_allocations = replay_state[order_id]
        cumulative_fill += int(fill.quantity)
        signed_total = cumulative_fill if intent.side.value == "buy" else -cumulative_fill
        current_allocations = _scale_contributions(
            attribution.contributions,
            signed_target=signed_total,
            source_total=(
                attribution.quantity
                if attribution.side.value == "buy"
                else -attribution.quantity
            ),
        )
        deltas = {
            key: current_allocations.get(key, 0) - prior_allocations.get(key, 0)
            for key in prior_allocations.keys() | current_allocations.keys()
        }
        replay_state[order_id] = (
            attribution,
            action,
            intent,
            cumulative_fill,
            current_allocations,
        )
        _apply_fill(
            accounting=accounting,
            fill=fill,
            deltas=deltas,
            reference_price=action.reference_price,
            blockers=blockers,
        )

    broker_quantities: dict[str, Decimal] = {}
    for position in broker.positions:
        if (
            not isinstance(position.quantity, Decimal)
            or not position.quantity.is_finite()
            or position.quantity < 0
            or not isinstance(position.symbol, str)
            or not position.symbol.strip()
        ):
            blockers.add(PortfolioReconciliationBlocker.ATTRIBUTION_MISMATCH)
            continue
        symbol = position.symbol.strip().upper()
        if position.account_alias != broker.account.account_alias:
            blockers.add(PortfolioReconciliationBlocker.ATTRIBUTION_MISMATCH)
        broker_quantities[symbol] = broker_quantities.get(symbol, Decimal("0")) + position.quantity
    strategy_quantities: dict[str, int] = {}
    for (_, symbol), item in accounting.items():
        strategy_quantities[symbol] = strategy_quantities.get(symbol, 0) + item.quantity
    all_symbols = sorted(set(broker_quantities) | set(strategy_quantities))
    reconciled_positions = []
    for symbol in all_symbols:
        broker_quantity = broker_quantities.get(symbol, Decimal("0"))
        attributed_quantity = strategy_quantities.get(symbol, 0)
        if symbol in broker_quantities and symbol not in strategy_quantities and broker_quantity != 0:
            blockers.add(PortfolioReconciliationBlocker.UNEXPLAINED_POSITION)
        elif broker_quantity != Decimal(attributed_quantity):
            blockers.add(PortfolioReconciliationBlocker.ATTRIBUTION_MISMATCH)
        reconciled_positions.append(
            PortfolioReconciledPosition(symbol, broker_quantity, attributed_quantity)
        )

    open_order_ids = tuple(
        sorted(
            order_id
            for order_id, order in order_by_id.items()
            if (event := _latest_event(order.events)) is None or not event.status.is_terminal
        )
    )
    strategy_results = tuple(
        PortfolioStrategyAccounting(
            strategy_version_id=strategy_id,
            symbol=symbol,
            quantity=item.quantity,
            average_cost=(item.cost_basis / item.quantity if item.quantity > 0 else None),
            filled_quantity=item.filled_quantity,
            realized_pnl=item.realized_pnl,
            fees=item.fees,
            fees_complete=item.fees_complete,
            slippage=item.slippage,
            slippage_complete=item.slippage_complete,
        )
        for (strategy_id, symbol), item in sorted(accounting.items())
    )
    return PortfolioReconciliationResult(
        observed_at=now,
        blockers=tuple(sorted(blockers, key=lambda item: item.value)),
        positions=tuple(reconciled_positions),
        strategy_accounting=strategy_results,
        open_order_ids=open_order_ids,
    )


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _valid_broker_open_order(order: BrokerOpenOrder) -> bool:
    if not isinstance(order, BrokerOpenOrder):
        return False
    remaining = order.remaining_quantity
    return (
        isinstance(order.broker_order_id, int)
        and not isinstance(order.broker_order_id, bool)
        and order.broker_order_id > 0
        and isinstance(order.account_alias, str)
        and bool(order.account_alias.strip())
        and isinstance(order.symbol, str)
        and bool(order.symbol.strip())
        and isinstance(order.side, Side)
        and isinstance(order.quantity, Decimal)
        and order.quantity.is_finite()
        and order.quantity > 0
        and isinstance(remaining, Decimal)
        and remaining.is_finite()
        and remaining > 0
        and remaining <= order.quantity
    )


def _check_freshness(
    now: datetime,
    broker: BrokerAccountPortfolio,
    max_age: timedelta,
    blockers: set[PortfolioReconciliationBlocker],
) -> None:
    timestamps = (broker.account.observed_at, *(item.observed_at for item in broker.positions))
    if any(not _aware(value) or value > now or now - value > max_age for value in timestamps):
        blockers.add(PortfolioReconciliationBlocker.STALE_SNAPSHOT)


def _check_open_order_freshness(
    now: datetime,
    broker: BrokerAccountPortfolio,
    truth: BrokerOpenOrderTruth,
    max_age: timedelta,
    blockers: set[PortfolioReconciliationBlocker],
) -> None:
    if (
        not _aware(truth.observed_at)
        or truth.observed_at > now
        or now - truth.observed_at > max_age
        or truth.account_alias != broker.account.account_alias
        or not truth.snapshot_complete
    ):
        blockers.add(PortfolioReconciliationBlocker.STALE_SNAPSHOT)


def _latest_event(events: tuple[OrderEvent, ...]) -> OrderEvent | None:
    if not events:
        return None
    ordered = sorted(events, key=lambda item: (item.occurred_at, item.status.value, item.filled, item.remaining))
    latest_at = ordered[-1].occurred_at
    latest = tuple(item for item in ordered if item.occurred_at == latest_at)
    latest_facts = {
        (
            item.status,
            item.broker_order_id,
            item.filled,
            item.remaining,
            item.average_fill_price,
            item.last_fill_price,
        )
        for item in latest
    }
    if len(latest_facts) > 1:
        return OrderEvent(
            order_id=ordered[-1].order_id,
            broker_order_id=ordered[-1].broker_order_id,
            status=OrderStatus.UNKNOWN,
            occurred_at=latest_at,
        )
    return ordered[-1]


def _scale_contributions(
    contributions,
    *,
    signed_target: int,
    source_total: int,
) -> dict[tuple[str, str], int]:
    if source_total == 0 or signed_target == 0:
        return {}
    denominator = abs(source_total)
    allocated = []
    for item in sorted(
        contributions,
        key=lambda entry: (entry.strategy_version_id, entry.proposal_id),
    ):
        base, remainder = divmod(item.signed_quantity * abs(signed_target), denominator)
        allocated.append([item, base, remainder])
    remaining = signed_target - sum(entry[1] for entry in allocated)
    for entry in sorted(
        allocated,
        key=lambda value: (-value[2], value[0].strategy_version_id, value[0].proposal_id),
    )[:remaining]:
        entry[1] += 1
    return {
        (item.strategy_version_id, item.proposal_id): quantity
        for item, quantity, _ in allocated
        if quantity
    }


def _apply_fill(
    *,
    accounting: dict[tuple[str, str], _MutableAccounting],
    fill: ExecutionFill,
    deltas: dict[tuple[str, str], int],
    reference_price: Decimal,
    blockers: set[PortfolioReconciliationBlocker],
) -> None:
    shares_by_strategy: dict[str, int] = {}
    for (strategy_id, _proposal_id), signed_quantity in deltas.items():
        if signed_quantity == 0:
            continue
        key = (strategy_id, fill.symbol.strip().upper())
        item = accounting.setdefault(key, _MutableAccounting())
        item.filled_quantity += abs(signed_quantity)
        if signed_quantity > 0:
            item.quantity += signed_quantity
            item.cost_basis += fill.price * signed_quantity
        else:
            sold = -signed_quantity
            if sold > item.quantity:
                blockers.add(PortfolioReconciliationBlocker.ATTRIBUTION_MISMATCH)
            else:
                average_cost = item.cost_basis / item.quantity if item.quantity else Decimal("0")
                item.realized_pnl += (fill.price - average_cost) * sold
                item.cost_basis -= average_cost * sold
                item.quantity -= sold
        shares_by_strategy[strategy_id] = (
            shares_by_strategy.get(strategy_id, 0) + abs(signed_quantity)
        )
        contribution_sign = 1 if signed_quantity > 0 else -1
        item.slippage += (
            (fill.price - reference_price)
            * contribution_sign
            * abs(signed_quantity)
        )

    if fill.fee is None:
        for strategy_id in shares_by_strategy:
            accounting[(strategy_id, fill.symbol.strip().upper())].fees_complete = False
    elif shares_by_strategy:
        fee_allocations = _allocate_decimal(
            fill.fee,
            shares_by_strategy,
        )
        for strategy_id, fee in fee_allocations.items():
            accounting[(strategy_id, fill.symbol.strip().upper())].fees += fee


def _allocate_decimal(total: Decimal, weights: dict[str, int]) -> dict[str, Decimal]:
    """Allocate a fee pro rata, assigning Decimal rounding residue stably."""
    denominator = sum(weights.values())
    if denominator <= 0:
        return {}
    keys = sorted(weights)
    result: dict[str, Decimal] = {}
    allocated = Decimal("0")
    for strategy_id in keys[:-1]:
        value = total * Decimal(weights[strategy_id]) / Decimal(denominator)
        result[strategy_id] = value
        allocated += value
    result[keys[-1]] = total - allocated
    return result
