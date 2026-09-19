"""Risk application: the single pre-trade risk authority.

Every question of the form "may this account buy this, and how much of it?"
is answered here and nowhere else.  Before this module existed the answer was
assembled from three places at once -- ``AutoQuantEngine`` sized and gated the
entry, ``PreTradeRiskEngine`` re-checked it, and the configured
``LayeredRiskLimits`` reached the two by different routes -- so a limit could
be enforced in a unit test and absent in the running system.  The Desktop
wiring defect that made that concrete is recorded in ``__init__`` below.

Four rules are load-bearing.

**Risk reduces, it does not veto.**  A proposal for 100 shares against a
20-share ceiling is approved *for 20*, with the ceilings that bound it named in
``RiskDecision.adjustments``.  Refusing outright would have changed the sizing
behaviour the transitional runtime already relies on; the point of the
migration is to move that arithmetic into one place, not to alter it.

**A halt never blocks a reduction.**  The daily-loss halt and the drawdown
halt exist to stop the account taking on *more* risk.  Blocking a lawful exit
with them would turn a risk control into a reason the account cannot get out,
so ``SELL`` is evaluated against the position only: the symbol need not be
allowed, the account may already be over every ceiling, and the prices of
unrelated holdings are irrelevant.

**BUY fails closed on missing information.**  If an existing holding has no
market price, its exposure cannot be computed, and evaluating a purchase
against a total that silently omits it would understate the book.  The
evaluation is refused rather than assuming zero.

**It cannot produce an order.**  There is no import of ``OrderIntent``,
``PaperOrderIntent``, ``BrokerExecutionPort`` or ``OrderRepositoryPort``; the
output is a ``RiskDecision`` over whole-share quantities.  Deciding *whether*
and *how much* is risk's job; creating order identity, submitting, cancelling
and reconciling are execution's.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Mapping

from us_quant.trading.domain.account import (
    Position,
    RiskAccountSnapshot,
)
from us_quant.trading.domain.common import ONE, ZERO
from us_quant.trading.domain.risk import (
    LayeredRiskLimits,
    RiskDecision,
    RiskEvaluationRequest,
    RiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
    resolve_session_risk_overrides,
    resolve_symbol_risk_overrides,
)
from us_quant.trading.domain.strategy import TradeAction


class RiskApplication:
    """Stateless pre-trade risk over immutable limits.

    It holds the limits and the configured exposure multipliers, and nothing
    else.  Positions, cash, the current account and any pending order arrive
    with each call, so a stale snapshot cannot be remembered as a rule.
    """

    def __init__(
        self,
        limits: LayeredRiskLimits | RiskLimits,
        *,
        exposure_multipliers: Mapping[str, Decimal] | None = None,
    ) -> None:
        """Bind the limits, and the per-symbol exposure multipliers.

        ``exposure_multipliers`` is the ``execution_symbol`` →
        ``exposure_multiplier`` map derived from the configured instrument
        substitutions.  It lives here, with the per-symbol overrides, because
        the transitional runtime used to keep its own copy: the configured map
        reached the engine through one constructor argument while the account
        limits reached it through ``ShadowConfig``, and the Desktop passed
        only the former.  A multiplier the engine cannot see sizes exposure
        wrongly, so there is exactly one owner now.
        """

        self._limits = (
            limits
            if isinstance(limits, LayeredRiskLimits)
            else LayeredRiskLimits(account=limits)
        )
        resolved = dict(exposure_multipliers or {})
        for symbol, multiplier in resolved.items():
            if multiplier <= ZERO:
                raise ValueError(
                    f"exposure multiplier for {symbol} must be positive"
                )
        self._exposure_multipliers = resolved

    # -- read-only policy ------------------------------------------------

    @property
    def limits(self) -> LayeredRiskLimits:
        """The layered limits this application enforces."""

        return self._limits

    @property
    def session_overrides(self) -> SessionRiskOverrides:
        """Session policy, exposed so callers stop re-parsing the layers.

        ``entry_start``, ``last_entry``, ``maximum_trades_per_day`` and
        ``daily_loss_limit`` remain the transitional runtime's business: they
        order *when* a session may trade, which is not a pre-trade risk
        verdict.  Reading them from here is what keeps ``LayeredRiskLimits``
        out of a second module.
        """

        return resolve_session_risk_overrides(self._limits)

    def symbol_overrides(self, symbol: str) -> SymbolRiskOverrides:
        """The per-symbol policy for ``symbol``, defaulted if unset."""

        return resolve_symbol_risk_overrides(symbol, self._limits)

    def exposure_multiplier(self, symbol: str) -> Decimal:
        """The effective exposure multiplier for ``symbol``.

        A symbol override that sets anything other than one wins; otherwise
        the configured substitution multiplier applies; otherwise one.  A
        single resolution order is what stops two callers computing two
        different exposures for the same holding.
        """

        overrides = self.symbol_overrides(symbol)
        if overrides.exposure_multiplier != ONE:
            return overrides.exposure_multiplier
        return self._exposure_multipliers.get(symbol, ONE)

    # -- evaluation ------------------------------------------------------

    def evaluate(
        self,
        *,
        request: RiskEvaluationRequest,
        account: RiskAccountSnapshot,
        positions: Mapping[str, Position],
        market_prices: Mapping[str, Decimal],
        allowed_symbols: set[str] | frozenset[str] | None = None,
    ) -> RiskDecision:
        """Judge ``request`` against ``account`` and the current book.

        ``allowed_symbols`` is the runtime's own scope -- for an automated
        rotation it is the candidate set.  It is applied here, and only to
        purchases, so that a symbol which drops out of scope can still be
        sold.
        """

        proposal = request.proposal
        requested = proposal.desired_quantity
        if proposal.action is TradeAction.HOLD:
            # A hold produces no order at all, so there is nothing to approve.
            return RiskDecision.reject(
                "hold proposal requires no order",
                requested_quantity=requested,
            )
        if proposal.action is TradeAction.SELL:
            return self._evaluate_sell(
                requested=requested,
                symbol=request.execution_symbol,
                positions=positions,
            )
        return self._evaluate_buy(
            request=request,
            account=account,
            positions=positions,
            market_prices=market_prices,
            allowed_symbols=allowed_symbols,
        )

    # -- sells -----------------------------------------------------------

    @staticmethod
    def _evaluate_sell(
        *,
        requested: int,
        symbol: str,
        positions: Mapping[str, Position],
    ) -> RiskDecision:
        """A reduction is checked against the position, and nothing else.

        No account halt, no symbol permission, no exposure ceiling, no cash
        and no unrelated price is consulted: an account that has already
        breached a limit is precisely the account that most needs to be able
        to sell.  Overselling is refused rather than trimmed, because a
        proposal to sell more than is held means the caller and the broker
        disagree about what is owned, and quietly selling five of six would
        hide that.
        """

        position = positions.get(symbol)
        held = position.quantity if position is not None else 0
        if held <= 0:
            return RiskDecision.reject(
                "no long position to reduce",
                requested_quantity=requested,
            )
        if requested > held:
            return RiskDecision.reject(
                "short positions are disabled",
                requested_quantity=requested,
            )
        return RiskDecision.approve(requested_quantity=requested)

    # -- buys ------------------------------------------------------------

    def _evaluate_buy(
        self,
        *,
        request: RiskEvaluationRequest,
        account: RiskAccountSnapshot,
        positions: Mapping[str, Position],
        market_prices: Mapping[str, Decimal],
        allowed_symbols: set[str] | frozenset[str] | None,
    ) -> RiskDecision:
        proposal = request.proposal
        symbol = request.execution_symbol
        requested = proposal.desired_quantity
        overrides = self.symbol_overrides(symbol)

        halts = self._entry_halt_reasons(
            symbol=symbol,
            account=account,
            overrides=overrides,
            allowed_symbols=allowed_symbols,
        )
        if halts:
            return RiskDecision.reject(
                *halts, requested_quantity=requested
            )

        multiplier = self.exposure_multiplier(symbol)
        price = proposal.reference_price
        exposure_per_share = price * multiplier

        missing = self._missing_price_reasons(
            symbol=symbol, positions=positions, market_prices=market_prices
        )
        if missing:
            return RiskDecision.reject(
                *missing, requested_quantity=requested
            )

        caps = [
            (
                "position exposure cap",
                self._position_limit_quantity(
                    symbol=symbol,
                    account=account,
                    overrides=overrides,
                    positions=positions,
                    exposure_per_share=exposure_per_share,
                ),
            ),
            (
                "gross exposure cap",
                self._gross_limit_quantity(
                    account=account,
                    positions=positions,
                    market_prices=market_prices,
                    symbol=symbol,
                    price=price,
                    exposure_per_share=exposure_per_share,
                ),
            ),
        ]
        if not self._limits.account.allow_margin_borrowing:
            caps.append(
                (
                    "cash cap",
                    self._cash_limit_quantity(
                        request=request,
                        account=account,
                        price=price,
                    ),
                )
            )
        caps = tuple(caps)

        approved = min(
            [requested, *(limit for _, limit in caps)]
        )
        if approved <= 0:
            # Which ceiling was zero matters: "no cash" and "already at the
            # position ceiling" call for different operator responses.
            binding = [name for name, limit in caps if limit == approved]
            return RiskDecision.reject(
                "no safe whole-share quantity remains",
                *(f"{name} left no whole share" for name in binding),
                requested_quantity=requested,
            )
        if approved == requested:
            return RiskDecision.approve(requested_quantity=requested)
        binding = [
            name
            for name, limit in caps
            if limit == approved and limit < requested
        ]
        return RiskDecision.approve(
            requested_quantity=requested,
            approved_quantity=approved,
            adjustments=tuple(
                f"{name} reduced quantity {requested} → {approved}"
                for name in binding
            ),
        )

    def _entry_halt_reasons(
        self,
        *,
        symbol: str,
        account: RiskAccountSnapshot,
        overrides: SymbolRiskOverrides,
        allowed_symbols: set[str] | frozenset[str] | None,
    ) -> list[str]:
        """Why this *purchase* is not permitted, regardless of size.

        These are verdicts about taking on risk at all, so they are settled
        before any quantity is computed -- and they apply to purchases only.
        The two account ratios are unchanged from the retired engine: loss is
        measured against the day's starting equity, drawdown against the high
        watermark.
        """

        limits = self._limits.account
        reasons: list[str] = []
        if not overrides.allowed:
            reasons.append("execution symbol is blocked by risk overrides")
        if allowed_symbols is not None and symbol not in allowed_symbols:
            reasons.append("execution symbol is not allowed")
        if account.day_start_equity > ZERO:
            daily_loss = (
                account.day_start_equity - account.net_liquidation
            ) / account.day_start_equity
            if daily_loss >= limits.daily_loss_halt_pct:
                reasons.append("daily account loss halt is active")
        if account.high_watermark > ZERO:
            drawdown = (
                account.high_watermark - account.net_liquidation
            ) / account.high_watermark
            if drawdown >= limits.drawdown_halt_pct:
                reasons.append("account drawdown halt is active")
        if account.net_liquidation <= ZERO:
            reasons.append("account net liquidation is not positive")
        return reasons

    def _position_limit_quantity(
        self,
        *,
        symbol: str,
        account: RiskAccountSnapshot,
        overrides: SymbolRiskOverrides,
        positions: Mapping[str, Position],
        exposure_per_share: Decimal,
    ) -> int:
        """Shares that fit under the effective per-symbol exposure ceiling.

        The effective ceiling is the *minimum* over every layer that declares
        one: the account limit, the symbol override and the session fraction.
        A symbol override therefore cannot raise a ceiling -- the retired
        engine let it replace the account limit, which is the specific
        widening this migration closes.
        """

        limits = self._limits.account
        effective = limits.max_position_exposure_pct
        if overrides.max_position_exposure_pct is not None:
            effective = min(
                effective, overrides.max_position_exposure_pct
            )
        session_fraction = self.session_overrides.max_position_fraction
        if session_fraction is not None:
            effective = min(effective, session_fraction)

        existing = positions.get(symbol)
        held = existing.quantity if existing is not None else 0
        room = (
            account.net_liquidation * effective
            - exposure_per_share * held
        )
        return _whole_shares(room, exposure_per_share)

    def _gross_limit_quantity(
        self,
        *,
        account: RiskAccountSnapshot,
        positions: Mapping[str, Position],
        market_prices: Mapping[str, Decimal],
        symbol: str,
        price: Decimal,
        exposure_per_share: Decimal,
    ) -> int:
        """Shares that fit under the account gross-exposure ceiling."""

        limits = self._limits.account
        gross = ZERO
        for held_symbol, position in positions.items():
            mark = price if held_symbol == symbol else market_prices.get(
                held_symbol
            )
            if mark is None:
                # ``_missing_price_reasons`` has already refused this
                # evaluation; returning no room keeps the helper fail-closed
                # rather than merely unreachable.
                return 0
            gross += (
                mark * position.quantity
                * self.exposure_multiplier(held_symbol)
            )
        room = account.net_liquidation * limits.max_gross_exposure_pct - gross
        return _whole_shares(room, exposure_per_share)

    @staticmethod
    def _cash_limit_quantity(
        *,
        request: RiskEvaluationRequest,
        account: RiskAccountSnapshot,
        price: Decimal,
    ) -> int:
        """Shares the account's own cash pays for, commission included.

        Buying power is deliberately not consulted: while margin borrowing is
        disabled -- the posture the Paper safety invariants require -- the
        question is what the cash covers, not what the broker would lend.  The
        caller decides whether this ceiling applies at all:
        ``allow_margin_borrowing`` removes it, exactly as the retired engine
        did.  A shortfall trims the order instead of refusing it, which is
        what the runtime already did.
        """

        return _whole_shares(
            account.cash - request.estimated_commission, price
        )

    def _missing_price_reasons(
        self,
        *,
        symbol: str,
        positions: Mapping[str, Position],
        market_prices: Mapping[str, Decimal],
    ) -> list[str]:
        """Holdings whose exposure cannot be priced are a refusal, not a zero.

        The symbol being bought is exempt: the proposal carries the price it
        would trade at, so its own exposure is always computable.
        """

        return [
            f"missing market price for {held_symbol}"
            for held_symbol in positions
            if held_symbol != symbol and held_symbol not in market_prices
        ]


def _whole_shares(room: Decimal, per_share: Decimal) -> int:
    """How many whole shares fit in ``room``, rounded down.

    Fractional shares are not traded, and rounding up would breach the very
    ceiling this is computing room under.
    """

    if room <= ZERO or per_share <= ZERO:
        return 0
    return int(
        (room / per_share).to_integral_value(rounding=ROUND_DOWN)
    )


__all__ = ["RiskApplication"]
