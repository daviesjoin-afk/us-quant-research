"""Qt-free rules for the execution / AutoQuant route.

Everything here is a pure function of its arguments so each rule can be asserted
without standing up a window, a page or a broker.  What lived in ``MainWindow``
as inline arithmetic and inline sentences is now one named rule per decision:

* **which symbols are market *references*** -- trimmed, upper-cased and
  de-duplicated, and never tradable rotation candidates;
* **whether the selected version is eligible** for an AutoQuant session;
* **how many realtime quotes the current session demands**;
* **how the operator's capital limit bounds Paper cash** -- it may only shrink
  it, never enlarge it;
* **how a finished scan becomes a shortlist**, and how the scope and summary
  sentences that describe it read.

The shortlist rule is the one that matters most: it is the only place a
``MarketScan`` turns into retained candidates, and it excludes the reference
symbols a second time because a reference that slipped through the scanner's own
``excluded_symbols`` would otherwise be traded on the strength of a strategy's
own yardstick.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from us_quant.extended_hours import USEquitySession
from us_quant.trading.domain.strategy import StrategyStatus, StrategyVersion
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.preflight import (
    AutoQuantPreflight,
    AutoQuantPreflightCheck,
)

#: The one AutoQuant strategy the desktop route will launch.
AUTO_ROTATION_STRATEGY_ID = "intraday-auto-rotation"

#: The preflight check that is a *confirmation*, not a condition: it is excluded
#: from the tally the page shows, because the operator's answer is collected by
#: the launch dialog rather than asserted as a passing check.
ARM_CONFIRMATION_CHECK = "本次确认"


def reference_symbols(parameters: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The strategy's market reference symbols, normalized and de-duplicated.

    Order is preserved and the rule is verbatim from the retired window helper:
    strip, upper-case, drop empties, then de-duplicate.  A reference symbol is a
    yardstick, not a position -- see :func:`build_candidates`.
    """

    if not parameters:
        return ()
    raw = parameters.get("market_reference_symbols", []) or []
    return tuple(
        dict.fromkeys(
            str(symbol).strip().upper()
            for symbol in raw
            if str(symbol).strip()
        )
    )


