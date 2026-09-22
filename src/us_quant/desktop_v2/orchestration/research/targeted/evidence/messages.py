"""Every operator-facing string the targeted evidence capability publishes.

One place for the vocabulary, because these strings are contract: the refusal
wording is what the operator reads, and the completion lines are what the footer
and the runtime event log record.  A migration that rewords them has changed the
product, so they are gathered here where a reviewer sees the whole set at once
rather than scattered through the code that happens to raise them.

Two groups, and the second is the load-bearing one:

* **refusals and progress labels** -- shown before a run starts;
* **completion text** -- built from a *result*, which is why the builders here are
  functions rather than constants.  The Cross Section round shipped a defect where
  a completion message formatted a numeric *string* with ``{:+.2%}`` and raised
  ``ValueError`` **after** the truth had already moved and the page had already
  repainted: a "successful" run fired its artifact bridge and then failed from a
  logger.  So the ordering rule the orchestrator follows is that these are called
  while the candidate snapshot is being projected, before anything is committed,
  and a raise here is therefore a failure to publish rather than a half-published
  run.

Pure and Qt-free: given a result, a builder returns a value.  Nothing here reads
state, holds a store or touches a widget.
"""

from __future__ import annotations

import re

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRuntimeEvent,
    TargetedRobustnessBundle,
)
from us_quant.targeted_replay import TargetedReplayResult

#: The one symbol shape this workspace accepts: a leading letter, then up to nine
#: more letters, digits, dots or hyphens.  Frozen -- widening or narrowing what an
#: operator may research is not this round's business.  It is spelled here rather
#: than in ``models`` because it is the wording of the "代码无效" refusal, and the
#: title and the shape must be read together.
SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")


def is_valid_symbol(symbol: str) -> bool:
    """Whether ``symbol`` is a well-formed ticker for this workspace."""

    return SYMBOL_PATTERN.fullmatch(symbol) is not None


MISSING_STRATEGY_TITLE = "缺少策略版本"
MISSING_STRATEGY_MESSAGE = "请选择指定标的日内 T 策略版本。"

#: The two invalid-symbol refusals name the action the operator asked for, which
#: is how they tell which button they clicked.
INVALID_SYMBOL_TITLE = "代码无效"
REPLAY_INVALID_SYMBOL_MESSAGE = "请输入需要回放的股票或 ETF 代码。"
ROBUSTNESS_INVALID_SYMBOL_MESSAGE = "请输入需要评估的股票或 ETF 代码。"

INELIGIBLE_TITLE = "标的门未通过"
INELIGIBLE_MESSAGE = "{symbol} 未通过当前非中概研究资格门。"

REPLAY_START_MESSAGE = "{symbol} 分钟回放开始…"
ROBUSTNESS_START_MESSAGE = "{symbol} 多日稳健性评估开始…"


def replay_event(result: TargetedReplayResult) -> TargetedEvidenceRuntimeEvent:
    """The recorded event one successful replay produces."""

    return TargetedEvidenceRuntimeEvent(
        severity="info",
        component="targeted_replay",
        code="REPLAY_COMPLETE",
        message=(
            f"{result.symbol} run {result.run_id[:8]} 完成；"
            f"{result.row_count} 行；收益 {result.total_return:.2%}；"
            "券商订单 0"
        ),
    )


def replay_log(result: TargetedReplayResult) -> str:
    """The footer line one successful replay produces."""

    return (
        f"{result.symbol} 分钟回放完成：收益 "
        f"{result.total_return:.2%}，最大回撤 "
        f"{result.maximum_drawdown:.2%}，成交 {len(result.fills)} 笔。"
    )


def bundle_event(
    bundle: TargetedRobustnessBundle,
) -> TargetedEvidenceRuntimeEvent:
    """The recorded event one successful suite produces."""

    robustness = bundle.robustness
    return TargetedEvidenceRuntimeEvent(
        severity="info",
        component="targeted_robustness",
        code="ROBUSTNESS_COMPLETE",
        message=(
            f"{robustness.symbol} run {robustness.run_id[:8]} 完成；"
            f"有效独立会话 {robustness.usable_sessions}/"
            f"{robustness.total_sessions}；"
            f"证据 {robustness.evidence_grade}；"
            f"过拟合诊断 {bundle.overfit.evidence_grade}；"
            f"数据质量 {bundle.data_quality.evidence_grade}；"
            f"执行压力 {bundle.execution_stress.evidence_grade}；"
            f"独立评审 {bundle.review.decision}；"
            "自动晋级 0"
        ),
    )


def bundle_log(bundle: TargetedRobustnessBundle) -> str:
    """The footer line one successful suite produces.

    The walk-forward clause is conditional on one having been produced at all,
    which is the same condition that decides whether that family is committed.
    """

    robustness = bundle.robustness
    review = bundle.review
    walk_forward = bundle.walk_forward
    return (
        f"{robustness.symbol} 多日稳健性评估完成："
        f"{robustness.usable_sessions}/"
        f"{robustness.total_sessions} 个有效会话，"
        f"参数收益方向一致率 "
        f"{robustness.sign_stability_fraction:.0%}；"
        + (
            f"时间隔离测试超额 "
            f"{walk_forward.out_of_sample_excess_return:+.2%}。"
            if walk_forward is not None
            else "未达到 20 会话，未运行时间隔离验证。"
        )
        + f" 过拟合诊断：{bundle.overfit.evidence_grade}。"
        + (
            f" 独立评审通过门 {review.passed_gates}/"
            f"{len(review.gates)}，结论 {review.decision}。"
        )
    )


__all__ = [
    "INELIGIBLE_MESSAGE",
    "INELIGIBLE_TITLE",
    "INVALID_SYMBOL_TITLE",
    "MISSING_STRATEGY_MESSAGE",
    "MISSING_STRATEGY_TITLE",
    "REPLAY_INVALID_SYMBOL_MESSAGE",
    "REPLAY_START_MESSAGE",
    "ROBUSTNESS_INVALID_SYMBOL_MESSAGE",
    "ROBUSTNESS_START_MESSAGE",
    "SYMBOL_PATTERN",
    "bundle_event",
    "bundle_log",
    "is_valid_symbol",
    "replay_event",
    "replay_log",
]
