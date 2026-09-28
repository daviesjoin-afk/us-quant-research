"""Immutable portfolio allocation values for deterministic pre-risk decisions.

This module is deliberately independent of strategies, adapters, persistence,
the broker, Qt, and the execution application.  Strategy proposals remain
proposals; a portfolio action is only a netted candidate for later risk review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from us_quant.trading.domain.common import ZERO


class PortfolioError(ValueError):
    """Base error for malformed portfolio policy and fact values."""


class PortfolioSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PortfolioVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class PortfolioBlocker(StrEnum):
    POLICY_MISSING = "portfolio_policy_missing"
    CAPITAL_EXCEEDED = "portfolio_capital_exceeded"
    STRATEGY_NOT_ALLOCATED = "strategy_not_allocated"
    STRATEGY_ALLOCATION_EXCEEDED = "strategy_allocation_exceeded"
    SYMBOL_CONCENTRATION_EXCEEDED = "symbol_concentration_exceeded"
    POSITION_LIMIT_EXCEEDED = "position_limit_exceeded"
    INVALID_SNAPSHOT = "invalid_snapshot"
    CONFLICTING_INTENT = "conflicting_intent"
    UNKNOWN_STRATEGY = "unknown_strategy"
    INSUFFICIENT_CASH = "insufficient_cash"


def _decimal(value: Decimal, name: str, *, allow_zero: bool = True) -> None:
    if not isinstance(value, Decimal):
        raise PortfolioError(f"{name} must be Decimal")
    if not value.is_finite() or value < ZERO or (not allow_zero and value == ZERO):
        raise PortfolioError(f"{name} must be finite and non-negative")


def _signed_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise PortfolioError(f"{name} must be a finite Decimal")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioError(f"{name} must be a nonblank string")


def _symbol(value: str) -> str:
    """Return the single canonical identity used for portfolio symbols."""

    _text(value, "symbol")
    return value.strip().upper()


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PortfolioError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PortfolioStrategyAllocation:
    """Explicit capital ownership for one already-governed strategy version."""

    strategy_version_id: str
    capital_weight: Decimal = ZERO
    max_capital: Decimal = ZERO
    max_gross_exposure: Decimal = ZERO
    enabled: bool = False

    def __post_init__(self) -> None:
        _text(self.strategy_version_id, "strategy_version_id")
        for name in ("capital_weight", "max_capital", "max_gross_exposure"):
            _decimal(getattr(self, name), name)
        if self.capital_weight > Decimal("1"):
            raise PortfolioError("capital_weight must be at most 1")
        if type(self.enabled) is not bool:
            raise PortfolioError("enabled must be bool")


@dataclass(frozen=True, slots=True)
class PortfolioCapitalPolicy:
    """Hard, deterministic portfolio ceilings; zero defaults authorize nothing."""

    total_capital_limit: Decimal = ZERO
    max_gross_exposure: Decimal = ZERO
    max_net_exposure: Decimal = ZERO
    max_single_position_notional: Decimal = ZERO
    max_symbol_concentration: Decimal = ZERO
    max_strategy_concentration: Decimal = ZERO
    max_positions: int = 0
    max_open_orders: int = 0
    allocations: tuple[PortfolioStrategyAllocation, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "total_capital_limit",
            "max_gross_exposure",
            "max_net_exposure",
            "max_single_position_notional",
            "max_symbol_concentration",
            "max_strategy_concentration",
        ):
            _decimal(getattr(self, name), name)
        for name in ("max_symbol_concentration", "max_strategy_concentration"):
            if getattr(self, name) > Decimal("1"):
                raise PortfolioError(f"{name} must be at most 1")
        for name in ("max_positions", "max_open_orders"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PortfolioError(f"{name} must be a non-negative integer")
        if not isinstance(self.allocations, tuple) or any(
            not isinstance(item, PortfolioStrategyAllocation)
            for item in self.allocations
        ):
            raise PortfolioError("allocations must be a tuple of allocations")
        strategy_ids = tuple(item.strategy_version_id for item in self.allocations)
        if len(set(strategy_ids)) != len(strategy_ids):
            raise PortfolioError("strategy allocations must be unique")
        ordered_allocations = sorted(self.allocations, key=lambda item: item.strategy_version_id)
        if sum((item.capital_weight for item in ordered_allocations), ZERO) > Decimal("1"):
            raise PortfolioError("strategy capital weights exceed 1")
        if sum((item.max_capital for item in ordered_allocations), ZERO) > self.total_capital_limit:
            raise PortfolioError("strategy capital allocations exceed portfolio budget")

    @property
    def is_configured(self) -> bool:
        """Whether every required hard ceiling can authorize any exposure."""

        positive_limits = (
            self.total_capital_limit,
            self.max_gross_exposure,
            self.max_net_exposure,
            self.max_single_position_notional,
            self.max_symbol_concentration,
            self.max_strategy_concentration,
        )
        return (
            all(value > ZERO for value in positive_limits)
            and self.max_positions > 0
            and self.max_open_orders > 0
            and any(
                allocation.enabled
                and allocation.capital_weight > ZERO
                and allocation.max_capital > ZERO
                and allocation.max_gross_exposure > ZERO
                for allocation in self.allocations
            )
        )

    def allocation_for(self, strategy_version_id: str) -> PortfolioStrategyAllocation | None:
        return next(
            (
                item
                for item in self.allocations
                if item.strategy_version_id == strategy_version_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    """One account-level position; ownership is symbol-level, not per strategy."""

    symbol: str
    quantity: int
    notional: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if type(self.quantity) is not int or self.quantity <= 0:
            raise PortfolioError("position quantity must be a positive integer")
        _decimal(self.notional, "position notional", allow_zero=False)


@dataclass(frozen=True, slots=True)
class PortfolioSymbolExposure:
    symbol: str
    notional: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _decimal(self.notional, "symbol exposure")


@dataclass(frozen=True, slots=True)
class PortfolioOpenOrder:
    """An existing portfolio-owned open-order reservation, without broker fields."""

    strategy_version_id: str
    symbol: str
    side: PortfolioSide
    notional: Decimal
    quantity: int

    def __post_init__(self) -> None:
        _text(self.strategy_version_id, "strategy_version_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.side, PortfolioSide):
            raise PortfolioError("open-order side must be PortfolioSide")
        _decimal(self.notional, "open-order notional")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise PortfolioError("open-order quantity must be a positive integer")


@dataclass(frozen=True, slots=True)
class PortfolioStrategyExposure:
    strategy_version_id: str
    symbol: str
    notional: Decimal
    quantity: int

    def __post_init__(self) -> None:
        _text(self.strategy_version_id, "strategy_version_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _decimal(self.notional, "strategy exposure")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise PortfolioError("strategy exposure quantity must be a positive integer")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Frozen account and attribution facts observed at one aware timestamp."""

    cash: Decimal = ZERO
    equity: Decimal = ZERO
    gross_exposure: Decimal = ZERO
    net_exposure: Decimal = ZERO
    positions: tuple[PortfolioPosition, ...] = ()
    open_orders: tuple[PortfolioOpenOrder, ...] = ()
    strategy_exposure: tuple[PortfolioStrategyExposure, ...] = ()
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("cash", "equity", "gross_exposure"):
            _decimal(getattr(self, name), name)
        _signed_decimal(self.net_exposure, "net_exposure")
        if not isinstance(self.positions, tuple) or any(
            not isinstance(item, PortfolioPosition) for item in self.positions
        ):
            raise PortfolioError("positions must be a tuple of PortfolioPosition")
        if not isinstance(self.open_orders, tuple) or any(
            not isinstance(item, PortfolioOpenOrder) for item in self.open_orders
        ):
            raise PortfolioError("open_orders must be a tuple of PortfolioOpenOrder")
        if not isinstance(self.strategy_exposure, tuple) or any(
            not isinstance(item, PortfolioStrategyExposure)
            for item in self.strategy_exposure
        ):
            raise PortfolioError(
                "strategy_exposure must be a tuple of PortfolioStrategyExposure"
            )
        symbols = tuple(item.symbol for item in self.positions)
        if len(set(symbols)) != len(symbols):
            raise PortfolioError("positions must be unique by symbol")
        strategy_symbols = tuple(
            (item.strategy_version_id, item.symbol)
            for item in self.strategy_exposure
        )
        if len(set(strategy_symbols)) != len(strategy_symbols):
            raise PortfolioError("strategy exposure must be unique by strategy and symbol")
        recorded_exposure = sum(
            (item.notional for item in sorted(self.positions, key=lambda item: item.symbol)),
            ZERO,
        ) + sum(
            (
                item.notional
                for item in sorted(
                    self.open_orders,
                    key=lambda item: (item.strategy_version_id, item.symbol, item.side.value),
                )
                if item.side is PortfolioSide.BUY
            ),
            ZERO,
        )
        if self.gross_exposure < recorded_exposure:
            raise PortfolioError("gross exposure cannot be below positions and open buys")
        if abs(self.net_exposure) > self.gross_exposure:
            raise PortfolioError("absolute net exposure cannot exceed gross exposure")
        if self.observed_at is not None:
            _aware(self.observed_at, "observed_at")

    @property
    def symbol_exposure(self) -> tuple[PortfolioSymbolExposure, ...]:
        """Account exposure aggregated at the real, symbol-level position."""
        exposures = {item.symbol: item.notional for item in self.positions}
        for order in sorted(
            self.open_orders,
            key=lambda item: (item.strategy_version_id, item.symbol, item.side.value),
        ):
            if order.side is PortfolioSide.BUY:
                exposures[order.symbol] = exposures.get(order.symbol, ZERO) + order.notional
        return tuple(
            PortfolioSymbolExposure(symbol, exposures[symbol])
            for symbol in sorted(exposures)
        )


