from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Mapping

from us_quant.domain import (
    AccountSnapshot,
    ONE,
    ZERO,
    OrderIntent,
    Position,
    RiskDecision,
    Side,
)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_gross_exposure_pct: Decimal
    max_position_exposure_pct: Decimal
    daily_loss_halt_pct: Decimal
    drawdown_halt_pct: Decimal
    allow_margin_borrowing: bool = False

    def __post_init__(self) -> None:
        percentages = (
            self.max_gross_exposure_pct,
            self.max_position_exposure_pct,
            self.daily_loss_halt_pct,
            self.drawdown_halt_pct,
        )
        if any(value <= ZERO or value > ONE for value in percentages):
            raise ValueError("risk percentages must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class SymbolRiskOverrides:
    max_position_exposure_pct: Decimal | None = None
    exposure_multiplier: Decimal = ONE
    allowed: bool = True


@dataclass(frozen=True, slots=True)
class SessionRiskOverrides:
    maximum_trades_per_day: int | None = None
    entry_start: time | None = None
    last_entry: time | None = None
    force_flat: time | None = None
    daily_loss_limit: Decimal | None = None
    max_position_fraction: Decimal | None = None


@dataclass(frozen=True, slots=True)
class LayeredRiskLimits:
    account: RiskLimits
    symbols: Mapping[str, SymbolRiskOverrides] = field(
        default_factory=dict
    )
    session: SessionRiskOverrides = field(
        default_factory=SessionRiskOverrides
    )


def resolve_symbol_risk_overrides(
    symbol: str,
    layered_limits: LayeredRiskLimits,
) -> SymbolRiskOverrides:
    overrides = layered_limits.symbols.get(symbol)
    if overrides is not None:
        return overrides
    return SymbolRiskOverrides()


def resolve_session_risk_overrides(
    layered_limits: LayeredRiskLimits,
) -> SessionRiskOverrides:
    return layered_limits.session


class PreTradeRiskEngine:
    """Fail-closed pre-trade controls for the long-only MVP."""

    def __init__(
        self,
        limits: LayeredRiskLimits | RiskLimits,
        allowed_symbols: set[str] | None = None,
    ) -> None:
        if isinstance(limits, RiskLimits):
            self._layered_limits = LayeredRiskLimits(account=limits)
        else:
            self._layered_limits = limits
        self._allowed_symbols = allowed_symbols

    def _account_limits(self) -> RiskLimits:
        return self._layered_limits.account

    def _symbol_overrides(
        self, symbol: str
    ) -> SymbolRiskOverrides:
        return resolve_symbol_risk_overrides(
            symbol, self._layered_limits
        )

    def _session_overrides(self) -> SessionRiskOverrides:
        return resolve_session_risk_overrides(self._layered_limits)

    def evaluate(
        self,
        *,
        intent: OrderIntent,
        account: AccountSnapshot,
        positions: Mapping[str, Position],
        market_prices: Mapping[str, Decimal],
        estimated_commission: Decimal = ZERO,
    ) -> RiskDecision:
        reasons: list[str] = []
        limits = self._account_limits()
        symbol_overrides = self._symbol_overrides(
            intent.execution_symbol
        )
        session_overrides = self._session_overrides()

        if not symbol_overrides.allowed:
            reasons.append("execution symbol is blocked by risk overrides")

        if (
            self._allowed_symbols is not None
            and intent.execution_symbol not in self._allowed_symbols
        ):
            reasons.append("execution symbol is not allowed")

        if intent.side == Side.BUY and account.day_start_equity > ZERO:
            daily_loss = (
                account.day_start_equity - account.net_liquidation
            ) / account.day_start_equity
            if daily_loss >= limits.daily_loss_halt_pct:
                reasons.append("daily account loss halt is active")

        if intent.side == Side.BUY and account.high_watermark > ZERO:
            drawdown = (
                account.high_watermark - account.net_liquidation
            ) / account.high_watermark
            if drawdown >= limits.drawdown_halt_pct:
                reasons.append("account drawdown halt is active")

        current = positions.get(
            intent.execution_symbol,
            Position(
                symbol=intent.execution_symbol,
                quantity=0,
                average_price=ZERO,
                exposure_multiplier=intent.exposure_multiplier,
            ),
        )
        signed_quantity = (
            intent.quantity if intent.side == Side.BUY else -intent.quantity
        )
        projected_quantity = current.quantity + signed_quantity
        if projected_quantity < 0:
            reasons.append("short positions are disabled")
            projected_quantity = 0
        if (
            current.quantity > 0
            and current.exposure_multiplier != intent.exposure_multiplier
        ):
            reasons.append("position exposure multiplier mismatch")

        projected_positions = dict(positions)
        projected_positions[intent.execution_symbol] = Position(
            symbol=intent.execution_symbol,
            quantity=projected_quantity,
            average_price=intent.estimated_price,
            exposure_multiplier=intent.exposure_multiplier,
        )

        if account.net_liquidation <= ZERO:
            reasons.append("account net liquidation is not positive")
        else:
            gross_exposure = ZERO
            current_gross_exposure = ZERO
            for symbol, position in projected_positions.items():
                price = (
                    intent.estimated_price
                    if symbol == intent.execution_symbol
                    else market_prices.get(symbol)
                )
                if price is None:
                    reasons.append(f"missing market price for {symbol}")
                    continue
                exposure = position.risk_exposure(price)
                gross_exposure += exposure
                previous_position = positions.get(symbol)
                previous_exposure = (
                    previous_position.risk_exposure(price)
                    if previous_position is not None
                    else ZERO
                )
                current_gross_exposure += previous_exposure
                max_position_exposure_pct = (
                    symbol_overrides.max_position_exposure_pct
                    if symbol_overrides.max_position_exposure_pct
                    is not None
                    else limits.max_position_exposure_pct
                )
                if (
                    exposure / account.net_liquidation
                    > max_position_exposure_pct
                    and exposure >= previous_exposure
                ):
                    reasons.append(
                        f"position exposure limit exceeded for {symbol}"
                    )

            if (
                gross_exposure / account.net_liquidation
                > limits.max_gross_exposure_pct
                and gross_exposure >= current_gross_exposure
            ):
                reasons.append("gross exposure limit exceeded")

        if intent.side == Side.BUY and not limits.allow_margin_borrowing:
            required_cash = (
                intent.estimated_price * intent.quantity
                + estimated_commission
            )
            if required_cash > account.cash:
                reasons.append("insufficient cash; margin borrowing is disabled")

        return (
            RiskDecision.reject(*dict.fromkeys(reasons))
            if reasons
            else RiskDecision.approve()
        )
