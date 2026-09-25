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

    Semantics are exactly the three the window used to branch on, unchanged:

    1. an intraday-targeted-t *research* version is exploratory shadow use:
       real-time simulated evidence may be collected, it is not a promotion,
       and no broker order can ever be sent;
    2. a gate-passed paper-shadow version has cleared the evidence gate but
       still needs a fresh Paper account and real-time quotes;
    3. everything else is a hard evidence-gate block, and the gate's reason
       is shown.
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
    if version.gate_passed and version.status is StrategyStatus.PAPER_SHADOW:
        return (
            f"策略证据门：通过；已绑定 {version.strategy_id} "
            f"{version.semver}。仍需新鲜 Paper 账户与实时行情。"
        )
    return (
        "策略证据门：硬阻断。"
        f"{version.strategy_id} {version.semver}："
        f"{version.gate_reason}"
    )


__all__ = ["parse_clone_parameters", "strategy_account_notice"]