@dataclass(frozen=True, slots=True)
class StrategyPortfolioIntent:
    """A strategy proposal before it becomes an order or execution instruction."""

    strategy_version_id: str
    symbol: str
    side: PortfolioSide
    requested_quantity: int
    reference_price: Decimal
    proposal_id: str

    def __post_init__(self) -> None:
        _text(self.strategy_version_id, "strategy_version_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _text(self.proposal_id, "proposal_id")
        if not isinstance(self.side, PortfolioSide):
            raise PortfolioError("side must be PortfolioSide")
        if type(self.requested_quantity) is not int or self.requested_quantity <= 0:
            raise PortfolioError("requested_quantity must be a positive integer")
        _decimal(self.reference_price, "reference_price", allow_zero=False)


@dataclass(frozen=True, slots=True)
class PortfolioOrderAttribution:
    portfolio_decision_id: str
    strategy_version_id: str
    proposal_id: str
    symbol: str
    signed_requested_quantity: int

    def __post_init__(self) -> None:
        for name in (
            "portfolio_decision_id",
            "strategy_version_id",
            "proposal_id",
            "symbol",
        ):
            _text(getattr(self, name), name)
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if type(self.signed_requested_quantity) is not int or self.signed_requested_quantity == 0:
            raise PortfolioError("attributed quantity must be a non-zero integer")


@dataclass(frozen=True, slots=True)
class PortfolioAction:
    """A netted portfolio candidate, not a broker order."""

    symbol: str
    side: PortfolioSide
    quantity: int
    reference_price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.side, PortfolioSide):
            raise PortfolioError("side must be PortfolioSide")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise PortfolioError("action quantity must be a positive integer")
        _decimal(self.reference_price, "reference_price", allow_zero=False)


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    """One symbol-level allocation result with complete proposal attribution."""

    decision_id: str
    decision: PortfolioVerdict
    symbol: str
    strategy_version_ids: tuple[str, ...]
    requested_quantity: int
    net_quantity: int
    blocker: PortfolioBlocker | None
    attribution: tuple[PortfolioOrderAttribution, ...]
    action: PortfolioAction | None

    def __post_init__(self) -> None:
        _text(self.decision_id, "decision_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if not isinstance(self.decision, PortfolioVerdict):
            raise PortfolioError("decision must be PortfolioVerdict")
        if not isinstance(self.strategy_version_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.strategy_version_ids
        ):
            raise PortfolioError("strategy_version_ids must be a tuple of strings")
        if type(self.requested_quantity) is not int or self.requested_quantity < 0:
            raise PortfolioError("requested_quantity must be non-negative")
        if type(self.net_quantity) is not int:
            raise PortfolioError("net_quantity must be an integer")
        if not isinstance(self.attribution, tuple) or any(
            not isinstance(item, PortfolioOrderAttribution) for item in self.attribution
        ):
            raise PortfolioError("attribution must be an immutable attribution tuple")
        if self.blocker is not None and not isinstance(self.blocker, PortfolioBlocker):
            raise PortfolioError("blocker must be PortfolioBlocker or None")
        if self.action is not None and not isinstance(self.action, PortfolioAction):
            raise PortfolioError("action must be PortfolioAction or None")
        if self.strategy_version_ids != tuple(sorted(set(self.strategy_version_ids))):
            raise PortfolioError("strategy_version_ids must be sorted and unique")
        expected_requested = sum(
            abs(item.signed_requested_quantity) for item in self.attribution
        )
        expected_net = sum(item.signed_requested_quantity for item in self.attribution)
        if self.requested_quantity != expected_requested or self.net_quantity != expected_net:
            raise PortfolioError("decision quantities must match their attribution")
        if any(
            item.portfolio_decision_id != self.decision_id or item.symbol != self.symbol
            for item in self.attribution
        ):
            raise PortfolioError("attribution must belong to this decision and symbol")
        if {item.strategy_version_id for item in self.attribution} != set(
            self.strategy_version_ids
        ):
            raise PortfolioError("decision strategy ids must match attribution")
        if self.decision is PortfolioVerdict.APPROVE:
            if self.blocker is not None:
                raise PortfolioError("approved decision cannot have a blocker")
            if self.net_quantity == 0 and self.action is not None:
                raise PortfolioError("zero-net decision cannot have an action")
            if self.net_quantity != 0 and self.action is None:
                raise PortfolioError("non-zero approved decision requires an action")
            if self.action is not None and (
                self.action.symbol != self.symbol
                or self.action.quantity != abs(self.net_quantity)
                or self.action.side
                is not (PortfolioSide.BUY if self.net_quantity > 0 else PortfolioSide.SELL)
            ):
                raise PortfolioError("action must exactly represent the net portfolio delta")
        elif self.action is not None or self.blocker is None:
            raise PortfolioError("rejected decision requires a blocker and no action")

    @property
    def strategy_version_id(self) -> str | None:
        """Return the sole contributor, when this decision has exactly one."""

        if len(self.strategy_version_ids) == 1:
            return self.strategy_version_ids[0]
        return None


def stable_portfolio_decision_id(
    *, symbol: str, proposal_ids: tuple[str, ...], observed_at: datetime | None
) -> str:
    """Create a stable ID independent of input arrival order."""

    timestamp = observed_at.isoformat() if observed_at is not None else "unobserved"
    material = "\0".join((_symbol(symbol), timestamp, *sorted(proposal_ids)))
    return sha256(material.encode("utf-8")).hexdigest()
