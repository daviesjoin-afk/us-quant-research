from __future__ import annotations

from typing import Any, Mapping


class StrategyParameterError(ValueError):
    pass


def validate_strategy_parameters(
    strategy_id: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(parameters)
    if strategy_id == "dual-ma-trend":
        short = _integer(normalized, "short_window", 2, 250)
        long = _integer(normalized, "long_window", 3, 500)
        if short >= long:
            raise StrategyParameterError(
                "双均线参数要求 short_window < long_window"
            )
        _whole_shares(normalized)
    elif strategy_id == "donchian-breakout":
        entry = _integer(normalized, "entry_window", 5, 252)
        exit_window = _integer(normalized, "exit_window", 2, 126)
        if exit_window >= entry:
            raise StrategyParameterError(
                "唐奇安参数要求 exit_window < entry_window"
            )
        _whole_shares(normalized)
    elif strategy_id == "rsi-mean-reversion":
        _integer(normalized, "window", 2, 50)
        entry = _number(normalized, "entry_threshold", 1, 49)
        exit_value = _number(
            normalized, "exit_threshold", 2, 99
        )
        if entry >= exit_value:
            raise StrategyParameterError(
                "RSI 参数要求 entry_threshold < exit_threshold"
            )
        _whole_shares(normalized)
    elif strategy_id == "buy-hold":
        _whole_shares(normalized)
    elif strategy_id == "sector-momentum":
        lookbacks = _integer_list(
            normalized, "lookbacks", 20, 504
        )
        rebalance = _integer_list(
            normalized, "rebalance_days", 1, 63
        )
        holdings = _integer_list(
            normalized, "max_holdings", 1, 20
        )
        if not lookbacks or not rebalance or not holdings:
            raise StrategyParameterError(
                "横截面动量参数列表不能为空"
            )
        _whole_shares(normalized)
        gross = _number(
            normalized, "max_gross_risk_pct", 0.10, 1
        )
        if gross < 0.10:
            raise StrategyParameterError(
                "组合研究总风险上限不能低于单仓 10%"
            )
    elif strategy_id in {
        "intraday-targeted-t",
        "intraday-auto-rotation",
    }:
        lookback = _integer(
            normalized, "momentum_lookback_minutes", 2, 30
        )
        _integer(normalized, "warmup_minutes", 5, 60)
        _integer(normalized, "maximum_hold_minutes", 5, 180)
        _integer(normalized, "maximum_trades_per_day", 1, 20)
        _number(normalized, "max_position_fraction", 0.01, 0.10)
        _number(normalized, "min_order_notional", 1, 1000)
        _number(normalized, "commission_per_order", 0, 20)
        _number(normalized, "slippage_bps", 0, 100)
        _number(
            normalized, "maximum_spread_fraction", 0.0001, 0.02
        )
        minimum_momentum = _number(
            normalized, "minimum_momentum", 0.0001, 0.10
        )
        maximum_momentum = _number(
            normalized, "maximum_momentum", 0.001, 0.25
        )
        if minimum_momentum >= maximum_momentum:
            raise StrategyParameterError(
                "minimum_momentum 必须小于 maximum_momentum"
            )
        if strategy_id == "intraday-auto-rotation":
            normalized.setdefault("minimum_positive_steps", 0)
            positive_steps = _integer(
                normalized,
                "minimum_positive_steps",
                0,
                30,
            )
            if positive_steps > lookback:
                raise StrategyParameterError(
                    "minimum_positive_steps 不能大于动量回看分钟数"
                )
            normalized.setdefault("maximum_one_minute_move", "1")
            _number(
                normalized,
                "maximum_one_minute_move",
                0.001,
                1,
            )
            normalized.setdefault("entry_order_timeout_seconds", 90)
            _integer(
                normalized,
                "entry_order_timeout_seconds",
                15,
                300,
            )
        _number(normalized, "profit_target", 0.001, 0.20)
        _number(normalized, "stop_loss", 0.001, 0.20)
        _number(normalized, "trailing_stop", 0.001, 0.20)
        _whole_shares(normalized)
    return normalized


def strategy_schema_summary(strategy_id: str) -> str:
    summaries = {
        "buy-hold": "whole_shares: bool",
        "dual-ma-trend": (
            "short_window: 2..250；long_window: 3..500；short < long"
        ),
        "donchian-breakout": (
            "entry_window: 5..252；exit_window: 2..126；exit < entry"
        ),
        "rsi-mean-reversion": (
            "window: 2..50；entry_threshold: 1..49；"
            "exit_threshold: 2..99；entry < exit"
        ),
        "sector-momentum": (
            "lookbacks/rebalance_days/max_holdings 为非空整数列表；"
            "max_gross_risk_pct: 0.10..1.00"
        ),
        "intraday-targeted-t": (
            "标的由运行时输入；整股；lookback 2..30 分钟；"
            "最长持有 5..180 分钟；每日 1..20 笔；"
            "点差、动量、止盈止损和成本均版本化"
        ),
        "intraday-auto-rotation": (
            "候选由广域扫描动态生成；整股；单持仓自动轮动；"
            "lookback 2..30 分钟；最长持有 5..180 分钟；"
            "正收益步数和单分钟跳变过滤；"
            "Paper 限价单、点差、止盈止损和成本均版本化"
        ),
    }
    return summaries.get(
        strategy_id,
        "自定义策略：仅校验 JSON 对象；尚无结构化运行工厂",
    )


def _integer(
    parameters: dict[str, Any],
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    value = parameters.get(name)
    if isinstance(value, bool):
        raise StrategyParameterError(f"{name} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise StrategyParameterError(f"{name} 必须是整数") from error
    if str(parsed) != str(value):
        raise StrategyParameterError(f"{name} 必须是整数")
    if not minimum <= parsed <= maximum:
        raise StrategyParameterError(
            f"{name} 必须在 {minimum}..{maximum} 内"
        )
    parameters[name] = parsed
    return parsed


def _number(
    parameters: dict[str, Any],
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(parameters.get(name))
    except (TypeError, ValueError) as error:
        raise StrategyParameterError(f"{name} 必须是数字") from error
    if not minimum <= parsed <= maximum:
        raise StrategyParameterError(
            f"{name} 必须在 {minimum}..{maximum} 内"
        )
    parameters[name] = str(parsed)
    return parsed


def _integer_list(
    parameters: dict[str, Any],
    name: str,
    minimum: int,
    maximum: int,
) -> list[int]:
    value = parameters.get(name)
    if not isinstance(value, list):
        raise StrategyParameterError(f"{name} 必须是整数列表")
    parsed = []
    for item in value:
        if isinstance(item, bool):
            raise StrategyParameterError(f"{name} 必须是整数列表")
        try:
            number = int(item)
        except (TypeError, ValueError) as error:
            raise StrategyParameterError(
                f"{name} 必须是整数列表"
            ) from error
        if str(number) != str(item):
            raise StrategyParameterError(f"{name} 必须是整数列表")
        if not minimum <= number <= maximum:
            raise StrategyParameterError(
                f"{name} 的值必须在 {minimum}..{maximum} 内"
            )
        parsed.append(number)
    parameters[name] = parsed
    return parsed


def _whole_shares(parameters: dict[str, Any]) -> None:
    value = parameters.get("whole_shares", True)
    if value is not True:
        raise StrategyParameterError("whole_shares 必须保持 true")
    parameters["whole_shares"] = True
