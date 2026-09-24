"""The immutable facts and driven-port contracts of Paper launch orchestration.

Frozen plain data and declared Protocols only -- Qt-free and adapter-free.  Every
operator string is verbatim from the retired ``MainWindow`` handlers.  The workflow
controller and the order-service owner are named for *typing only*, being this
lifecycle's canonical owners; a broker adapter, a risk or execution application and a
widget are never imported, because those arrive through the build seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from us_quant.auto_launch import AutoLaunchPlan
from us_quant.paper_order_models import PaperBrokerState
from us_quant.trading.runtime.workflow_state import WorkflowStateError

if TYPE_CHECKING:
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.paper_contracts import (
        PaperEngine,
        PaperOrderPort,
    )
    from us_quant.trading.runtime.paper_models import PaperSessionResult
    from us_quant.trading.runtime.trading import TradingRuntime

#: The component this launch's runtime event is filed under, and its code.
PAPER_LAUNCH_COMPONENT = "auto_quant"
PAPER_ARMED_CODE = "PAPER_SESSION_ARMED"

#: Why a start was refused, as the dialog the operator sees -- the exact title and
#: message the retired handler showed.  ``BAD_CALLBACK_MESSAGE`` is the one
#: *delegation* message: a malformed callback shape is a programming error rather than
#: an operator condition, so it is raised loudly instead of shown in a dialog.
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
#: Progress, status and event wording, verbatim from the retired chain.
CONNECTING_SUMMARY = "正在连接独立 IBKR Paper 订单会话并核验唯一 DU 账户…"
CONNECT_START_MESSAGE = "IBKR Paper 自动量化连接中…"
CONNECT_PROGRESS = "连接 IBKR Paper 订单通道…"
CONNECT_VERIFIED_PROGRESS = "已核验 {alias}；准备逐会话武装…"
ARMED_EVENT_MESSAGE = "IBKR Paper 自动量化会话 {session} 已武装；候选 {candidates}；Live 永久阻断"
#: The broker gates' refusal text -- the sentences naming the failed account fact.
NET_LIQUIDATION_MESSAGE = "IBKR Paper 订单会话未返回有效净值"
POSITIONS_MESSAGE = "首期自动量化要求 Paper 账户启动时空仓；当前持仓：{symbols}"
CASH_MESSAGE = "IBKR Paper 订单会话未返回现金；禁止使用保证金借款代替现金"


@dataclass(frozen=True, slots=True)
class PaperLaunchRefusal:
    """One refusal, shaped as the dialog the operator must see."""
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class PaperOrderChannel:
    """The order channel one attempt connects, read once at plan time.

    ``config`` is opaque because building it names the IBKR connection module the
    composition root owns.
    """

    config: object
    repository: object
    extended_hours_enabled: bool


@dataclass(frozen=True, slots=True)
class PaperLaunchRequest:
    """The frozen inputs of one attempt, read exactly once."""

    plan: AutoLaunchPlan
    strategy_version: Any
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
    """What the build seam returns: the ports ``publish_armed`` binds, the session id,
    and the runtime the desktop keeps for the still-unmigrated active-session paths."""

    engine: PaperEngine
    orders: PaperOrderPort
    session_id: str
    candidate_count: int
    runtime: TradingRuntime


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

    Nothing here submits or cancels: that belongs to the runtime, through the execution
    application the build seam injects.  ``candidate_service`` takes this reference for
    one callback's stack, never stores it and never keeps it past promotion.
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
    "BEGIN_REFUSED_TITLE", "CASH_MESSAGE", "CONNECTING_SUMMARY",
    "CONNECT_FAILED_MESSAGE", "CONNECT_PROGRESS", "CONNECT_START_MESSAGE",
    "CONNECT_VERIFIED_PROGRESS", "DUPLICATE_CONFIRM_MESSAGE", "DUPLICATE_MESSAGE",
    "DUPLICATE_TITLE", "IDENTITY_CHANGED_MESSAGE", "LAUNCH_FAILED_TITLE",
    "NET_LIQUIDATION_MESSAGE", "PAPER_ARMED_CODE", "PAPER_LAUNCH_COMPONENT",
    "POSITIONS_MESSAGE", "PREFLIGHT_CHANGED_MESSAGE", "PREFLIGHT_PREFIX",
    "PREFLIGHT_TITLE", "PaperAccountReading", "PaperCandidateOrder",
    "PaperLaunchEvent", "PaperLaunchPublication", "PaperLaunchRefusal",
    "PaperLaunchRequest", "PaperOrderChannel", "PaperSessionBuildResult",
    "PaperSessionBuilder", "SHADOW_ACTIVE_MESSAGE", "SHADOW_ACTIVE_TITLE",
    "STALE_PLAN_MESSAGE", "WorkflowStateError",
]
