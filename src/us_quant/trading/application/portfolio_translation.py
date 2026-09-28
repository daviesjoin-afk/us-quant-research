"""The single translator from governed trade proposals to portfolio intents."""

from __future__ import annotations

from hashlib import sha256

from us_quant.trading.domain.portfolio import (
    PortfolioSide,
    StrategyPortfolioIntent,
)
from us_quant.trading.domain.strategy import TradeAction, TradeProposal


def strategy_proposal_to_portfolio_intent(
    proposal: TradeProposal,
) -> StrategyPortfolioIntent | None:
    """Translate one proposal; HOLD remains a non-executable observation."""

    if not isinstance(proposal, TradeProposal):
        raise TypeError("proposal must be TradeProposal")
    if proposal.action is TradeAction.HOLD:
        return None
    material = "\0".join(
        (
            proposal.strategy.version_id,
            proposal.strategy.parameter_hash,
            proposal.symbol.strip().upper(),
            proposal.action.value,
            str(proposal.desired_quantity),
            str(proposal.reference_price),
            proposal.generated_at.isoformat(),
        )
    )
    return StrategyPortfolioIntent(
        strategy_version_id=proposal.strategy.version_id,
        symbol=proposal.symbol,
        side=(PortfolioSide.BUY if proposal.action is TradeAction.BUY else PortfolioSide.SELL),
        requested_quantity=proposal.desired_quantity,
        reference_price=proposal.reference_price,
        proposal_id=sha256(material.encode("utf-8")).hexdigest(),
    )
