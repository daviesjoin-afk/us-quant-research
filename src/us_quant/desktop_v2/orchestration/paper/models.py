"""The immutable facts and driven-port contracts of Paper launch orchestration.

Frozen plain data and declared Protocols only -- Qt-free and adapter-free.  Every
operator string is verbatim from the retired ``MainWindow`` handlers; a broker adapter,
a risk or execution application and a widget are never imported here.  The design
rationale lives in ``docs/DESKTOP_DECOMPOSITION.md`` §27.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from us_quant.auto_launch import AutoLaunchPlan
from us_quant.paper_order_models import PaperBrokerState
from us_quant.trading.domain.strategy import StrategyIdentity
from us_quant.trading.runtime.workflow_state import WorkflowStateError

if TYPE_CHECKING:
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.paper_contracts import (
        PaperEngine,
        PaperOrderPort,
    )
    from us_quant.trading.runtime.paper_models import PaperSessionResult
    from us_quant.trading.runtime.trading import TradingRuntime

#: The component one launch's runtime event is filed under, and its code.
PAPER_LAUNCH_COMPONENT = "auto_quant"
PAPER_ARMED_CODE = "PAPER_SESSION_ARMED"

#: Why a start was refused: the exact title and message the retired handler showed.
#: ``BAD_CALLBACK_MESSAGE`` is the one *delegation* message -- a malformed callback
#: shape is a programming error, so it is raised loudly instead of shown in a dialog.
DUPLICATE_TITLE = "Paper 会话正在连接"
DUPLICATE_MESSAGE = "当前启动检查仍在进行中，请不要重复启动。"
DUPLICATE_CONFIRM_MESSAGE = "当前启动检查仍在进行中，请等待本次连接完成或失败后再试。"
SHADOW_ACTIVE_TITLE = "内部仿真仍在运行"
SHADOW_ACTIVE_MESSAGE = "同一资金真值不能同时运行内部仿真和 IBKR Paper 自动量化。"
PREFLIGHT_TITLE = "启动前检查未通过"
PREFLIGHT_PREFIX = "请先处理以下项目：\n"
BEGIN_REFUSED_TITLE = "Paper 会话不可启动"
LAUNCH_FAILED_TITLE = "Paper 会话未启动"
CONNECT_FAILED_MESSAGE = "IBKR Paper 连接失败，未启动会话：{error}"
STALE_PLAN_MESSAGE = "已忽略过期的 Paper 连接结果；不会武装订单会话。"
PREFLIGHT_CHANGED_MESSAGE = "连接期间启动条件发生变化，已断开未武装的 Paper 会话。"
IDENTITY_CHANGED_MESSAGE = "连接期间策略、候选或资金上限已变化；已断开未武装的 Paper 会话。"
ARMING_FAILED_MESSAGE = "Paper 会话校验或武装失败，未提交自动订单：{error}"
BAD_CALLBACK_MESSAGE = "unexpected auto order connection result"

#: The one *invariant* message: ``publish_armed`` succeeded but the promotion that must
#: follow it did not, so the session runs with no owner.  A broken safety invariant
#: rather than a launch failure, reported at error severity under its own code.
PAPER_PROMOTION_INVARIANT_TITLE = "Paper 会话已发布但未能接管"
PAPER_PROMOTION_INVARIANT_CODE = "PAPER_PROMOTION_INVARIANT"
PAPER_PROMOTION_INVARIANT_MESSAGE = (
    "Paper 会话已发布但候选接管失败；会话处于已发布未接管状态，"
    "需人工处理；已保留租约与候选原状，不做回滚：{error}"
)

#: The catalogue-fault message.  Raised when a governed version's declared hash does not
#: describe its own parameters; the launch never proceeds, so the operator must be told
#: why rather than left with a confirmed-but-never-started attempt.
PAPER_STRATEGY_INTEGRITY_TITLE = "Paper 策略版本校验失败"
PAPER_STRATEGY_INTEGRITY_CODE = "PAPER_STRATEGY_INTEGRITY_FAILED"

#: Progress, status and event wording, verbatim from the retired chain.
CONNECTING_SUMMARY = "正在连接独立 IBKR Paper 订单会话并核验唯一 DU 账户…"
CONNECT_START_MESSAGE = "IBKR Paper 自动量化连接中…"
CONNECT_PROGRESS = "连接 IBKR Paper 订单通道…"
CONNECT_VERIFIED_PROGRESS = "已核验 {alias}；准备逐会话武装…"
ARMED_EVENT_MESSAGE = "IBKR Paper 自动量化会话 {session} 已武装；候选 {candidates}；Live 永久阻断"

#: The broker gates' text -- the sentences naming the failed account fact.
NET_LIQUIDATION_MESSAGE = "IBKR Paper 订单会话未返回有效净值"
POSITIONS_MESSAGE = "首期自动量化要求 Paper 账户启动时空仓；当前持仓：{symbols}"
CASH_MESSAGE = "IBKR Paper 订单会话未返回现金；禁止使用保证金借款代替现金"


@dataclass(frozen=True, slots=True)
class PaperLaunchRefusal:
    """One refusal, shaped as the dialog the operator must see."""
    title: str
    message: str


class PaperLaunchIntegrityError(RuntimeError):
    """A governed version's declared hash does not describe its own parameters.

    A catalogue fault rather than an operator condition, so it is not a refusal shape:
    the launch must not proceed under *either* hash, because running the declared one
    would execute parameters it does not cover.
    """


@dataclass(frozen=True, slots=True)
class PaperOrderChannel:
    """The order channel one attempt connects, read once at plan time.

    ``config`` is opaque: building it names the IBKR module the composition root owns.
    """

    config: object
    repository: object
    extended_hours_enabled: bool


@dataclass(frozen=True, slots=True)
class PaperStrategyLaunchFact:
    """The strategy identity one attempt runs, detached from the live version.

    ``parameters`` is a **deep copy** taken at freeze time.  Holding the live version
    would not freeze anything: it normalizes ``parameters`` to a plain mutable ``dict``
    and reads ``parameter_hash`` off the governed identity rather than recomputing it,
    so an in-place edit during the broker connect would let the attempt be *planned*
    under hash A and *built* from parameters B.  Deep rather than a structural freeze
    because the parameter validator requires real ``list`` values.
    """

    version_id: str
    parameter_hash: str
    identity: StrategyIdentity
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PaperLaunchRequest:
    """The frozen inputs of one attempt, read exactly once."""

    plan: AutoLaunchPlan
    strategy: PaperStrategyLaunchFact
    candidates: tuple[AutoQuantCandidate, ...]
    order_channel: PaperOrderChannel

    @property
    def candidate_symbols(self) -> tuple[str, ...]:
        """The frozen shortlist's symbols, upper-cased as the plan normalizes."""

        return tuple(row.symbol.upper() for row in self.candidates)


