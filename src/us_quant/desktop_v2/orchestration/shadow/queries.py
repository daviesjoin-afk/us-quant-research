"""Pure Shadow start rules: no Qt, no engine, no store, no I/O.

``MainWindow._start_shadow`` used to be one 180-line handler that read eight
facts off the window, ran ten gates inline, built an engine and started it.  The
gates are *rules*, not sequencing: which strategy versions may run the internal
simulator, what a runnable status is, which symbols the research pool admits, and
which quote may price a run.  This module is those rules.

Everything here is a plain function over plain data.  There is no engine, no
store, no widget and no callback: :func:`plan_start` receives the already-read
facts and returns either an immutable ``ShadowStartRequest`` or an immutable
``ShadowStartRefusal``.  Deciding *when* to read a fact, and what to do with the
verdict, is the orchestrator's job.

The wording of every refusal is imported from :mod:`.models` rather than written
here: the sentences are presentation, and the module that decides *when* to say
something should not also be where the sentence lives.

Two rules are deliberate rather than accidental:

* **the symbol pattern is a second copy** of the one in
  ``orchestration/research/targeted/session/queries.py``.  It has to be: Shadow
  orchestration may not import another capability.  And Shadow *validates* rather
  than normalizes -- the Targeted session capability owns the draft's
  normalization, so re-normalizing here would be a second rule for one fact;
* **the market gate requires freshness, not merely a present snapshot.**  The
  simulator fills against the touch, so a stale touch is a false fill rather than
  a degraded input.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any

from us_quant.desktop_v2.orchestration.shadow.models import (
    CAPITAL_MESSAGE,
    CAPITAL_TITLE,
    MARKET_MESSAGE,
    MARKET_TITLE,
    QUOTE_MESSAGE,
    QUOTE_TITLE,
    RUNTIME_BUSY_MESSAGE,
    RUNTIME_BUSY_TITLE,
    STATUS_MESSAGE,
    STATUS_TITLE,
    STRATEGY_MISMATCH_MESSAGE,
    STRATEGY_MISMATCH_TITLE,
    STRATEGY_MESSAGE,
    STRATEGY_TITLE,
    SYMBOL_MESSAGE,
    SYMBOL_TITLE,
    UNIVERSE_INELIGIBLE_MESSAGE,
    UNIVERSE_MISSING_MESSAGE,
    UNIVERSE_TITLE,
    ShadowCapitalFact,
    ShadowStartRefusal,
    ShadowStartRequest,
)
from us_quant.trading.domain.market import MarketQuote, MarketSnapshot
from us_quant.trading.domain.strategy import StrategyStatus, StrategyVersion
from us_quant.universe import UniverseSnapshot

#: The frozen input gate, byte-identical to the retired handler's regex.  It is
#: the *input* gate only -- the Targeted session capability already normalized the
#: draft, so this rejects rather than repairs.
TARGET_SYMBOL_PATTERN = r"[A-Z][A-Z0-9.-]{0,9}"

#: The one strategy the internal simulator may run.  A targeted intraday T
#: simulation bound to any other strategy is a refusal, not a fallback.
TARGETED_STRATEGY_ID = "intraday-targeted-t"

#: The simulator's daily loss ceiling, as a fraction of the Paper account's own
#: net liquidation.  Derived from broker truth for the same reason the initial
#: cash is: the simulator must not invent capital.
DAILY_LOSS_LIMIT_FRACTION = Decimal("0.01")

#: The statuses whose versions may run the internal simulator.  ``PAPER_SHADOW``
#: additionally has to have passed its evidence gate; ``PAUSED``, ``STOPPED`` and
#: ``LEGACY_INVALIDATED`` never run.
RUNNABLE_STATUSES = frozenset(
    {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
)


def is_valid_target_symbol(symbol: str) -> bool:
    """Whether ``symbol`` may be simulated at all.

    Anchored at both ends, exactly as the retired handler matched it, so
    ``"AAPL "`` and ``"AA PL"`` are rejected rather than silently repaired.
    """

    return re.fullmatch(TARGET_SYMBOL_PATTERN, symbol) is not None


def is_targeted_strategy(strategy_id: str) -> bool:
    """Whether ``strategy_id`` is the strategy the targeted simulator runs."""

    return strategy_id == TARGETED_STRATEGY_ID


def is_runnable_status(status: StrategyStatus, *, gate_passed: bool) -> bool:
    """Whether a version in ``status`` may run the internal simulator.

    ``PAPER_SHADOW`` is conditional: the version has to have passed its evidence
    gate.  A ``PAPER_SHADOW`` version that has not is refused for the same reason
    a stopped one is -- only versions that are allowed to run, run.
    """

    if status not in RUNNABLE_STATUSES:
        return False
    if status is StrategyStatus.PAPER_SHADOW:
        return gate_passed
    return True


def is_research_eligible(universe: UniverseSnapshot, symbol: str) -> bool:
    """Whether the research pool admits ``symbol``.

    The pool is the non-China research set, so this is the "不做中概股" hard
    filter.  ``universe`` must be present; a *missing* directory is a different
    refusal, decided before this rule runs.
    """

    return any(
        row.symbol == symbol and row.eligible_for_research
        for row in universe.records
    )


def market_gate_passed(*, is_live: bool, stream: MarketSnapshot | None) -> bool:
    """Whether the feed may price a simulation.

    All three have to hold: a usable feed is owned, a snapshot exists, and that
    snapshot is real-time.  A stale quote is not a degraded input here -- the
    simulator fills against the touch, so a stale touch is a false fill.
    """

    return is_live and stream is not None and stream.realtime_ready


def find_ready_quote(
    stream: MarketSnapshot, symbol: str
) -> MarketQuote | None:
    """The stream's *fresh* quote for ``symbol``, or ``None``.

    Deliberately no fallback: not another symbol, not the last quote, not an
    account mark.  A target with only a stale quote has no usable quote.
    """

    return next(
        (
            quote
            for quote in stream.quotes
            if quote.symbol == symbol and quote.realtime_ready
        ),
        None,
    )


def format_money(value: Decimal | float | int | None) -> str:
    """``_money`` from ``desktop.py``, mirrored for the two operator messages.

    The orchestrator is Qt-free and may not reach for the window's helper, so the
    one formatting rule a Shadow message needs lives here.  It is byte-identical
    to the retired output, because the sentences it appears in must not change.
    """

    if value is None:
        return "不可用"
    return f"${float(value):,.2f}"


def capital_source_text(account_alias: str) -> str:
    """The provenance line recorded for the run's initial cash.

    It names the broker account the figure came from, because that is the point:
    the simulator is sized from a real Paper reading, never from a research
    scenario number.
    """

    return f"IBKR Paper {account_alias} NetLiquidation"


def daily_loss_limit_for(capital: Decimal) -> Decimal:
    """The simulator's daily loss ceiling for ``capital``."""

    return capital * DAILY_LOSS_LIMIT_FRACTION


