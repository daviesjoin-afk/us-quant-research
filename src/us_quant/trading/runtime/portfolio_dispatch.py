"""Adapter from a portfolio action to the existing Risk/OrderDispatch seam."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from us_quant.trading.domain.portfolio import PortfolioDecision, PortfolioSide
from us_quant.trading.domain.portfolio_runtime import PortfolioDispatchResult
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.runtime.dispatch import OrderDispatch
from us_quant.trading.runtime.portfolio import SessionBook


class PortfolioOrderDispatchBridge:
    """Use one injected OrderDispatch for both Risk evaluation and submission."""

    def __init__(
        self,
        *,
        dispatch: OrderDispatch,
        book: SessionBook,
        session_id: str,
        allowed_symbols: frozenset[str],
    ) -> None:
        self._dispatch = dispatch
        self._book = book
        self._session_id = session_id
        self._allowed_symbols = allowed_symbols

    def evaluate(
        self, decision: PortfolioDecision, *, observed_at: datetime
    ) -> RiskDecision:
        proposal = _proposal(decision, observed_at)
        return self._dispatch.evaluate(
            proposal=proposal,
            symbol=proposal.symbol,
            now=observed_at,
            book=self._book,
            allowed_symbols=self._allowed_symbols,
        )

    def submit(
        self,
        decision: PortfolioDecision,
        risk_decision: RiskDecision,
        *,
        observed_at: datetime,
    ) -> PortfolioDispatchResult:
        proposal = _proposal(decision, observed_at)
        outcome = self._dispatch.submit(
            proposal=proposal,
            decision=risk_decision,
            execution_symbol=proposal.symbol,
            reason=proposal.reason,
            session_id=self._session_id,
        )
        intent = outcome.intent
        if intent is not None:
            # Match TradingRuntime's pending-book ownership for both normal
            # and uncertain submissions, so the next Risk/account view sees
            # the order while reconciliation is outstanding.
            self._book.add(intent)
        return PortfolioDispatchResult(
            submitted=outcome.submitted,
            halt=outcome.halt,
            order_id=intent.order_id if intent is not None else None,
            status=outcome.status,
        )


def _proposal(decision: PortfolioDecision, observed_at: datetime) -> TradeProposal:
    action = decision.action
    if decision.decision.value != "approve" or action is None:
        raise ValueError("only an approved portfolio action can reach Risk")
    digest = sha256(decision.decision_id.encode("utf-8")).hexdigest()
    return TradeProposal(
        strategy=StrategyIdentity(
            strategy_id="portfolio-runtime",
            version_id="portfolio-runtime",
            parameter_hash=digest,
        ),
        symbol=action.symbol,
        action=(TradeAction.BUY if action.side is PortfolioSide.BUY else TradeAction.SELL),
        desired_quantity=action.quantity,
        reference_price=action.reference_price,
        reason=f"portfolio decision {decision.decision_id}",
        generated_at=observed_at,
    )