@dataclass(frozen=True, slots=True)
class PaperAccountReading:
    """The Paper account facts the capital gates read: net liquidation, cash, alias."""
    net_liquidation: Decimal
    cash: Decimal
    account_alias: str


@dataclass(frozen=True, slots=True)
class PaperSessionBuildResult:
    """What the build seam returns: the built runtime and what arming it needs.

    The seam composes and starts the runtime but deliberately does **not** arm, so
    ``arm -> ensure -> publish -> promote`` is one sequence in the orchestrator.
    """

    engine: PaperEngine
    orders: PaperOrderPort
    session_id: str
    candidate_count: int
    runtime: TradingRuntime
    max_order_notional: Decimal


@dataclass(frozen=True, slots=True)
class PaperLaunchPublication:
    """One successful launch's outcome: the workflow's own result, never a copy."""

    runtime: TradingRuntime
    result: PaperSessionResult
    session_id: str
    candidate_count: int


@dataclass(frozen=True, slots=True)
class PaperLaunchEvent:
    """One runtime event the orchestrator asks the window to record."""

    severity: str
    component: str
    code: str
    message: str


class PaperCandidateOrder(Protocol):
    """The *borrowed* candidate, as validation and the build seam read it.

    Nothing here submits or cancels -- that belongs to the runtime, through the
    execution application the seam injects -- and ``candidate_service`` lends this for
    one callback's stack only.
    """

    def connection_snapshot(self) -> Any: ...

    def broker_state(self) -> PaperBrokerState: ...

    def arm(self, *, session_id: str, allowed_symbols: tuple[str, ...],
            max_order_notional: Decimal,
            sellable_quantities: dict[str, int] | None = None) -> None: ...


#: The narrow session-build seam, assembled by the composition root so this layer
#: imports no risk or execution application, no config builder and no IBKR adapter.
PaperSessionBuilder = Callable[
    [PaperLaunchRequest, PaperCandidateOrder, PaperAccountReading],
    PaperSessionBuildResult,
]

__all__ = [
    "ARMED_EVENT_MESSAGE", "ARMING_FAILED_MESSAGE", "BAD_CALLBACK_MESSAGE",
    "BEGIN_REFUSED_TITLE", "CASH_MESSAGE", "CONNECT_FAILED_MESSAGE",
    "CONNECTING_SUMMARY", "CONNECT_PROGRESS", "CONNECT_START_MESSAGE",
    "CONNECT_VERIFIED_PROGRESS", "DUPLICATE_CONFIRM_MESSAGE", "DUPLICATE_MESSAGE",
    "DUPLICATE_TITLE", "IDENTITY_CHANGED_MESSAGE", "LAUNCH_FAILED_TITLE",
    "NET_LIQUIDATION_MESSAGE", "PAPER_ARMED_CODE", "PAPER_LAUNCH_COMPONENT",
    "PAPER_PROMOTION_INVARIANT_CODE", "PAPER_PROMOTION_INVARIANT_MESSAGE",
    "PAPER_PROMOTION_INVARIANT_TITLE", "PAPER_STRATEGY_INTEGRITY_CODE",
    "PAPER_STRATEGY_INTEGRITY_TITLE", "POSITIONS_MESSAGE",
    "PREFLIGHT_CHANGED_MESSAGE", "PREFLIGHT_PREFIX", "PREFLIGHT_TITLE",
    "PaperAccountReading", "PaperCandidateOrder", "PaperLaunchEvent",
    "PaperLaunchIntegrityError", "PaperLaunchPublication", "PaperLaunchRefusal",
    "PaperLaunchRequest", "PaperOrderChannel", "PaperSessionBuildResult",
    "PaperSessionBuilder", "PaperStrategyLaunchFact", "SHADOW_ACTIVE_MESSAGE",
    "SHADOW_ACTIVE_TITLE", "STALE_PLAN_MESSAGE", "WorkflowStateError",
]
