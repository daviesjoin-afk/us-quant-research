"""Dispatching proposals: the one place risk and execution are called.

A ``TradeProposal`` arrives here and leaves either as a refusal or as an order
the ``ExecutionApplication`` has already accepted and made durable.  This is
the only module in the runtime package that names ``RiskApplication`` or
``ExecutionApplication``, and it deliberately does *not* name the strategy
runtime: the strategy never learns that a verdict exists, and this module never
learns why a proposal was made.

It returns outcomes instead of mutating the session.  A refusal is a string the
session reports; a failure the session must stop for is a flag it acts on.  That
keeps one writer of session state -- ``TradingRuntime`` -- even though two
layers are asked their opinion in between.

Every exit goes through risk here as well: stop-loss, take-profit, trailing,
the maximum-hold exit, the close-of-session flatten and a user stop are all
ordinary proposals.  "A lawful reduction is always allowed" is asserted once, in
the risk layer, instead of being an exemption granted at this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.orders import OrderIntent
from us_quant.trading.domain.risk import RiskDecision, RiskEvaluationRequest
from us_quant.trading.domain.strategy import TradeProposal
from us_quant.trading.ports.broker_execution import (
    ExecutionSubmissionUncertain,
)
from us_quant.trading.runtime.models import StrategySessionPolicy
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.shadow_paper import ShadowConfig


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What dispatching one proposal did.

    Exactly one of the cases is true.  ``submitted`` means an order exists and
    ``intent`` is it.  ``blockage`` is a refusal that must not stop the scan --
    a risk rejection, or a trim that left the order below the minimum notional.
    ``halt`` is a failure the session has to stop for: a refused reduction, a
    blocked submission, or one whose outcome cannot be established locally.
    ``reconciliation_required`` distinguishes the refusal that means the
    runtime and risk disagree about the book from the one that means the broker
    call itself failed.
    """

    submitted: bool = False
    blockage: str | None = None
    halt: bool = False
    reconciliation_required: bool = False
    status: str = ""
    intent: OrderIntent | None = None


