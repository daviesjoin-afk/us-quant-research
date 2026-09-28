"""Deterministic hard-cap allocation before the existing risk authority."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Iterable

from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioBlocker,
    PortfolioCapitalPolicy,
    PortfolioDecision,
    PortfolioOpenOrder,
    PortfolioOrderAttribution,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    StrategyPortfolioIntent,
    PortfolioVerdict,
    stable_portfolio_decision_id,
)


class CapitalAllocator:
    """Net governed strategy proposals under explicit portfolio ownership.

    This authority only approves or rejects a symbol-level portfolio action. It
    cannot create broker orders, persist them, bypass RiskApplication, or
    authorize Live execution. Any batch-level hard-cap violation rejects the
    whole batch so input order never acts as an implicit priority.
    """

    def allocate(
        self,
        *,
        intents: Iterable[StrategyPortfolioIntent],
        snapshot: PortfolioSnapshot,
        policy: PortfolioCapitalPolicy,
    ) -> tuple[PortfolioDecision, ...]:
        proposals = tuple(intents)
        if not proposals:
            return ()
        if any(not isinstance(item, StrategyPortfolioIntent) for item in proposals):
            raise TypeError("intents must contain only StrategyPortfolioIntent values")
        proposals = tuple(sorted(proposals, key=_intent_key))

        groups: dict[str, list[StrategyPortfolioIntent]] = defaultdict(list)
        for intent in proposals:
            groups[intent.symbol].append(intent)
        ordered_groups = {
            symbol: tuple(sorted(items, key=_intent_key))
            for symbol, items in sorted(groups.items())
        }

        blocker = self._batch_blocker(proposals, snapshot, policy)
        if blocker is None:
            blocker = self._allocation_blocker(proposals, snapshot, policy)
        if blocker is None:
            blocker = self._portfolio_limit_blocker(ordered_groups, snapshot, policy)

        return tuple(
            self._decision(symbol, items, snapshot, blocker)
            for symbol, items in ordered_groups.items()
        )

    @staticmethod
    def _batch_blocker(
        intents: tuple[StrategyPortfolioIntent, ...],
        snapshot: PortfolioSnapshot,
        policy: PortfolioCapitalPolicy,
    ) -> PortfolioBlocker | None:
        if not isinstance(policy, PortfolioCapitalPolicy) or not policy.is_configured:
            return PortfolioBlocker.POLICY_MISSING
        if not isinstance(snapshot, PortfolioSnapshot) or snapshot.observed_at is None:
            return PortfolioBlocker.INVALID_SNAPSHOT
        current_position_notional = sum(
            (
                item.notional
                for item in sorted(snapshot.positions, key=lambda item: item.symbol)
            ),
            Decimal("0"),
        ) + sum(
            (
                item.notional
                for item in sorted(
                    snapshot.open_orders,
                    key=lambda item: (
                        item.strategy_version_id,
                        item.symbol,
                        item.side.value,
                        item.notional,
                    ),
                )
                if item.side is PortfolioSide.BUY
            ),
            Decimal("0"),
        )
        if (
            snapshot.equity <= Decimal("0")
            or snapshot.gross_exposure < current_position_notional
            or sum(
                (
                    item.notional
                    for item in sorted(
                        snapshot.strategy_exposure,
                        key=lambda item: (item.strategy_version_id, item.symbol),
                    )
                ),
                Decimal("0"),
            )
            > snapshot.gross_exposure
        ):
            return PortfolioBlocker.INVALID_SNAPSHOT

        proposal_counts = Counter(item.proposal_id for item in intents)
        if any(count > 1 for count in proposal_counts.values()):
            return PortfolioBlocker.CONFLICTING_INTENT
        for intent in intents:
            allocation = policy.allocation_for(intent.strategy_version_id)
            if allocation is None:
                return PortfolioBlocker.UNKNOWN_STRATEGY
            if not allocation.enabled:
                return PortfolioBlocker.STRATEGY_NOT_ALLOCATED

        for items in CapitalAllocator._group_by_symbol(intents).values():
            if len({item.reference_price for item in items}) != 1:
                return PortfolioBlocker.CONFLICTING_INTENT
        return None

    @staticmethod
    def _group_by_symbol(
        intents: tuple[StrategyPortfolioIntent, ...],
    ) -> dict[str, tuple[StrategyPortfolioIntent, ...]]:
        grouped: dict[str, list[StrategyPortfolioIntent]] = defaultdict(list)
        for item in intents:
            grouped[item.symbol].append(item)
        return {
            symbol: tuple(items)
            for symbol, items in sorted(grouped.items())
        }

    @staticmethod
    def _allocation_blocker(
        intents: tuple[StrategyPortfolioIntent, ...],
        snapshot: PortfolioSnapshot,
        policy: PortfolioCapitalPolicy,
    ) -> PortfolioBlocker | None:
        deltas: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        quantity_deltas: dict[tuple[str, str], int] = defaultdict(int)
        for intent in intents:
            sign = Decimal("1") if intent.side is PortfolioSide.BUY else Decimal("-1")
            quantity_sign = 1 if intent.side is PortfolioSide.BUY else -1
            notional = sign * intent.reference_price * intent.requested_quantity
            deltas[intent.strategy_version_id] += notional
            quantity_deltas[(intent.strategy_version_id, intent.symbol)] += (
                quantity_sign * intent.requested_quantity
            )
        existing: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        existing_quantity_by_symbol: dict[tuple[str, str], int] = defaultdict(int)
        for exposure in sorted(
            snapshot.strategy_exposure,
            key=lambda item: (item.strategy_version_id, item.symbol),
        ):
            existing[exposure.strategy_version_id] += exposure.notional
            existing_quantity_by_symbol[(exposure.strategy_version_id, exposure.symbol)] += (
                exposure.quantity
            )
        reserved_buys: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        reserved_sell_quantities: dict[tuple[str, str], int] = defaultdict(int)
        for order in sorted(
            snapshot.open_orders,
            key=lambda item: (
                item.strategy_version_id,
                item.symbol,
                item.side.value,
                item.notional,
            ),
        ):
            if order.side is PortfolioSide.BUY:
                reserved_buys[order.strategy_version_id] += order.notional
            else:
                reserved_sell_quantities[(order.strategy_version_id, order.symbol)] += (
                    order.quantity
                )
        for strategy_id, delta in deltas.items():
            allocation = policy.allocation_for(strategy_id)
            assert allocation is not None
            ceiling = min(
                allocation.max_capital,
                policy.total_capital_limit * allocation.capital_weight,
                allocation.max_gross_exposure,
                snapshot.equity * policy.max_strategy_concentration,
            )
            held = existing.get(strategy_id, Decimal("0"))
            projected = held + reserved_buys[strategy_id] + delta
            if projected > ceiling:
                return PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED
        for (strategy_id, symbol), quantity_delta in quantity_deltas.items():
            if quantity_delta < 0 and (
                existing_quantity_by_symbol[(strategy_id, symbol)]
                - reserved_sell_quantities[(strategy_id, symbol)]
                + quantity_delta
                < 0
            ):
                return PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED
        return None

    @staticmethod
    def _portfolio_limit_blocker(
        groups: dict[str, tuple[StrategyPortfolioIntent, ...]],
        snapshot: PortfolioSnapshot,
        policy: PortfolioCapitalPolicy,
    ) -> PortfolioBlocker | None:
        held_positions = {item.symbol: item.notional for item in snapshot.positions}
        held_quantities = {item.symbol: item.quantity for item in snapshot.positions}
        positions = dict(held_positions)
        reserved_sell_quantities: dict[str, int] = defaultdict(int)
        for order in sorted(
            snapshot.open_orders,
            key=lambda item: (
                item.strategy_version_id,
                item.symbol,
                item.side.value,
                item.notional,
            ),
        ):
            if order.side is PortfolioSide.BUY:
                positions[order.symbol] = positions.get(order.symbol, Decimal("0")) + order.notional
            else:
                reserved_sell_quantities[order.symbol] += order.quantity
        net_deltas: dict[str, Decimal] = {}
        actions = 0
        for symbol, items in groups.items():
            net_quantity = sum(
                item.requested_quantity
                if item.side is PortfolioSide.BUY
                else -item.requested_quantity
                for item in items
            )
            if net_quantity == 0:
                continue
            actions += 1
            reference_price = items[0].reference_price
            delta = reference_price * net_quantity
            if net_quantity < 0 and -net_quantity > (
                held_quantities.get(symbol, 0) - reserved_sell_quantities[symbol]
            ):
                return PortfolioBlocker.POSITION_LIMIT_EXCEEDED
            projected_symbol = positions.get(symbol, Decimal("0")) + delta
            if projected_symbol < Decimal("0"):
                return PortfolioBlocker.POSITION_LIMIT_EXCEEDED
            if (
                projected_symbol > policy.max_single_position_notional
                or projected_symbol > snapshot.equity * policy.max_symbol_concentration
            ):
                return PortfolioBlocker.SYMBOL_CONCENTRATION_EXCEEDED
            net_deltas[symbol] = delta

        projected_gross = snapshot.gross_exposure + sum(net_deltas.values(), Decimal("0"))
        projected_net = snapshot.net_exposure + sum(net_deltas.values(), Decimal("0"))
        if (
            projected_gross > min(policy.total_capital_limit, policy.max_gross_exposure)
            or abs(projected_net) > policy.max_net_exposure
        ):
            return PortfolioBlocker.CAPITAL_EXCEEDED
        required_cash = sum(
            (
                delta
                for _, delta in sorted(net_deltas.items())
                if delta > Decimal("0")
            ),
            Decimal("0"),
        )
        if required_cash > snapshot.cash:
            return PortfolioBlocker.INSUFFICIENT_CASH
        if len(snapshot.open_orders) + actions > policy.max_open_orders:
            return PortfolioBlocker.POSITION_LIMIT_EXCEEDED

        active_positions = {item.symbol for item in snapshot.positions}
        active_positions.update(
            item.symbol
            for item in snapshot.open_orders
            if item.side is PortfolioSide.BUY
        )
        for symbol, delta in net_deltas.items():
            projected_symbol = positions.get(symbol, Decimal("0")) + delta
            if projected_symbol > Decimal("0"):
                active_positions.add(symbol)
            else:
                active_positions.discard(symbol)
        if len(active_positions) > policy.max_positions:
            return PortfolioBlocker.POSITION_LIMIT_EXCEEDED
        return None

    @staticmethod
    def _decision(
        symbol: str,
        intents: tuple[StrategyPortfolioIntent, ...],
        snapshot: PortfolioSnapshot,
        blocker: PortfolioBlocker | None,
    ) -> PortfolioDecision:
        ordered = tuple(sorted(intents, key=_intent_key))
        decision_id = stable_portfolio_decision_id(
            symbol=symbol,
            proposal_ids=tuple(item.proposal_id for item in ordered),
            observed_at=(snapshot.observed_at if isinstance(snapshot, PortfolioSnapshot) else None),
        )
        attributions = tuple(
            PortfolioOrderAttribution(
                portfolio_decision_id=decision_id,
                strategy_version_id=item.strategy_version_id,
                proposal_id=item.proposal_id,
                symbol=symbol,
                signed_requested_quantity=(
                    item.requested_quantity
                    if item.side is PortfolioSide.BUY
                    else -item.requested_quantity
                ),
            )
            for item in ordered
        )
        net_quantity = sum(item.signed_requested_quantity for item in attributions)
        action = None
        if blocker is None and net_quantity != 0:
            action = PortfolioAction(
                symbol=symbol,
                side=(PortfolioSide.BUY if net_quantity > 0 else PortfolioSide.SELL),
                quantity=abs(net_quantity),
                reference_price=ordered[0].reference_price,
            )
        return PortfolioDecision(
            decision_id=decision_id,
            decision=(
                PortfolioVerdict.REJECT if blocker is not None else PortfolioVerdict.APPROVE
            ),
            symbol=symbol,
            strategy_version_ids=tuple(sorted({item.strategy_version_id for item in ordered})),
            requested_quantity=sum(item.requested_quantity for item in ordered),
            net_quantity=net_quantity,
            blocker=blocker,
            attribution=attributions,
            action=action,
        )


def _intent_key(item: StrategyPortfolioIntent) -> tuple[str, str, str, int, Decimal]:
    return (
        item.strategy_version_id,
        item.proposal_id,
        item.side.value,
        item.requested_quantity,
        item.reference_price,
    )
