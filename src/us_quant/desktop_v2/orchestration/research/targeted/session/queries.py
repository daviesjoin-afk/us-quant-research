"""Pure targeted-session rules: no Qt, no store, no command, no page.

Seven functions, each one rule that an inline ``MainWindow`` handler used to spell
out itself, moved here so it can be tested without a window and so the wording
exists in exactly one place.  Nothing here reads a store, evaluates a preflight,
sets a Market subscription or starts anything -- those are commands, and commands
belong to the orchestrator's injected dependencies.

The symbol pattern is **frozen**: ``[A-Z][A-Z0-9.-]{0,9}``, matched anchored at
both ends, exactly as the inline handlers matched it.  It is not a load-bearing
validator -- ``evaluate_target_preflight`` has its own format gate and re-checks
the symbol -- it is the *input* gate, and replacing it with a "better" expression
would change what the operator may type.
"""

from __future__ import annotations

import re

from us_quant.desktop_v2.pages.research.targeted.models import TargetedControlView
from us_quant.minute_data import MinuteDataSummary
from us_quant.trading.domain.market import MarketQuote, MarketSnapshot
from us_quant.universe import UniverseRecord, UniverseSnapshot

#: The frozen input gate.  Written as the literal the window used, so a future
#: reader can diff it against the retired handlers without trusting a refactor.
TARGET_SYMBOL_PATTERN = r"[A-Z][A-Z0-9.-]{0,9}"


def normalize_target_symbol(value: str) -> str:
    """The one normalization: strip the edges, upper-case the rest.

    Applied to the operator's raw input and again to anything already normalized
    (idempotent), which is what lets the draft signal and the command paths share
    one rule instead of two nearly-equal ones.
    """

    return value.strip().upper()


def is_valid_target_symbol(symbol: str) -> bool:
    """Whether ``symbol`` may be used as a target at all.

    Anchored at both ends, so ``"AAPL "`` and ``"AA PL"`` are rejected rather
    than silently trimmed into validity by a later step.
    """

    return re.fullmatch(TARGET_SYMBOL_PATTERN, symbol) is not None


def find_universe_record(
    universe: UniverseSnapshot | None,
    symbol: str,
) -> UniverseRecord | None:
    """The official directory row for ``symbol``, or ``None``.

    The first exact match, unchanged: a current snapshot that does not contain the
    symbol is *absence*, not an error, and the preflight's identity gate is what
    reports it.
    """

    if universe is None:
        return None
    return next(
        (row for row in universe.records if row.symbol == symbol),
        None,
    )


def find_market_quote(
    snapshot: MarketSnapshot | None,
    symbol: str,
) -> MarketQuote | None:
    """The current stream's quote for ``symbol``, or ``None``.

    Deliberately no fallback: not another symbol, not a Scanner price, not an
    account mark.  A target with no quote in the live stream has no quote, and the
    preflight's subscription gate is what says so.
    """

    if snapshot is None:
        return None
    return next(
        (quote for quote in snapshot.quotes if quote.symbol == symbol),
        None,
    )


def target_status_text(
    symbol: str,
    universe: UniverseSnapshot | None,
) -> str:
    """The target line, from the current Universe.

    Two outcomes and one asymmetry, both inherited verbatim.  A symbol the
    current directory excludes from non-China research is called out as set but
    ungated; everything else -- eligible, or a directory that is not loaded at
    all -- reads as the ordinary shared-target line.  "No universe" is therefore
    *not* reported as ineligible: the preflight is where a missing directory
    blocks, and turning it into a status escalation here would tell the operator
    their symbol failed a gate that was never evaluated.
    """

    eligible: bool | None = None
    if universe is not None:
        eligible = any(
            row.symbol == symbol and row.eligible_for_research
            for row in universe.records
        )
    if eligible is False:
        return f"{symbol} · 已设置，但尚未通过当前非中概研究资格门"
    return f"{symbol} · 订阅、回放、评估和影子做 T 共用"


def minute_status_text(symbol: str, summary: MinuteDataSummary) -> str:
    """The minute-evidence line for one symbol, from its local store summary.

    A rendering of the summary, nothing more: it does not read the store (the
    service does), does not decide whether the evidence is sufficient (the
    preflight's minute gate does) and does not filter by provider.
    """

    providers = " / ".join(summary.providers) or "无"
    origins = " / ".join(summary.evidence_origins) or "缺失"
    data_range = (
        f"{summary.first_minute} → {summary.last_minute}"
        if summary.first_minute and summary.last_minute
        else "尚无"
    )
    return (
        f"分钟证据 · {symbol}：可用 {summary.usable_rows} / "
        f"总计 {summary.total_rows} 行 · 来源 {providers} · "
        f"证据类型 {origins} · 区间 {data_range}"
    )


def control_view(shadow_active: bool) -> TargetedControlView:
    """The controls' enabled state, from whether an internal simulation runs.

    A running simulation owns the target, the strategy and the subscription, so
    those controls are disabled and only 停止内部仿真 is live.  Replay and
    robustness stay available: reading evidence while a simulation runs is not a
    competing writer.  The rule is unchanged from the window's -- this round does
    not redesign the button gating.
    """

    return TargetedControlView(
        strategy_enabled=not shadow_active,
        target_enabled=not shadow_active,
        subscribe_enabled=not shadow_active,
        shadow_start_enabled=not shadow_active,
        shadow_stop_enabled=shadow_active,
        replay_enabled=True,
        robustness_enabled=True,
    )


__all__ = [
    "TARGET_SYMBOL_PATTERN",
    "control_view",
    "find_market_quote",
    "find_universe_record",
    "is_valid_target_symbol",
    "minute_status_text",
    "normalize_target_symbol",
    "target_status_text",
]
