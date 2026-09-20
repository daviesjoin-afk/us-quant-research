"""Preflight: whether a Paper auto session may be armed at all.

A pure calculation over facts the window already holds -- capability, strategy
eligibility, candidate count, quote freshness, capital and the operator's
confirmation.  It decides nothing about the session and touches nothing: the
result is a list of named checks the window renders, and no runtime, risk
service, execution service, adapter or widget is reachable from here.

Moved out of ``auto_quant.py`` unchanged, because "can this start?" is not a
question either runtime owns -- the window asks it before either exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from us_quant.trading.domain.market import MarketQuote, MarketSnapshot


@dataclass(frozen=True, slots=True)
class AutoQuantPreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class AutoQuantPreflight:
    ready: bool
    checks: tuple[AutoQuantPreflightCheck, ...]

    @property
    def passed_count(self) -> int:
        return sum(row.passed for row in self.checks)


@dataclass(frozen=True, slots=True)
class QuoteReadinessBreakdown:
    """Freshness counts split by Paper candidates, references, and feed."""

    candidate_count: int
    candidate_current_count: int
    candidate_recent_count: int
    reference_count: int
    reference_current_count: int
    reference_recent_count: int
    subscription_count: int
    subscription_current_count: int
    subscription_recent_count: int


def calculate_quote_readiness_breakdown(
    quotes: MarketSnapshot | Iterable[MarketQuote],
    *,
    candidate_symbols: Iterable[str],
    reference_symbols: Iterable[str],
    recently_ready_symbols: Iterable[str],
) -> QuoteReadinessBreakdown:
    """Classify quote freshness without allowing references into candidates."""

    snapshot_quotes = (
        quotes.quotes if isinstance(quotes, MarketSnapshot) else quotes
    )
    subscribed_quotes = {
        quote.symbol.strip().upper(): quote for quote in snapshot_quotes
    }
    references = {
        symbol.strip().upper() for symbol in reference_symbols if symbol.strip()
    }
    candidates = {
        symbol.strip().upper() for symbol in candidate_symbols if symbol.strip()
    } - references
    subscribed = set(subscribed_quotes)
    current = {
        symbol for symbol, quote in subscribed_quotes.items()
        if quote.realtime_ready
    }
    recent = {
        symbol.strip().upper() for symbol in recently_ready_symbols
        if symbol.strip()
    } & subscribed
    return QuoteReadinessBreakdown(
        candidate_count=len(candidates),
        candidate_current_count=len(candidates & current),
        candidate_recent_count=len(candidates & recent),
        reference_count=len(references),
        reference_current_count=len(references & current),
        reference_recent_count=len(references & recent),
        subscription_count=len(subscribed),
        subscription_current_count=len(current),
        subscription_recent_count=len(recent),
    )


def evaluate_auto_quant_preflight(
    *,
    capability_enabled: bool,
    paper_confirmed: bool,
    strategy_eligible: bool,
    strategy_detail: str,
    candidate_count: int,
    realtime_ready_count: int,
    paper_capital: Decimal | None,
    recent_ready_count: int | None = None,
    minimum_realtime_quotes: int = 3,
) -> AutoQuantPreflight:
    if minimum_realtime_quotes <= 0:
        raise ValueError("minimum realtime quotes must be positive")
    required_quotes = min(minimum_realtime_quotes, candidate_count)
    checks = (
        AutoQuantPreflightCheck(
            "Paper 能力",
            capability_enabled,
            "已开启" if capability_enabled else "请在系统设置开启",
        ),
        AutoQuantPreflightCheck(
            "策略版本",
            strategy_eligible,
            strategy_detail,
        ),
        AutoQuantPreflightCheck(
            "动态候选",
            candidate_count >= 3,
            f"{candidate_count} 个；至少需要 3 个",
        ),
        AutoQuantPreflightCheck(
            "实时行情",
            candidate_count >= 3
            and realtime_ready_count >= required_quotes,
            (
                f"当前 fresh {realtime_ready_count}/{candidate_count}；"
                + (
                    f"近30秒覆盖 {recent_ready_count}/{candidate_count}；"
                    if recent_ready_count is not None
                    else ""
                )
                + f"启动至少需要当前 fresh {required_quotes}"
            ),
        ),
        AutoQuantPreflightCheck(
            "Paper 资金",
            paper_capital is not None and paper_capital > 0,
            (
                f"新鲜净值 {paper_capital:,.2f}"
                if paper_capital is not None
                else "请刷新 IBKR Paper 账户"
            ),
        ),
        AutoQuantPreflightCheck(
            "本次确认",
            paper_confirmed,
            "仅 DU 模拟账户"
            if paper_confirmed
            else "尚未勾选会话确认",
        ),
    )
    return AutoQuantPreflight(
        ready=all(row.passed for row in checks),
        checks=checks,
    )