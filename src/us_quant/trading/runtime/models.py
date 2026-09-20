"""What the strategy reads: candidates, frozen views, and one scan's answer.

Everything the strategy runtime is allowed to know about the session is here,
and it is deliberately only three things:

* ``AutoQuantCandidate`` -- the scan universe, unchanged from ``auto_quant.py``
  because it is also the vocabulary of the UI's candidate table.
* ``StrategyPositionView`` and ``StrategySessionPolicy`` -- frozen projections of
  the ledger and of the session policy.  Frozen is the point: the strategy
  reasons about what is held without being able to write to it, and it reads the
  entry window without holding the risk service those values come from.
* ``EntryEvaluation`` / ``ExitEvaluation`` -- one scan's and one exit pass's
  answer, in the shape the session consumes.

This module is what a strategy may import, so it names no order identity, no
verdict, no broker and no widget.  The session's own artifacts -- the snapshot
the window renders, which does carry orders -- live in ``artifacts.py``, one
import away and deliberately not reachable from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal

from us_quant.trading.domain.strategy import TradeProposal


@dataclass(frozen=True, slots=True)
class AutoQuantCandidate:
    symbol: str
    name: str
    sector: str
    leader_tier: int
    scan_score: Decimal
    signal: str


@dataclass(frozen=True, slots=True)
class StrategyPositionView:
    """One holding, as the strategy is allowed to see it.

    Everything here is a fact a strategy decision legitimately needs -- the
    exit gates compare the mark against ``average_price`` and ``high_water``,
    the hold clock compares against ``opened_at``, and sizing subtracts what is
    already held.  It is a frozen copy, so the strategy can reason about the
    ledger without being able to write to it; the session owns the real book.
    """

    symbol: str
    quantity: int
    average_price: Decimal
    opened_at: datetime
    high_water: Decimal

    @classmethod
    def of(cls, position) -> "StrategyPositionView":
        """Project a stored holding into the view the strategy may read.

        The ledger keeps ``opened_at`` as the ISO text it writes out; the view
        carries a real ``datetime`` because the only thing that reads it is a
        hold clock, and parsing it at each comparison would put the same
        conversion in more than one place.
        """

        return cls(
            symbol=position.symbol,
            quantity=position.quantity,
            average_price=position.average_price,
            opened_at=datetime.fromisoformat(
                position.opened_at.replace("Z", "+00:00")
            ),
            high_water=position.high_water,
        )


@dataclass(frozen=True, slots=True)
class StrategySessionPolicy:
    """The session policy values a strategy needs, with risk left outside.

    ``TradingRuntime`` resolves configuration defaults against
    ``RiskApplication.session_overrides`` once per tick and passes the result
    in.  That is what lets the entry window and the trade-count ceiling keep
    their current meaning without ``StrategyRuntime`` holding a risk service it
    is forbidden to call.
    """

    entry_start: time
    last_entry: time
    maximum_trades_per_day: int
    daily_loss_limit: Decimal


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    """One entry scan: the proposals, in rank order, and what it could not do.

    ``proposals`` is ordered strongest first, and the caller walks it until one
    candidate clears risk -- the scan deliberately does not stop at the
    strongest signal, because a leader blocked for a reason that does not apply
    to the next candidate must not hide an executable second.

    ``status`` explains a tick that produced no proposals at all (outside the
    entry window, warmup, a regime block).  ``notes`` carries the
    per-candidate reasons that did not stop the scan -- a signal whose
    whole-share budget comes to zero -- so the caller can report them beside
    its own risk refusals instead of losing them.
    """

    proposals: tuple[TradeProposal, ...]
    status: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExitEvaluation:
    """One exit pass: the reductions due on this tick, and what could not be.

    A tick produces one proposal per holding that breaches a gate -- a take
    profit, a stop loss, a trailing stop, the hold clock, or an unconditional
    reduction the caller asked for.  ``notes`` carries the holders that could
    not be priced at all, so a flatten that finds only stale bids is reported
    rather than silently doing nothing.
    """

    proposals: tuple[TradeProposal, ...]
    notes: tuple[str, ...] = ()