class OrderDispatch:
    def __init__(
        self,
        *,
        config: ShadowConfig,
        risk: RiskApplication,
        execution: ExecutionApplication,
    ) -> None:
        self.config = config
        self.risk = risk
        self.execution = execution

    def run_entries(
        self,
        *,
        now: datetime,
        proposals: tuple[TradeProposal, ...],
        book: SessionBook,
        session_id: str,
        allowed_symbols: frozenset[str],
    ) -> tuple[DispatchOutcome | None, tuple[str, ...]]:
        """Walk the ranked candidates and submit the first that clears risk.

        Returns the outcome that ended the walk -- a submission, or a halt --
        and the blockages collected along the way.  The walk deliberately does
        not stop at the strongest signal: a leader blocked for a reason that
        does not apply to the next candidate must not hide an executable
        second, and the reasons are kept rather than collapsed into "no
        signal".
        """

        blockages: list[str] = []
        for proposal in proposals:
            outcome = self.enter(
                now=now,
                proposal=proposal,
                book=book,
                session_id=session_id,
                allowed_symbols=allowed_symbols,
            )
            if outcome.submitted or outcome.halt:
                return outcome, tuple(blockages)
            blockages.append(outcome.blockage or "")
        return None, tuple(blockages)

    # -- the verdict -----------------------------------------------------

    def evaluate(
        self,
        *,
        proposal: TradeProposal,
        symbol: str,
        now: datetime,
        book: SessionBook,
        allowed_symbols: frozenset[str],
    ) -> RiskDecision:
        """Ask the single risk authority about one proposal.

        ``allowed_symbols`` is passed in rather than derived here: the trading
        scope belongs to the session, and reading it directly would make this
        module depend on the strategies it is holding a verdict about.
        """

        return self.risk.evaluate(
            request=RiskEvaluationRequest(
                proposal=proposal,
                execution_symbol=symbol,
                estimated_commission=self.config.commission_per_order,
            ),
            account=book.account_snapshot(now=now),
            positions=book.risk_positions(self.risk.exposure_multiplier),
            market_prices=book.risk_market_prices(),
            allowed_symbols=allowed_symbols,
        )

    def policy(self) -> StrategySessionPolicy:
        """Configuration defaults resolved against the session's overrides.

        Resolved here because the overrides live in ``RiskApplication``: this is
        what lets the entry window and the trade-count ceiling keep their
        current meaning without the strategy runtime holding a risk service it
        must not call.
        """

        overrides = self.risk.session_overrides
        return StrategySessionPolicy(
            entry_start=overrides.entry_start or self.config.entry_start,
            last_entry=overrides.last_entry or self.config.last_entry,
            maximum_trades_per_day=(
                overrides.maximum_trades_per_day
                if overrides.maximum_trades_per_day is not None
                else self.config.maximum_trades_per_day
            ),
            daily_loss_limit=(
                overrides.daily_loss_limit
                if overrides.daily_loss_limit is not None
                else self.config.daily_loss_limit
            ),
        )

    # -- entries ---------------------------------------------------------

    def enter(
        self,
        *,
        now: datetime,
        proposal: TradeProposal,
        book: SessionBook,
        session_id: str,
        allowed_symbols: frozenset[str],
    ) -> DispatchOutcome:
        """Ask risk about one candidate, and submit it if it clears.

        A refusal returns a blockage rather than an exception: the scan does not
        stop at the strongest signal, because a leader blocked for a reason that
        does not apply to the next candidate must not hide an executable second.
        """

        symbol = proposal.symbol
        decision = self.evaluate(
            proposal=proposal,
            symbol=symbol,
            now=now,
            book=book,
            allowed_symbols=allowed_symbols,
        )
        if not decision.approved:
            return DispatchOutcome(
                blockage=(
                    f"{symbol} 风险阻断：{'；'.join(decision.reasons)}"
                )
            )
        if (
            decision.approved_quantity * proposal.reference_price
            < self.config.min_order_notional
        ):
            # Risk trimmed the order below what is worth sending.  The next
            # candidate may still be executable, so this is not fatal.
            return DispatchOutcome(
                blockage=(
                    f"{symbol} 风险缩量至 {decision.approved_quantity} 股后"
                    "低于最小下单金额"
                )
            )
        return self.submit(
            proposal=proposal,
            decision=decision,
            execution_symbol=symbol,
            reason=self.entry_reason(proposal=proposal, decision=decision),
            session_id=session_id,
        )

    @staticmethod
    def entry_reason(
        *, proposal: TradeProposal, decision: RiskDecision
    ) -> str:
        """The order's reason text, with any risk reduction made visible.

        A quantity that changed between proposal and order must be readable
        from the order itself; otherwise an operator reconciling fills cannot
        tell a trimmed order from a rejected one.
        """

        if decision.approved_quantity >= proposal.desired_quantity:
            return proposal.reason
        return (
            f"风险缩量 {proposal.desired_quantity} → "
            f"{decision.approved_quantity}；"
            + "；".join(decision.adjustments)
            + "；"
            + proposal.reason
        )

    # -- exits -----------------------------------------------------------

    def exit(
        self,
        *,
        now: datetime,
        proposal: TradeProposal,
        symbol: str,
        book: SessionBook,
        session_id: str,
        allowed_symbols: frozenset[str],
    ) -> DispatchOutcome:
        """Ask risk about a reduction, and halt if it is refused.

        A refused reduction for a position this session actually holds means the
        runtime and the risk layer disagree about the book.  That is not
        something to retry through -- a retry loop is how a disagreement becomes
        an order storm -- so it asks the session to stop for a human.
        """

        decision = self.evaluate(
            proposal=proposal,
            symbol=symbol,
            now=now,
            book=book,
            allowed_symbols=allowed_symbols,
        )
        if not decision.approved:
            return DispatchOutcome(
                halt=True,
                reconciliation_required=True,
                status=(
                    "风险层拒绝了合法平仓单"
                    f"（{symbol}：{'；'.join(decision.reasons)}）"
                ),
            )
        return self.submit(
            proposal=proposal,
            decision=decision,
            execution_symbol=symbol,
            reason=proposal.reason,
            session_id=session_id,
        )

    # -- the one broker-facing call --------------------------------------

    def submit(
        self,
        *,
        proposal: TradeProposal,
        decision: RiskDecision,
        execution_symbol: str,
        reason: str,
        session_id: str,
    ) -> DispatchOutcome:
        """Hand one risk-approved proposal to the execution service.

        The dispatch builds no order identity, writes nothing durably and never
        touches a broker.  What it keeps is the behaviour around the call: an
        outcome it cannot establish locally halts the session and keeps the
        order in the pending book, and any other refusal halts too rather than
        retrying, because a retry loop is how one order becomes several.
        """

        try:
            result = self.execution.submit_approved(
                proposal=proposal,
                decision=decision,
                execution_symbol=execution_symbol,
                session_id=session_id,
                reason=reason,
            )
        except ExecutionSubmissionUncertain as error:
            return DispatchOutcome(
                halt=True,
                status=(
                    "Paper 订单提交结果不确定；已停机并保留在途意图，"
                    f"等待券商对账（Order {error.broker_order_id}）"
                ),
                intent=error.intent,
            )
        except Exception as error:
            return DispatchOutcome(
                halt=True,
                status=(
                    f"Paper 订单提交被阻断：{error}；"
                    "会话已停机，避免自动重试形成重复订单"
                ),
            )
        intent = result.intent
        return DispatchOutcome(
            submitted=True,
            status=(
                f"已提交 IBKR Paper {intent.side.order_text} "
                f"{intent.execution_symbol} "
                f"{intent.quantity} 股 @ {intent.limit_price} · "
                f"Order {result.broker_order_id}"
            ),
            intent=intent,
        )