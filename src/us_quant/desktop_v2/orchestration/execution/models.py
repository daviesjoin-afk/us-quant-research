"""Immutable facts, dialog copy and narrow ports for the execution route.

The execution / AutoQuant route is the one desktop workflow whose decisions are
made *locally* rather than by an application: whether a shortlist may be built,
whether the order channel is being probed, whether a launch is in flight.  Those
answers have exactly one home now -- :class:`ExecutionOrchestrator` -- and this
module holds the vocabulary they are expressed in:

* the **route state** is three route-specific facts (``launch_busy``,
  ``channel_probe_inflight``, the retained candidate shortlist) and nothing
  else.  There is deliberately no model here for a market snapshot, an account
  portfolio, a Paper presentation or a scan: those belong to the capability that
  owns them and are read through a provider on every use.
* the **dialog copy** lives here because the *route* asks for those dialogs.  It
  is not imported from the Paper package: a message the execution route shows is
  the execution route's, and importing Paper's copy would make the execution
  package depend on Paper for a string.
* the **ports** below are structural: the composition root passes
  ``paper_orchestrator`` and the task boundary, and this package never imports
  either implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence

from us_quant.trading.runtime.models import AutoQuantCandidate

#: How many rotation candidates an AutoQuant session needs before it may reach
#: READY.  Three is the smallest shortlist that still rotates.
MINIMUM_CANDIDATES = 3

#: The resource groups the two route-owned background tasks run in.  They are
#: the *existing* generic groups, not new ones: the scan task serializes with
#: manual scans, and the probe serializes with every other broker task.
SCAN_RESOURCE_GROUP = "scan"
BROKER_RESOURCE_GROUP = "broker"

# -- preparation -----------------------------------------------------------

UNIVERSE_MISSING_TITLE = "缺少官方标的池"
UNIVERSE_MISSING_MESSAGE = "请先在总览刷新官方标的池，再执行全市场扫描。"

SESSION_RUNNING_TITLE = "自动量化运行中"
SESSION_RUNNING_MESSAGE = "请先停止并完成 Paper 持仓对账，再更换候选集。"

PREPARING_SUMMARY = (
    "正在扫描全部非中概研究池；只有历史数据质量达标的标的"
    "才会进入实时轮动候选。"
)
PREPARE_START_MESSAGE = "全市场扫描与 Paper 候选准备中…"
PREPARE_PROGRESS_MESSAGE = "自动量化第 1 步：扫描全部非中概研究池及已有合格日 K…"

#: Shown when the canonical workflow refuses to enter PREPARING: a session that
#: is already running, or one mid-launch.  The *message* is the workflow's own
#: refusal text, handed back by ``PaperOrchestrator.begin_preparation``.
PREPARATION_REFUSED_TITLE = "Paper 会话不可准备"

FRESH_CAPITAL_TITLE = "需要新鲜的 Paper 资金"
FRESH_CAPITAL_MESSAGE = (
    "请先在“账户与持仓”刷新 IBKR Paper 账户。"
    "自动候选会按模拟账户资金筛选，不再套用 1500 美元"
    "历史研究情景。"
)

INSUFFICIENT_CANDIDATES_TITLE = "合格候选不足"

HISTORY_SCHEDULED_MESSAGE = (
    "全市场历史缺口已自动加入数据任务队列：新增 "
    "{scheduled:,} 个；后续分批补齐后会自动扩大可评分覆盖。"
)

# -- channel probe ---------------------------------------------------------

CHANNEL_BUSY_TITLE = "Paper 会话正在使用"
CHANNEL_BUSY_MESSAGE = "当前自动量化会话已占用订单通道，无需重复检查。"

CHANNEL_START_MESSAGE = "正在检查 IBKR Paper 订单通道（不下单）…"
CHANNEL_PROGRESS_MESSAGE = "连接 IBKR Paper 订单通道并读取账户/订单；不下单…"
CHANNEL_OK_HEALTH_PREFIX = "执行对账：订单通道检查通过（未下单） · "
CHANNEL_OK_LOG_PREFIX = "IBKR Paper 订单通道检查通过（未下单）："

# -- launch ----------------------------------------------------------------

LAUNCH_IN_FLIGHT_TITLE = "Paper 会话正在连接"
LAUNCH_IN_FLIGHT_MESSAGE = (
    "当前启动检查仍在进行中，请等待本次连接完成或失败后再试。"
)

CONFIRM_TITLE = "确认启动 IBKR Paper 模拟下单"
CONFIRM_MESSAGE = (
    "下一步会连接唯一 DU 模拟账户，并可能向 IBKR Paper "
    "提交整股 DAY 限价单。不会连接 Live，也不会动真实资金。\n\n"
    "确认后，程序只会在实时行情、策略和风控检查全部通过时"
    "提交模拟订单。是否继续？"
)

SAFE_END_HEALTH = "执行对账：会话已安全结束，券商持仓和订单均已核对。"

# -- stop stream -----------------------------------------------------------

STOP_STREAM_BLOCKED_TITLE = "请先停止模拟下单"
STOP_STREAM_BLOCKED_MESSAGE = (
    "当前 Paper 模拟下单会话仍可能有持仓或在途订单。"
    "请先点击“停止会话并请求平仓”，完成券商对账后"
    "才能停止行情。"
)
STOP_STREAM_SUMMARY = "当前行情已停止。可重新点击第 1 步准备新的候选。"


@dataclass(frozen=True)
class ExecutionRuntimeEvent:
    """One runtime event the route publishes for the generic System owner."""

    severity: str
    component: str
    code: str
    message: str


@dataclass(frozen=True)
class MarketReadinessFact:
    """The cross-domain symbols the market readiness card must classify.

    A *finished* fact: the market layer may not look either half up, because the
    shortlist is this route's and the reference symbols come from the selected
    strategy.  Keeping them in one immutable object is what lets the window
    bridge them without re-deriving anything.
    """

    candidate_symbols: tuple[str, ...]
    reference_symbols: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSelection:
    """The finished shortlist plus the counts the scope line quotes."""

    candidates: tuple[AutoQuantCandidate, ...]
    references: tuple[str, ...]
    scanned_count: int
    skipped_count: int
    research_count: int

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(row.symbol for row in self.candidates)


class PaperFactsPort(Protocol):
    """The narrow Paper surface this route may read or transition.

    Every member delegates to the canonical ``PaperWorkflowController``.  None of
    them caches a phase, and ``begin_preparation`` answers with a *message*
    rather than raising a Paper exception, so this package imports no Paper type
    -- neither the workflow, nor its phase enum, nor its error.
    """

    @property
    def preparation_active(self) -> bool: ...

    @property
    def launch_attempt_in_flight(self) -> bool: ...

    @property
    def order_service_held(self) -> bool: ...

    @property
    def runtime_active(self) -> bool: ...

    @property
    def presentation(self) -> object | None: ...

    @property
    def session_control_facts(self) -> Any: ...

    def begin_preparation(self) -> str | None: ...

    def cancel_preparation(self) -> None: ...

    def mark_preparation_ready(self) -> None: ...


@dataclass(frozen=True)
class ExecutionProviders:
    """Every ambient fact the route reads, as one narrow callable per fact.

    Grouped rather than passed one by one so the constructor stays legible, and
    *callable* rather than valued so a repaint always draws what the owning
    capability published now.  None of these is a capability object: the route
    may not name Market, Account, Scanner, History or Universe, so the
    composition root supplies the reads.
    """

    #: The official universe snapshot, or ``None`` before it exists.
    universe: Callable[[], Any]
    #: The canonical ``ScannerOrchestrator.scan``, as the *adopted* fact.
    scan: Callable[[], Any]
    #: Run the full-market scan over ``(universe, capital)`` and save it.  The
    #: concrete data roots, substitutions and risk pct live in the composition
    #: root; *which* capital and *when* stay here.
    run_market_scan: Callable[[Any, Decimal], Any]
    #: Hand a finished scan to the capability that owns scan truth.
    adopt_scan: Callable[[Any], None]
    #: Queue the missing history for a universe; returns how many were added.
    schedule_history: Callable[[Any], int]
    #: Repaint the history route after new gaps were queued.
    refresh_history: Callable[[], None]

    # -- ambient reads -------------------------------------------------
    #
    # Everything below is a *read* of a fact another capability published, or a
    # value this route owns the decision to consult.  The market entries are the
    # whole of this route's market surface, and they are reads on purpose: a
    # market *command* may only leave here as one of the four
    # ``market_*_requested`` signals, because whether a stop or a switch is
    # allowed depends on Paper and Shadow, which only composition may name.  A
    # command smuggled in as a provider field would put that decision back
    # inside this capability.
    market_snapshot: Callable[[], Any]
    market_is_live: Callable[[], bool]
    market_provider: Callable[[], str | None]
    was_recently_ready: Callable[[str], bool]
    recently_ready_symbols: Callable[[], Sequence[str]]

    account_portfolio: Callable[[], Any]
    #: Fresh Paper net liquidation, or ``None`` when it is not usable.
    fresh_paper_capital: Callable[[], Decimal | None]
    broker_state: Callable[[], Any]
    reconciliation_rows: Callable[[str, int], Sequence[Any]]
    audit_rows: Callable[[int], Sequence[Any]]
    latency_rows: Callable[[str, int], Sequence[Any]]

    #: Configured exposure multipliers, used as candidate risk weights.
    exposure_multipliers: Callable[[], Mapping[str, Decimal]]
    #: The research scenario figure the *scan* is sized on.  Never a Paper
    #: sizing input -- that is fresh Paper cash, read separately above.
    research_scenario_capital: Callable[[], Decimal]
    maximum_position_exposure_pct: Callable[[], Decimal]

    #: Probe the IBKR Paper order channel without submitting anything.  The
    #: concrete port, client id, timeout, repository and service call live in
    #: the composition root; admission, the flag and the reporting stay here.
    probe_order_channel: Callable[[], object]

    #: The Paper order capability preference, and the 5x24 preference.
    paper_capability_enabled: Callable[[], bool]
    extended_hours_enabled: Callable[[], bool]


#: The route-owned task boundary: the same ``TaskSubmitter`` every capability is
#: annotated with, written out here so the signature is legible in one place.
SubmitTask = Callable[..., bool]


__all__ = [
    "BROKER_RESOURCE_GROUP",
    "CHANNEL_BUSY_MESSAGE",
    "CHANNEL_BUSY_TITLE",
    "CHANNEL_OK_HEALTH_PREFIX",
    "CHANNEL_OK_LOG_PREFIX",
    "CHANNEL_PROGRESS_MESSAGE",
    "CHANNEL_START_MESSAGE",
    "CONFIRM_MESSAGE",
    "CONFIRM_TITLE",
    "CandidateSelection",
    "ExecutionProviders",
    "ExecutionRuntimeEvent",
    "FRESH_CAPITAL_MESSAGE",
    "FRESH_CAPITAL_TITLE",
    "HISTORY_SCHEDULED_MESSAGE",
    "INSUFFICIENT_CANDIDATES_TITLE",
    "LAUNCH_IN_FLIGHT_MESSAGE",
    "LAUNCH_IN_FLIGHT_TITLE",
    "MINIMUM_CANDIDATES",
    "MarketReadinessFact",
    "PREPARING_SUMMARY",
    "PREPARATION_REFUSED_TITLE",
    "PREPARE_PROGRESS_MESSAGE",
    "PREPARE_START_MESSAGE",
    "PaperFactsPort",
    "SAFE_END_HEALTH",
    "SCAN_RESOURCE_GROUP",
    "SESSION_RUNNING_MESSAGE",
    "SESSION_RUNNING_TITLE",
    "STOP_STREAM_BLOCKED_MESSAGE",
    "STOP_STREAM_BLOCKED_TITLE",
    "STOP_STREAM_SUMMARY",
    "SubmitTask",
    "UNIVERSE_MISSING_MESSAGE",
    "UNIVERSE_MISSING_TITLE",
]