def strategy_eligibility(
    strategy: StrategyVersion | None,
) -> tuple[bool, str]:
    """Whether ``strategy`` may drive an AutoQuant session, and its detail line.

    Statuses are compared as enum members rather than raw strings, so a domain
    rename cannot quietly keep an ineligible version eligible.  A version in
    research may run; a paper/shadow version must have passed its gate.
    """

    eligible = (
        strategy is not None
        and strategy.strategy_id == AUTO_ROTATION_STRATEGY_ID
        and strategy.status
        in {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
        and (
            strategy.status is StrategyStatus.RESEARCH
            or strategy.gate_passed
        )
    )
    detail = (
        f"{strategy.semver} · {strategy.status}"
        if strategy is not None
        else "请选择自动轮动策略版本"
    )
    return eligible, detail


def minimum_realtime_quotes(session: USEquitySession) -> int:
    """How many realtime candidates the open session demands.

    Three in the regular session, where a rotation needs a real choice; one
    outside it, where liquidity thins and demanding three would refuse sessions
    the operator can legitimately run.
    """

    return 3 if session is USEquitySession.REGULAR else 1


def rotation_liquidity_first(session: USEquitySession) -> bool:
    """Whether the shortlist should rank by liquidity rather than by score."""

    return session is not USEquitySession.REGULAR


def maximum_per_sector(limit: int) -> int:
    """The sector-concentration cap for a shortlist of ``limit`` rows."""

    return max(2, min(4, (limit + 5) // 6))


def bounded_capital(
    paper_capital: Decimal, requested_limit: Decimal
) -> Decimal:
    """Bound session capital by the operator's limit, which may only shrink it.

    ``requested_limit <= 0`` means "no limit given" and leaves the fresh Paper
    net liquidation alone.  A positive limit is a ceiling: the effective figure
    is the smaller of the two, so a typed number can never talk the session into
    sizing on capital the broker does not have.
    """

    if requested_limit > 0:
        return min(paper_capital, requested_limit)
    return paper_capital


def build_candidates(
    rows: Iterable[Any], references: Sequence[str]
) -> tuple[AutoQuantCandidate, ...]:
    """Project eligible scan rows into the retained shortlist.

    The exclusion is applied *again* here even though the scanner receives the
    same sequence: a reference symbol is a yardstick the strategy measures
    against, and one that reached the rotation list would make the strategy's own
    benchmark tradable.
    """

    excluded = set(references)
    candidates: list[AutoQuantCandidate] = []
    for row in rows:
        symbol = row.execution_symbol.strip().upper()
        if symbol in excluded:
            continue
        candidates.append(
            AutoQuantCandidate(
                symbol=symbol,
                name=row.name,
                sector=row.sector,
                leader_tier=row.leader_tier,
                scan_score=Decimal(str(row.score)),
                signal=row.signal,
            )
        )
    return tuple(candidates)


def stream_symbols(
    candidate_symbols: Sequence[str], references: Sequence[str]
) -> tuple[str, ...]:
    """The subscription set: the shortlist plus the references it judges against.

    The reference symbols are subscribed because the strategy reads them live;
    they are still not candidates, which is why this concatenation is the only
    place the two are ever combined.
    """

    return tuple(dict.fromkeys(tuple(candidate_symbols) + tuple(references)))


@dataclass(frozen=True)
class PreflightTally:
    """The preflight line as the page shows it."""

    ready: int
    total: int
    details: str


def preflight_tally(result: AutoQuantPreflight) -> PreflightTally:
    """Turn a preflight result into the tally the page draws.

    The arm confirmation is dropped from *both* the tally and the detail line:
    it is collected by the launch dialog, so counting it as a condition would
    report a permanently failing check on a route nobody has armed yet.
    """

    displayed: list[AutoQuantPreflightCheck] = [
        row for row in result.checks if row.name != ARM_CONFIRMATION_CHECK
    ]
    return PreflightTally(
        ready=sum(row.passed for row in displayed),
        total=len(displayed),
        details="  ·  ".join(
            f"{'✓' if row.passed else '✕'} {row.name}：{row.detail}"
            for row in displayed
        ),
    )


def candidate_scope_text(
    *,
    research_count: int,
    scanned_count: int,
    skipped_count: int,
    candidate_count: int,
) -> str:
    """The scope line after a successful shortlist build."""

    return (
        f"全市场入口：非中概研究池 {research_count:,} · "
        f"本轮有合格日 K 并完成评分 {scanned_count:,} · "
        f"缺数据/不足200根 {skipped_count:,} · "
        f"Paper 实时轮动候选 {candidate_count}。"
    )


def candidate_summary_text(candidate_count: int) -> str:
    """The summary line after a successful shortlist build."""

    return (
        f"已从全市场扫描中整理 {candidate_count} 个实时轮动候选。"
        "行情订阅只承担分钟信号，不代表扫描范围只有这些代码；"
        "全部订单仍未武装。"
    )


def switching_summary_text(candidate_count: int) -> str:
    """The summary line published while a live feed is being switch-requested."""

    return f"已整理 {candidate_count} 个候选，正在安全停止旧行情并切换。"


def insufficient_candidates_message(count: int, minimum: int) -> str:
    """The refusal shown when the shortlist is too short to rotate."""

    return (
        f"最新扫描只有 {count} 个满足非中概、"
        "龙头/优质二线、Paper 整股容量和数据门的候选；"
        f"至少需要 {minimum} 个才启动自动轮动。"
    )


def extended_hours_status_text(enabled: bool, routing: Any) -> str:
    """The 5×24 Paper line for the current US equity session."""

    if enabled:
        status = "已启用" if routing.allowed else "当前不可交易"
    else:
        status = "未启用（仅常规时段）"
    return (
        f"当前美东时段：{routing.label} · 5×24 Paper：{status} · "
        f"{routing.reason}"
    )


def scan_counts(scan: object) -> tuple[int, int]:
    """The ``(scored, skipped)`` counts a finished scan carries.

    The shape is asserted rather than assumed: a task that returned something
    else is a programming error at the scan boundary, and quoting counts off an
    unreadable object would hide it behind a wrong sentence.
    """

    try:
        return len(scan.results), len(scan.skipped)  # type: ignore[attr-defined]
    except AttributeError as error:
        raise TypeError("unexpected full-market scan result") from error


def format_money(value: Decimal | float | int | None) -> str:
    """``_money`` from ``desktop.py``, mirrored for the probe's detail line.

    The orchestrator is Qt-free and may not reach for the window's helper, so the
    one formatting rule this route's message needs lives here.  It is
    byte-identical to the retired output, because the sentence it appears in must
    not change.  ``shadow/queries.py`` mirrors the same helper for the same
    reason; the two are independent on purpose, so neither route's copy depends
    on the other's.
    """

    if value is None:
        return "不可用"
    return f"${float(value):,.2f}"


def channel_detail(result: object) -> str:
    """The account/order detail line one successful probe published.

    The shape is asserted rather than assumed: a probe that returned something
    else is a programming error at the broker boundary, and formatting an
    unreadable object would hide it behind a broken sentence.
    """

    try:
        connection, broker_state = result  # type: ignore[misc]
        account_alias = connection.account_alias
        open_orders = connection.open_broker_orders
        unresolved = connection.unreconciled_local_orders
        net_liquidation = broker_state.net_liquidation
        cash = broker_state.cash
        positions = len(broker_state.positions)
    except (AttributeError, TypeError, ValueError) as error:
        raise TypeError(
            "unexpected Paper channel check result"
        ) from error
    return (
        f"{account_alias} · 净值 {format_money(net_liquidation)} · "
        f"现金 {format_money(cash)} · 持仓 {positions} · "
        f"开放 API 订单 {open_orders} · 本地待对账 {unresolved}"
    )


__all__ = [
    "ARM_CONFIRMATION_CHECK",
    "AUTO_ROTATION_STRATEGY_ID",
    "PreflightTally",
    "bounded_capital",
    "build_candidates",
    "candidate_scope_text",
    "candidate_summary_text",
    "channel_detail",
    "extended_hours_status_text",
    "format_money",
    "insufficient_candidates_message",
    "maximum_per_sector",
    "minimum_realtime_quotes",
    "preflight_tally",
    "reference_symbols",
    "rotation_liquidity_first",
    "scan_counts",
    "strategy_eligibility",
    "stream_symbols",
    "switching_summary_text",
]