def plan_start(
    *,
    runtime_is_active: bool,
    strategy: StrategyVersion | None,
    capital: ShadowCapitalFact | None,
    market_is_live: bool,
    market_stream: MarketSnapshot | None,
    universe: UniverseSnapshot | None,
    target_symbol: str,
    symbol_risk_multipliers: Mapping[str, Decimal],
) -> ShadowStartRequest | ShadowStartRefusal:
    """Run the start gates in order and return either a request or a refusal.

    The order is the retired handler's, and it matters: the most fundamental
    refusal comes first, so an operator who has not read their Paper account is
    told that rather than being sent after a symbol.  Each gate reads only the
    fact it names -- a gate that peeked ahead would report a symptom whose cause
    it had not checked yet.

    Every fact is frozen into the returned request at the moment the last gate
    passes, so the run cannot be assembled from a value that changed in between.
    """

    if runtime_is_active:
        return ShadowStartRefusal(RUNTIME_BUSY_TITLE, RUNTIME_BUSY_MESSAGE)
    if strategy is None:
        return ShadowStartRefusal(STRATEGY_TITLE, STRATEGY_MESSAGE)
    if not is_targeted_strategy(strategy.strategy_id):
        return ShadowStartRefusal(
            STRATEGY_MISMATCH_TITLE, STRATEGY_MISMATCH_MESSAGE
        )
    if not is_runnable_status(strategy.status, gate_passed=strategy.gate_passed):
        return ShadowStartRefusal(STATUS_TITLE, STATUS_MESSAGE)
    if capital is None:
        return ShadowStartRefusal(CAPITAL_TITLE, CAPITAL_MESSAGE)
    if not market_gate_passed(is_live=market_is_live, stream=market_stream):
        return ShadowStartRefusal(MARKET_TITLE, MARKET_MESSAGE)
    if universe is None:
        return ShadowStartRefusal(UNIVERSE_TITLE, UNIVERSE_MISSING_MESSAGE)
    if not is_valid_target_symbol(target_symbol):
        return ShadowStartRefusal(SYMBOL_TITLE, SYMBOL_MESSAGE)
    if not is_research_eligible(universe, target_symbol):
        return ShadowStartRefusal(
            UNIVERSE_TITLE,
            UNIVERSE_INELIGIBLE_MESSAGE.format(symbol=target_symbol),
        )
    assert market_stream is not None
    if find_ready_quote(market_stream, target_symbol) is None:
        return ShadowStartRefusal(
            QUOTE_TITLE, QUOTE_MESSAGE.format(symbol=target_symbol)
        )
    return ShadowStartRequest(
        strategy_version_id=strategy.version_id,
        parameter_hash=strategy.parameter_hash,
        semver=strategy.semver,
        strategy_status=str(strategy.status),
        parameters=strategy.parameters,
        target_symbol=target_symbol,
        initial_cash=capital.net_liquidation,
        daily_loss_limit=daily_loss_limit_for(capital.net_liquidation),
        symbol_risk_multipliers=tuple(
            (str(symbol), Decimal(multiplier))
            for symbol, multiplier in symbol_risk_multipliers.items()
        ),
    )


__all__ = [
    "DAILY_LOSS_LIMIT_FRACTION",
    "RUNNABLE_STATUSES",
    "TARGETED_STRATEGY_ID",
    "TARGET_SYMBOL_PATTERN",
    "capital_source_text",
    "daily_loss_limit_for",
    "find_ready_quote",
    "format_money",
    "is_research_eligible",
    "is_runnable_status",
    "is_targeted_strategy",
    "is_valid_target_symbol",
    "market_gate_passed",
    "plan_start",
]
