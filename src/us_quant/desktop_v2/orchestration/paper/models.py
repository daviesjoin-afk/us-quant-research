"""The immutable facts and driven-port contracts of Paper orchestration.

Frozen plain data and declared Protocols only -- Qt-free and adapter-free.  Every
operator string is verbatim from the retired ``MainWindow`` handlers; a broker adapter,
a risk or execution application and a widget are never imported here.  The design
rationale lives in ``docs/DESKTOP_DECOMPOSITION.md`` §27.

The launch half and the active-session half share this file deliberately: both publish
through the same ``PaperSessionResult``, and the event shape one launch files under
``PAPER_SESSION_ARMED`` is the same shape a stream tick files a halt under.  Two event
types would mean two publication paths wearing different names.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
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

#: The component one launch's runtime event is filed under, and its code.
PAPER_LAUNCH_COMPONENT = "auto_quant"
PAPER_ARMED_CODE = "PAPER_SESSION_ARMED"

#: The component the *session's* own coordinator events are filed under.  Distinct from
#: the launch component above because the operator reads them as different things: one
#: is "a session was armed", the others are what the running session did.
PAPER_EXECUTION_COMPONENT = "paper_execution"

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

#: The one *invariant* message: the launch could not end its promotion claim after
#: ``publish_armed`` succeeded.  The session is live **and owned** -- taking the
#: promotion installs the owner before publication, so this is no longer an ownerless
#: session -- but a claim that is never released refuses every later reservation, so a
#: broken invariant is reported at error severity under its own code.
PAPER_PROMOTION_INVARIANT_TITLE = "Paper 会话发布后未能结束启动占用"
PAPER_PROMOTION_INVARIANT_CODE = "PAPER_PROMOTION_INVARIANT"
PAPER_PROMOTION_INVARIANT_MESSAGE = (
    "Paper 会话已发布且订单通道已接管，但启动占用未能释放；"
    "在重启客户端前，新的 Paper 启动会被拒绝：{error}"
)

#: The *rollback*'s invariant: publication was refused, and the promotion could not be
#: shown to have been given back.  Nothing published here, so the attempt stays in
#: flight -- finishing the rollback would dispose of a service that may still own the
#: slot and release PAPER while Shadow is no longer excluded.  Reported at error
#: severity under its own code, distinct from the published-session one above because
#: the operator's situation is different: that one is a live session, this is a stuck
#: launch.
PAPER_LAUNCH_ROLLBACK_TITLE = "Paper 启动回滚未能确认"
PAPER_LAUNCH_ROLLBACK_CODE = "PAPER_LAUNCH_ROLLBACK_FAILED"
PAPER_LAUNCH_ROLLBACK_MESSAGE = (
    "Paper 启动失败，且 promotion 回滚报告未释放任何占用；"
    "已保持 CONNECTING 与执行租约，不做进一步回滚，需人工处理：{error}"
)

#: The *release*'s invariant, v2O-E3's counterpart of the two above.  The active slot's
#: release is reserved before the workflow's lease gate is asked, so this is unreachable;
#: what it would leave behind -- PAPER released while an ownership is still held -- is the
#: one state the two-phase release exists to prevent, so it gets its own code rather than
#: being folded into the ordinary "ownership cannot be proved" refusal.
PAPER_RELEASE_INVARIANT_CODE = "PAPER_RELEASE_INVARIANT"
PAPER_RELEASE_INVARIANT_MESSAGE = (
    "Paper 会话的 active slot 释放未能提交，而执行租约已释放；"
    "在重启客户端前不要重新启动 Paper 会话：{error}"
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

#: The two entry-control confirmations, verbatim from the retired handlers.  They are
#: logged only after the workflow accepted the change, so the operator never reads
#: "paused" about a session whose phase disagreed.
PAUSE_SUCCEEDED_MESSAGE = (
    "自动量化已暂停新开仓；现有持仓的止损、止盈和时段退出继续运行。"
)
RESUME_SUCCEEDED_MESSAGE = "自动量化已恢复新开仓。"

#: The broker gates' text -- the sentences naming the failed account fact.
NET_LIQUIDATION_MESSAGE = "IBKR Paper 订单会话未返回有效净值"
POSITIONS_MESSAGE = "首期自动量化要求 Paper 账户启动时空仓；当前持仓：{symbols}"
CASH_MESSAGE = "IBKR Paper 订单会话未返回现金；禁止使用保证金借款代替现金"

#: Manual reconciliation (v2O-E3), verbatim from the retired window handlers.  The
#: started sentence is deliberately explicit that reconnecting is *not* resuming: the
#: operator's next decision is a separate, confirmed step.
RECONCILIATION_NO_SERVICE_MESSAGE = (
    "Paper 处于停机状态但没有可用的订单会话，无法对账；请重启客户端。"
)
RECONCILIATION_STARTED_MESSAGE = (
    "执行对账：正在重新连接 IBKR Paper 并读取开放订单、"
    "当日成交和当前持仓；不会自动恢复交易。"
)
RECONCILIATION_PROGRESS = "重新连接 IBKR Paper 并恢复订单快照…"
RECONCILIATION_START_MESSAGE = "IBKR Paper 重新对账中…"

#: The confirmation step.  ``RESUME_*_MESSAGE`` are refusals rather than errors: a
#: consumed, changed or missing proof is an operator condition, and the only honest
#: answer is a log line plus another reconciliation -- never a fabricated result.
RESUME_NOT_READY_MESSAGE = (
    "A fresh reconciliation proof is required before Paper can resume."
)
RESUME_EVIDENCE_MISSING_MESSAGE = (
    "Reconciliation proof is missing; run manual reconciliation again."
)
RESUME_PROGRESS = "Revalidating the complete IBKR Paper snapshot..."
RESUME_START_MESSAGE = "Paper reconciliation confirmation in progress..."

#: The zero-state proof.  Wording verbatim so the footer reads the same as before the
#: move.
FINALIZATION_PROGRESS = "Verifying complete Paper zero-state before disconnect..."
FINALIZATION_START_MESSAGE = "Paper safe finalization check in progress..."

#: The shutdown dispositions' own sentences.  Qt-free plain text: the window decides the
#: dialog title and shows this as its body, so the capability owns *why* the close is
#: refused without owning a widget.
SHUTDOWN_STOP_REQUESTED_MESSAGE = (
    "已请求安全停止 Paper 会话；请等待平仓、券商对账和最终确认完成后再关闭程序。"
)
SHUTDOWN_FINALIZATION_PENDING_MESSAGE = (
    "必须先完成安全停止和券商对账。停机状态需要人工对账与明确确认；"
    "客户端不会在未 finalized 时断开订单会话或退出。"
)
SHUTDOWN_MANUAL_RECOVERY_MESSAGE = (
    "Paper 会话已停机，只能通过人工对账与明确确认继续；"
    "客户端不会在未 finalized 时断开订单会话或退出。"
)
SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE = (
    "Paper 订单所有权无法确认已释放；客户端不会释放该所有权或正常退出。"
    "请重启客户端后重新启动 Paper 会话。"
)
SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE = (
    SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE + "\n\n{reason}"
)

#: The three *reasons* a shutdown can be blocked for, each a different situation for the
#: operator.  They are appended to the blocked message rather than replacing it, so the
#: standing instruction ("this process will not release it or exit normally") is always
#: the same sentence and only the diagnosis moves.
SHUTDOWN_LAUNCH_IN_FLIGHT_REASON = (
    "the launch attempt is still in flight: the workflow holds the Paper execution lease"
    " and a connected order candidate may already exist"
)
SHUTDOWN_UNPROVABLE_SESSION_REASON = (
    "an order service is owned but the workflow holds no session to prove anything about"
)
SHUTDOWN_CANDIDATE_OWNERSHIP_REASON = (
    "a connected order candidate is still tracked: the service owns two slots, and"
    " releasing the active one does not release a candidate"
)
SHUTDOWN_STOP_REFUSED_REASON = (
    "the orderly stop could not be requested and the session is still live"
)


class PaperShutdownDisposition(str, Enum):
    """What a close must do about the Paper session, decided without doing it.

    One value per *situation the operator has to be told apart*, not one per phase:
    ``WAITING_FOR_FINALIZATION`` covers both "a stop was just requested" and "the
    zero-state proof is still running", because the operator's instruction is the same
    (wait), while ``MANUAL_RECOVERY_REQUIRED`` and ``OWNERSHIP_BLOCKED`` are separated on
    purpose -- the first is "do the reconciliation", the second is "this process cannot
    fix it".
    """

    #: Every Paper ownership this capability could hold has been provably released, or
    #: was never taken.  The close may proceed.
    READY = "READY"

    #: An automatic route is running: an orderly stop, or the zero-state proof it leads
    #: to.  The admission gate must stay down while that finishes.
    WAITING_FOR_FINALIZATION = "WAITING_FOR_FINALIZATION"

    #: The session can only be left by the operator -- HALTED, RECONCILING or
    #: RECONCILING_READY -- and every step of that is a task, so the close has to hand
    #: the client back rather than hold the gate down.
    MANUAL_RECOVERY_REQUIRED = "MANUAL_RECOVERY_REQUIRED"

    #: The session reports finalized but an ownership cannot be shown to have been
    #: released.  Fail closed: nothing is forced, nothing is swallowed, and the process
    #: must not exit over it.
    OWNERSHIP_BLOCKED = "OWNERSHIP_BLOCKED"


@dataclass(frozen=True, slots=True)
class PaperShutdownResult:
    """One shutdown verdict: what the close should do, and what to tell the operator."""

    disposition: PaperShutdownDisposition
    message: str = ""


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
    """What the build seam returns: the started runtime and what arming it needs.

    The seam composes and starts the runtime but deliberately does **not** arm, so
    ``arm -> ensure -> publish -> promote`` is one sequence in the orchestrator.

    ``engine`` *is* the started runtime -- it satisfies ``PaperEngine`` -- and it is
    handed straight to ``publish_armed``.  There is deliberately no second field
    carrying the same object under a ``runtime`` name: that would have offered every
    caller a way to keep a runtime handle beside the workflow's own, which is exactly
    the second owner the capability boundary exists to prevent.
    """

    engine: PaperEngine
    orders: PaperOrderPort
    session_id: str
    candidate_count: int
    max_order_notional: Decimal


@dataclass(frozen=True, slots=True)
class PaperRuntimeEventRequest:
    """One runtime event the orchestrator asks the window to record.

    One shape for every operation, launch included.  The window owns the event store
    and is the only thing allowed to write it; the orchestrator owns *which* events a
    Paper operation produces, and requests each exactly once.
    """

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
    "DUPLICATE_TITLE", "FINALIZATION_PROGRESS", "FINALIZATION_START_MESSAGE",
    "IDENTITY_CHANGED_MESSAGE", "LAUNCH_FAILED_TITLE",
    "NET_LIQUIDATION_MESSAGE", "PAPER_ARMED_CODE", "PAPER_EXECUTION_COMPONENT",
    "PAPER_LAUNCH_COMPONENT",
    "PAPER_LAUNCH_ROLLBACK_CODE", "PAPER_LAUNCH_ROLLBACK_MESSAGE",
    "PAPER_LAUNCH_ROLLBACK_TITLE",
    "PAPER_PROMOTION_INVARIANT_CODE", "PAPER_PROMOTION_INVARIANT_MESSAGE",
    "PAPER_PROMOTION_INVARIANT_TITLE", "PAPER_RELEASE_INVARIANT_CODE",
    "PAPER_RELEASE_INVARIANT_MESSAGE", "PAPER_STRATEGY_INTEGRITY_CODE",
    "PAPER_STRATEGY_INTEGRITY_TITLE", "PAUSE_SUCCEEDED_MESSAGE", "POSITIONS_MESSAGE",
    "PREFLIGHT_CHANGED_MESSAGE", "PREFLIGHT_PREFIX", "PREFLIGHT_TITLE",
    "PaperAccountReading", "PaperCandidateOrder",
    "PaperLaunchIntegrityError", "PaperLaunchRefusal",
    "PaperLaunchRequest", "PaperOrderChannel", "PaperRuntimeEventRequest",
    "PaperSessionBuildResult",
    "PaperSessionBuilder", "PaperShutdownDisposition", "PaperShutdownResult",
    "PaperStrategyLaunchFact",
    "RECONCILIATION_NO_SERVICE_MESSAGE", "RECONCILIATION_PROGRESS",
    "RECONCILIATION_STARTED_MESSAGE", "RECONCILIATION_START_MESSAGE",
    "RESUME_EVIDENCE_MISSING_MESSAGE", "RESUME_NOT_READY_MESSAGE",
    "RESUME_PROGRESS", "RESUME_START_MESSAGE", "RESUME_SUCCEEDED_MESSAGE",
    "SHADOW_ACTIVE_MESSAGE",
    "SHADOW_ACTIVE_TITLE", "SHUTDOWN_FINALIZATION_PENDING_MESSAGE",
    "SHUTDOWN_CANDIDATE_OWNERSHIP_REASON",
    "SHUTDOWN_LAUNCH_IN_FLIGHT_REASON", "SHUTDOWN_MANUAL_RECOVERY_MESSAGE",
    "SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE",
    "SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE",
    "SHUTDOWN_STOP_REFUSED_REASON", "SHUTDOWN_STOP_REQUESTED_MESSAGE",
    "SHUTDOWN_UNPROVABLE_SESSION_REASON",
    "STALE_PLAN_MESSAGE", "WorkflowStateError",
]
