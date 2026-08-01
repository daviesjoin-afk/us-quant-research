from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Mapping

from us_quant.domain import ONE, ZERO


@dataclass(frozen=True, slots=True)
class SubstitutionRule:
    source_symbol: str
    execution_symbol: str
    exposure_multiplier: Decimal
    holding_mode: str

    def __post_init__(self) -> None:
        if self.exposure_multiplier <= ONE:
            raise ValueError("substitution leverage must be greater than one")
        if not self.source_symbol or not self.execution_symbol:
            raise ValueError("substitution symbols are required")


@dataclass(frozen=True, slots=True)
class ResolvedInstrument:
    signal_symbol: str
    execution_symbol: str
    quantity: int
    price: Decimal
    exposure_multiplier: Decimal
    used_substitution: bool

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def risk_exposure(self) -> Decimal:
        return self.notional * self.exposure_multiplier


class IntegerPositionSizer:
    """Convert target exposure to whole-share quantities."""

    def __init__(
        self,
        substitutions: Mapping[str, SubstitutionRule],
        *,
        force_substitution_symbols: frozenset[str] = frozenset(),
    ) -> None:
        self._substitutions = dict(substitutions)
        unknown = force_substitution_symbols.difference(self._substitutions)
        if unknown:
            raise ValueError(
                "forced substitutions require an approved rule: "
                + ", ".join(sorted(unknown))
            )
        self._force_substitution_symbols = force_substitution_symbols

    @staticmethod
    def whole_quantity(notional: Decimal, price: Decimal) -> int:
        if notional < ZERO:
            raise ValueError("target notional cannot be negative")
        if price <= ZERO:
            raise ValueError("price must be positive")
        return int((notional / price).to_integral_value(rounding=ROUND_FLOOR))

    def resolve(
        self,
        *,
        signal_symbol: str,
        target_risk_exposure: Decimal,
        prices: Mapping[str, Decimal],
    ) -> ResolvedInstrument | None:
        if target_risk_exposure <= ZERO:
            return None
        source_price = prices.get(signal_symbol)
        if source_price is None:
            raise KeyError(f"missing price for {signal_symbol}")

        if signal_symbol not in self._force_substitution_symbols:
            direct_quantity = self.whole_quantity(
                target_risk_exposure, source_price
            )
            if direct_quantity >= 1:
                return ResolvedInstrument(
                    signal_symbol=signal_symbol,
                    execution_symbol=signal_symbol,
                    quantity=direct_quantity,
                    price=source_price,
                    exposure_multiplier=ONE,
                    used_substitution=False,
                )

        rule = self._substitutions.get(signal_symbol)
        if rule is None:
            return None
        substitute_price = prices.get(rule.execution_symbol)
        if substitute_price is None:
            raise KeyError(f"missing price for {rule.execution_symbol}")

        substitute_notional = (
            target_risk_exposure / rule.exposure_multiplier
        )
        substitute_quantity = self.whole_quantity(
            substitute_notional, substitute_price
        )
        if substitute_quantity < 1:
            return None

        return ResolvedInstrument(
            signal_symbol=signal_symbol,
            execution_symbol=rule.execution_symbol,
            quantity=substitute_quantity,
            price=substitute_price,
            exposure_multiplier=rule.exposure_multiplier,
            used_substitution=True,
        )
