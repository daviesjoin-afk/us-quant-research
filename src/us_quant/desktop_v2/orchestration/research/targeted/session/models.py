"""The canonical targeted-session snapshot and its refusals.

The session half of the Targeted workspace used to be three loose attributes on
``MainWindow`` -- the target status string, the minute-evidence status string and
the last preflight result -- plus the target draft, which was not stored at all
because the window read the ``QLineEdit`` whenever it needed one.  This module is
the immutable value that replaces all four.

Why the draft is a field and not "whatever the editor currently says": the
operator's typing *is* the input the research evidence reads, and it is read
before anything has been applied or validated.  Naming it ``target_draft`` records
that honestly -- it is not an applied target, not a validated one, and not a
trading target.  A second reading of the widget would be a second truth.

Not here: the Shadow session, its engine, its positions and its fills.  The page
draws them, but they belong to the Shadow runtime (v2O-D), so they arrive at
render time through a provider and are never copied into this snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from us_quant.targeted_preflight import TargetPreflightResult

#: The status a target that has never been set shows.  ``MainWindow`` used to
#: spell this literal; it lives here now so the default and the rendering cannot
#: drift apart.
DEFAULT_TARGET_STATUS = "未指定"

#: What the minute-evidence line says before a valid symbol exists.  Kept
#: verbatim from the window's initialisation and its invalid-symbol branch, which
#: were two copies of one sentence.
DEFAULT_MINUTE_STATUS = (
    "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
)

#: The two refusal severities the session owns.  Deliberately two and not a
#: general framework: the invalid-symbol case is a *warning* because the operator
#: made an input mistake, while a refusal that protects a running Shadow session
#: or a live feed is *information* -- nothing is wrong, the request is simply not
#: available right now.  Collapsing them would tell the operator the wrong thing.
REFUSAL_WARNING = "warning"
REFUSAL_INFORMATION = "information"

#: The operator-facing text for the target commands and their three refusals,
#: kept verbatim from the retired ``MainWindow`` handlers so the operator is told
#: the same thing about the same condition.  They are here, next to the defaults,
#: rather than in the orchestrator: they are presentation wording, and the class
#: that decides *when* to say something should not also be where the sentence
#: lives.
INVALID_TITLE = "代码无效"
INVALID_MESSAGE = "请输入一个有效的美股或 ETF 代码。"
SHADOW_ACTIVE_TITLE = "影子会话运行中"
SHADOW_ACTIVE_MESSAGE = "请先停止当前影子会话，再切换指定标的。"
MARKET_LIVE_TITLE = "请先停止当前行情流"
MARKET_LIVE_MESSAGE = "停止当前行情流后，再切换本次针对性日内 T 标的。"
APPLY_LOG = "当前指定做 T 标的已切换为 {symbol}；没有默认代码或单一股票专用逻辑。"
SUBSCRIBE_LOG = (
    "本次针对性日内 T 标的设为 {symbol}；启动行情后仍需通过实时性与中概排除门。"
)
SUBSCRIBE_NOTE = "针对性日内 T：{symbol}"


@dataclass(frozen=True, slots=True)
class TargetedSessionSnapshot:
    """The targeted session's canonical desktop truth.

    Four facts and nothing else.  :attr:`preflight` is a *derived* fact: it is
    ``None`` until a refresh has succeeded, and it is committed only when the
    evaluator returned.  A failed refresh leaves this field untouched *and* raises
    -- the caller must learn that the verdict on screen is no longer fresh, rather
    than reading a stale one as current.
    """

    target_draft: str = ""
    target_status: str = DEFAULT_TARGET_STATUS
    minute_status: str = DEFAULT_MINUTE_STATUS
    preflight: TargetPreflightResult | None = None


__all__ = [
    "APPLY_LOG",
    "DEFAULT_MINUTE_STATUS",
    "DEFAULT_TARGET_STATUS",
    "INVALID_MESSAGE",
    "INVALID_TITLE",
    "MARKET_LIVE_MESSAGE",
    "MARKET_LIVE_TITLE",
    "REFUSAL_INFORMATION",
    "REFUSAL_WARNING",
    "SHADOW_ACTIVE_MESSAGE",
    "SHADOW_ACTIVE_TITLE",
    "SUBSCRIBE_LOG",
    "SUBSCRIBE_NOTE",
    "TargetedSessionSnapshot",
]
