"""Pure account queries: rules with no runtime, no Qt and no I/O.

The one rule here is the **fresh Paper net liquidation** gate.  It used to be
``MainWindow._paper_simulation_capital``, which meant the rule was only
reachable through a 5k-line widget owner, was only testable by standing that
owner up, and had to be kept in step with the preflight's own freshness check
by hand.

Living as a Qt-free function, the rule now has exactly one home: changing how
fresh an account must be to size an internal simulation is a change to this
file plus its unit tests, and it touches neither Qt, nor ``MainWindow``, nor
Paper or Shadow.

The rule is deliberately kept word-for-word identical to the retired method,
including the two easy-to-lose details: the window is inclusive (exactly
``max_age_seconds`` old is still fresh) and a naive ``observed_at`` is read as
UTC rather than rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.common import Environment

#: How old a Paper observation may be and still size an internal simulation.
#: The same value the preflight applies, so simulation can never be sized from
#: an account the preflight would reject.
FRESH_PAPER_MAX_AGE_SECONDS = 300.0


def _as_utc(value: datetime) -> datetime:
    """Read a naive timestamp as UTC, matching the retired rule.

    The domain builds ``observed_at`` aware, but a portfolio assembled by a
    test or by a future adapter may not be; the legacy rule treated that case
    as UTC rather than refusing it, and refusing it now would be a behaviour
    change smuggled into an extraction.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fresh_paper_net_liquidation(
    portfolio: BrokerAccountPortfolio | None,
    *,
    now: datetime | None = None,
    max_age_seconds: float = FRESH_PAPER_MAX_AGE_SECONDS,
) -> Decimal | None:
    """The account's net liquidation, or ``None`` when it may not be used.

    ``None`` means "do not size anything from this": there is no portfolio, the
    account is not Paper, the reading is older than the freshness window (or
    dated in the future), or the broker reported no usable net liquidation.

    ``now`` is injectable so the rule is deterministic under test; production
    callers leave it out and get the wall clock.
    """

    if portfolio is None:
        return None
    account = portfolio.account
    if account.environment is not Environment.PAPER:
        return None
    observed = _as_utc(account.observed_at)
    reference = datetime.now(timezone.utc) if now is None else _as_utc(now)
    age_seconds = (reference - observed).total_seconds()
    if age_seconds < 0 or age_seconds > max_age_seconds:
        return None
    value = account.net_liquidation
    if value is None or value <= 0:
        return None
    return value


__all__ = [
    "FRESH_PAPER_MAX_AGE_SECONDS",
    "fresh_paper_net_liquidation",
]
