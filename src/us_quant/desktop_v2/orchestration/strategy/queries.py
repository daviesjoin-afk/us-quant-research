"""Qt-free queries for the strategy governance orchestration.

The orchestrator stays Qt-free at the logic level by keeping every rule that
can be expressed without widgets in this module.  Two rules live here:

* ``parse_clone_parameters`` -- the JSON adapter the clone action needs.  The
  parameters arrive from the page's text editor as a *string*; whether they
  decode and whether they decode to a JSON object are questions with one
  answer, and the window used to re-implement both halves of it.
* ``strategy_account_notice`` -- the strategy-evidence copy the account page
  shows for the version the operator is looking at.  This is the rule that had
  to leave ``MainWindow``: a window that explains strategy gates is a second
  strategy owner.

Both are pure functions over their arguments: no Qt, no repository, no
service, no caching.
"""

from __future__ import annotations

import json
from typing import Any

from us_quant.trading.domain.strategy import (
    StrategyStatus,
    StrategyVersion,
)


def parse_clone_parameters(parameters_json: str) -> dict[str, Any]:
    """Decode the editor's JSON text into a parameters mapping.

    Raises ``json.JSONDecodeError`` for text that is not JSON at all and
    ``ValueError`` for JSON that is not an object -- the two refusals the
    operator sees as distinct messages.  ``clone_version`` itself decides
    whether the decoded *values* are valid; this function decides nothing
    about them.
    """

    parameters = json.loads(parameters_json)
    if not isinstance(parameters, dict):
        raise ValueError("参数必须是 JSON 对象")
    return parameters


def strategy_account_notice(version: StrategyVersion) -> str:
    """The account-page notice for the version the operator is viewing.

    Describes the version's *status* and nothing more.  It used to branch on
    ``gate_passed`` and quote ``gate_reason``, which made this a place where a
    legacy boolean decided what the operator was told about their own
    authorisation.  That flag no longer decides anything: Paper lifecycle
    authorisation comes from the coverage chain and the lifecycle controller, and
    a notice that inferred it from a retired field would be stating an authority
    the system does not have.

    So the notice says what status the version is in, and -- where it matters --
    that a Paper session still has to pass the current authorisation check.
    """

    if (
        version.strategy_id == "intraday-targeted-t"
        and version.status is StrategyStatus.RESEARCH
    ):
        return (
            f"探索性影子模式：已绑定 {version.strategy_id} "
            f"{version.semver}。可收集实时模拟证据；"
            "不代表晋级，不会发送券商订单。"
        )
    if version.status is StrategyStatus.PAPER_SHADOW:
        return (
            f"当前版本状态为 Paper Shadow：{version.strategy_id} "
            f"{version.semver}。新的 Paper session 仍必须通过当前"
            "生命周期授权检查。"
        )
    if version.status is StrategyStatus.PAUSED:
        return (
            f"当前版本已暂停：{version.strategy_id} {version.semver}。"
        )
    if version.status is StrategyStatus.STOPPED:
        return (
            f"当前版本已停止：{version.strategy_id} {version.semver}。"
        )
    return (
        f"研究状态：{version.strategy_id} {version.semver}。"
        "尚未进入受治理的 Paper 生命周期。"
    )


__all__ = ["parse_clone_parameters", "strategy_account_notice"]
