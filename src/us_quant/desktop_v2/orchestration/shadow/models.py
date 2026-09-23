"""The immutable facts Shadow orchestration consumes and publishes.

Four shapes, all frozen, all plain data: the frozen inputs of a run, one refusal,
one runtime event the window should record, and the sentences the operator is
told.  This module is deliberately Qt-free and engine-free -- nothing here holds a
``ShadowPaperEngine``, a store, a widget or a callback, because a fact that
carried its owner would make the boundary unverifiable.

Two rules are load-bearing:

* **the operator-facing wording lives next to the condition that produces it.**
  Every string below is kept verbatim from the retired ``MainWindow`` handler, so
  an operator is told exactly what they were told before about exactly the same
  condition.  They are here rather than in the orchestrator for the reason the
  targeted session round put its refusals here: the class that decides *when* to
  say something should not also be where the sentence lives.
* **the run's inputs are read once and frozen.**  ``ShadowStartRequest`` is built
  only after every gate has passed, so a run cannot be assembled from a strategy
  the operator changed halfway through, or from a target typed after the gates
  were checked.  ``symbol_risk_multipliers`` is a tuple of pairs rather than a
  mapping so the window cannot keep mutating the dict it handed over and change
  the run behind the orchestrator's back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

#: The service the runtime events are filed under.  Kept verbatim: an operator
#: reading the event log is reading the same component name they always were.
SHADOW_COMPONENT = "shadow_paper"

#: Why a start was refused, as the dialog the operator sees.  Each pair is the
#: exact title and message the retired handler showed for that condition.
RUNTIME_BUSY_TITLE = "IBKR Paper 自动量化运行中"
RUNTIME_BUSY_MESSAGE = "同一资金真值不能同时运行自动量化和内部仿真。"
STRATEGY_TITLE = "请选择策略版本"
STRATEGY_MESSAGE = "请先在“策略目录与版本”中选择指定标的日内 T 版本。"
STRATEGY_MISMATCH_TITLE = "策略类型不匹配"
STRATEGY_MISMATCH_MESSAGE = (
    "针对性日内 T 必须绑定“指定标的日内 T”策略版本。"
    "请先在“策略目录与版本”中选择该策略。"
)
STATUS_TITLE = "策略状态不可运行"
STATUS_MESSAGE = (
    "只有 research 探索版本或已通过证据门的 Paper Shadow "
    "版本可以运行内部影子盘。停止或暂停版本不能启动。"
)
CAPITAL_TITLE = "缺少 IBKR Paper 资金真值"
CAPITAL_MESSAGE = (
    "请先在“账户与持仓”页读取 IBKR Paper 账户。"
    "模拟盘必须使用券商返回的 NetLiquidation 建账，"
    "不会用任何历史研究资金情景代替。"
)
MARKET_TITLE = "行情门未通过"
MARKET_MESSAGE = (
    "请先在“实时行情”页启动外部实时流，并等待至少一个"
    "代码显示 READY。延迟或 stale 行情不能启动日内影子盘。"
)
UNIVERSE_TITLE = "标的门未通过"
UNIVERSE_MISSING_MESSAGE = "缺少已核验标的池，无法执行“不做中概股”硬过滤。"
UNIVERSE_INELIGIBLE_MESSAGE = (
    "{symbol} 不在当前已排除中概风险的研究标的池内。"
    "请先刷新广域标的池与国家证据。"
)
SYMBOL_TITLE = "请输入标的"
SYMBOL_MESSAGE = "针对性日内 T 需要输入一个有效的美股或 ETF 代码。"
QUOTE_TITLE = "目标行情未就绪"
QUOTE_MESSAGE = (
    "{symbol} 尚未获得 fresh bid/ask。"
    "请先点击“订阅该标的行情”，等行情页显示 READY。"
)

#: The dialog when the engine could not be started at all, and when the
#: competing Paper runtime is already up.
START_FAILED_TITLE = "内部影子仿真未启动"

#: The event codes and the two operator-facing message templates.  ``{status}``,
#: ``{symbol}``, ``{semver}`` and ``{capital}`` are filled by the queries module;
#: the templates live here so the wording has one home.
SHADOW_START_CODE = "SHADOW_START"
SHADOW_STOP_CODE = "SHADOW_STOP"
START_EVENT_MESSAGE = (
    "针对性日内 T 影子盘启动；模式 {status}；标的 {symbol}；"
    "初始资金 {capital} 来自 IBKR Paper NetLiquidation；无券商订单权限"
)
START_LOG_MESSAGE = (
    "针对性日内 T 影子盘已启动：{symbol}，策略 {semver}；"
    "资金 {capital} 来自 IBKR Paper；等待分钟预热。"
)
STOP_EVENT_MESSAGE = "内部影子盘停止；最后持仓按最后有效 mark 影子平仓"


@dataclass(frozen=True, slots=True)
class ShadowStartRefusal:
    """One refusal, shaped as the dialog the operator must see."""

    title: str
    message: str


@dataclass(frozen=True, slots=True)
class ShadowCapitalFact:
    """The Paper reading one run may be sized from.

    The *amount* only.  The account it came from is deliberately not carried
    here: the retired handler read the alias while building the engine, after
    every gate had passed, so a refusing start never touched the portfolio.  A
    single object holding both would force that read at gate time, which is a
    behaviour change rather than a tidy-up.
    """

    net_liquidation: Decimal


@dataclass(frozen=True, slots=True)
class ShadowStartRequest:
    """The frozen gate inputs of one internal simulation.

    Built only after every gate passed, and every value in it was read once, so a
    run cannot be assembled from a strategy the operator changed halfway through
    or from a target typed after the gates were checked.

    ``initial_cash`` is a broker fact rather than a research one: the simulator is
    sized from a real Paper net liquidation, never from a scenario figure.
    ``capital_source`` is absent by design -- the provenance names the account the
    amount came from, and it is composed at engine-build time from the alias
    provider.  It is metadata about the capital, not an input any gate reads.
    """

    strategy_version_id: str
    parameter_hash: str
    semver: str
    strategy_status: str
    parameters: Mapping[str, Any]
    target_symbol: str
    initial_cash: Decimal
    daily_loss_limit: Decimal
    symbol_risk_multipliers: tuple[tuple[str, Decimal], ...] = ()

    def risk_multipliers(self) -> dict[str, Decimal]:
        """The config-facing projection; the builder takes a mapping."""

        return dict(self.symbol_risk_multipliers)


@dataclass(frozen=True, slots=True)
class ShadowRuntimeEvent:
    """One runtime event the orchestrator wants recorded.

    The orchestrator does not hold the event store: it asks, and the window
    writes.  That keeps ``Shadow -> System`` from becoming a dependency.
    """

    severity: str
    component: str
    code: str
    message: str


class ShadowLease(Protocol):
    """The internal-only execution lease, as Shadow sees it.

    ``ShadowWorkflowController`` already satisfies this, and no adapter is
    constructed.  The protocol exists because the concrete class lives in
    ``desktop_v2.workflows`` next to ``PaperWorkflowController``, and Shadow
    orchestration may not import a module that names the Paper lifecycle.  Its
    only job is to make the dependency legible and to close the import.

    The lease is *shared*: acquiring Shadow is refused while Paper holds it, so
    this layer must never treat a failure to acquire as something to retry.
    """

    @property
    def active(self) -> bool:
        ...

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


__all__ = [
    "CAPITAL_MESSAGE",
    "CAPITAL_TITLE",
    "MARKET_MESSAGE",
    "MARKET_TITLE",
    "QUOTE_MESSAGE",
    "QUOTE_TITLE",
    "RUNTIME_BUSY_MESSAGE",
    "RUNTIME_BUSY_TITLE",
    "SHADOW_COMPONENT",
    "SHADOW_START_CODE",
    "SHADOW_STOP_CODE",
    "START_EVENT_MESSAGE",
    "START_FAILED_TITLE",
    "START_LOG_MESSAGE",
    "STATUS_MESSAGE",
    "STATUS_TITLE",
    "STOP_EVENT_MESSAGE",
    "STRATEGY_MISMATCH_MESSAGE",
    "STRATEGY_MISMATCH_TITLE",
    "STRATEGY_MESSAGE",
    "STRATEGY_TITLE",
    "SYMBOL_MESSAGE",
    "SYMBOL_TITLE",
    "UNIVERSE_INELIGIBLE_MESSAGE",
    "UNIVERSE_MISSING_MESSAGE",
    "UNIVERSE_TITLE",
    "ShadowCapitalFact",
    "ShadowLease",
    "ShadowRuntimeEvent",
    "ShadowStartRefusal",
    "ShadowStartRequest",
]
