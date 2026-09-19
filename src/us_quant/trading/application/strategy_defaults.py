"""The built-in strategy catalogue, moved verbatim from ``seed_defaults``.

Every value below is copied character for character from the retired
``StrategyRegistry.seed_defaults``: ids, names, descriptions, semver strings,
raw parameter literals, universe and code hashes, risk budgets and gate
reasons.  This is a relocation, not a research revision.

Two things in here look wrong and are deliberately left alone:

* **The code hashes look stale.**  ``unverified-local-source-0.7.0`` appears
  on strategies written long after 0.7.0 and must stay that way.  Re-stamping
  a code hash would silently relabel old evidence as current-source evidence,
  which is exactly the kind of laundering the hash exists to prevent.  If the
  hash is wrong, the right fix is a new version with new evidence, not an edit
  to the record.
* **Raw parameter literals are not normalised here.**  They are handed to
  ``validate_strategy_parameters`` by the application, exactly as before, and
  the *validated* form is what gets hashed and stored.  ``"50"`` therefore
  becomes ``"50.0"`` and ``"0.010"`` becomes ``"0.01"`` -- reproducing that is
  the point, because the stored ``parameter_hash`` of every already-governed
  version depends on it.

``tests/test_trading_strategy_application`` boots this catalogue against a
fresh store and compares every field with ``tests/fixtures/
strategy_seed_baseline.json``, which was captured from the retired module
before it was deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus


@dataclass(frozen=True, slots=True)
class StrategySeed:
    """One default version, described rather than constructed.

    Kept as data so the catalogue can be read, diffed and compared against the
    captured baseline without importing the application service that writes
    it.
    """

    strategy_id: str
    name: str
    description: str
    semver: str
    parameters: Mapping[str, Any]
    universe_hash: str
    code_hash: str
    risk_budget_pct: float
    status: StrategyStatus = StrategyStatus.RESEARCH
    mode: StrategyMode = StrategyMode.RESEARCH
    gate_passed: bool = False
    gate_reason: str = "尚未通过研究晋级门"


DEFAULT_STRATEGY_SEEDS: tuple[StrategySeed, ...] = (
    StrategySeed(
        strategy_id="sector-momentum",
        name="板块龙头横截面动量",
        description=(
            "当前上市龙头/优质二线的探索性 walk-forward；"
            "统一整股和10%总风险约束后才能晋级"
        ),
        semver="2.0.0-research",
        parameters={
            "lookbacks": [63, 126],
            "rebalance_days": [5, 21],
            "max_holdings": [3, 5],
            "whole_shares": True,
            "max_gross_risk_pct": 0.10,
            "china_concept": "fail_closed",
            "max_substitution_holding_days": 5,
        },
        universe_hash="unverified-current-listed-snapshot",
        code_hash="unverified-local-source-0.7.0",
        risk_budget_pct=0.10,
        gate_reason=(
            "复权价不可执行；历史时点股票池、DSR/PBO 尚未达标"
        ),
    ),
    StrategySeed(
        strategy_id="sector-momentum",
        name="板块龙头横截面动量",
        description=(
            "多板块整股组合研究；研究总风险与单仓风险独立，"
            "替代执行品使用通用持有期硬门"
        ),
        semver="2.1.0-research",
        parameters={
            "lookbacks": [63, 126],
            "rebalance_days": [5, 21],
            "max_holdings": [3, 5],
            "whole_shares": True,
            "max_gross_risk_pct": 0.50,
            "max_position_risk_pct": 0.10,
            "china_concept": "fail_closed",
            "max_substitution_holding_days": 5,
        },
        universe_hash="unverified-current-listed-snapshot",
        code_hash="unverified-local-source-0.8.1",
        risk_budget_pct=0.10,
        gate_reason=(
            "组合预算已修复；复权价、历史时点股票池和 DSR/PBO "
            "仍未达到晋级条件"
        ),
    ),
    StrategySeed(
        strategy_id="intraday-targeted-t",
        name="指定标的日内 T",
        description=(
            "标的由用户在每次运行时输入；使用该标的自身的实时行情、"
            "整股定仓、点差门、止盈止损、移动止损与持有时间上限。"
        ),
        semver="1.3.0-research",
        parameters={
            "momentum_lookback_minutes": 5,
            "warmup_minutes": 10,
            "maximum_hold_minutes": 45,
            "maximum_trades_per_day": 4,
            "max_position_fraction": "0.10",
            "min_order_notional": "50",
            "commission_per_order": "0.35",
            "slippage_bps": "2",
            "maximum_spread_fraction": "0.002",
            "minimum_momentum": "0.0035",
            "maximum_momentum": "0.025",
            "profit_target": "0.012",
            "stop_loss": "0.007",
            "trailing_stop": "0.006",
            "whole_shares": True,
        },
        universe_hash="runtime-user-selected-non-china-symbol",
        code_hash="unverified-local-source-0.10.0",
        risk_budget_pct=0.10,
        gate_reason=(
            "已支持独立交易日与参数扰动证据；仍需至少 20 个"
            "真实会话及人工复核，暂不晋级自动券商执行。"
        ),
    ),
    StrategySeed(
        strategy_id="intraday-auto-rotation",
        name="自动多标的日内轮动",
        description=(
            "候选由最新广域扫描动态生成；在 fresh 实时行情中选择"
            "最强合格动量，按整股与单仓风险预算提交 IBKR Paper "
            "限价单。当前最多同时持有一只，禁止 Live、做空和借款。"
        ),
        semver="1.0.0-research",
        parameters={
            "momentum_lookback_minutes": 5,
            "warmup_minutes": 10,
            "maximum_hold_minutes": 45,
            "maximum_trades_per_day": 8,
            "max_position_fraction": "0.08",
            "min_order_notional": "50",
            "commission_per_order": "0.35",
            "slippage_bps": "3",
            "maximum_spread_fraction": "0.002",
            "minimum_momentum": "0.0035",
            "maximum_momentum": "0.025",
            "profit_target": "0.012",
            "stop_loss": "0.007",
            "trailing_stop": "0.006",
            "whole_shares": True,
        },
        universe_hash="runtime-broad-scan-non-china-candidates",
        code_hash="unverified-local-source-0.16.0",
        risk_budget_pct=0.08,
        gate_reason=(
            "Paper 自动订单适配与成交对账处于研究阶段；"
            "必须由用户逐会话武装，禁止 Live。"
        ),
    ),
    StrategySeed(
        strategy_id="intraday-auto-rotation",
        name="自动多标的日内轮动",
        description=(
            "在动态候选中筛选连续动量，过滤单分钟异常跳变，"
            "收紧点差和仓位，并通过 IBKR Paper 限价单验证。"
        ),
        semver="1.1.0-research",
        parameters={
            "momentum_lookback_minutes": 5,
            "warmup_minutes": 15,
            "maximum_hold_minutes": 35,
            "maximum_trades_per_day": 6,
            "max_position_fraction": "0.05",
            "min_order_notional": "50",
            "commission_per_order": "0.35",
            "slippage_bps": "3",
            "maximum_spread_fraction": "0.0015",
            "minimum_momentum": "0.0025",
            "maximum_momentum": "0.018",
            "minimum_positive_steps": 3,
            "maximum_one_minute_move": "0.012",
            "entry_order_timeout_seconds": 45,
            "profit_target": "0.010",
            "stop_loss": "0.006",
            "trailing_stop": "0.0045",
            "whole_shares": True,
        },
        universe_hash="runtime-broad-scan-non-china-candidates",
        code_hash="unverified-local-source-0.17.0",
        risk_budget_pct=0.05,
        gate_reason=(
            "连续动量和异常跳变过滤仍需在至少 20 个真实 Paper "
            "会话中做样本外验证；禁止 Live。"
        ),
    ),
    StrategySeed(
        strategy_id="intraday-auto-rotation",
        name="Automated intraday rotation with market regime gate",
        description=(
            "Research-only Paper candidate: new BUY intents require fresh "
            "non-negative one-minute SPY and QQQ reference returns; "
            "existing-position exits remain independent."
        ),
        semver="1.2.0-research",
        parameters={
            "momentum_lookback_minutes": 5,
            "warmup_minutes": 15,
            "maximum_hold_minutes": 35,
            "maximum_trades_per_day": 6,
            "max_position_fraction": "0.05",
            "min_order_notional": "50",
            "commission_per_order": "0.35",
            "slippage_bps": "3",
            "maximum_spread_fraction": "0.0015",
            "minimum_momentum": "0.0025",
            "maximum_momentum": "0.018",
            "minimum_positive_steps": 3,
            "maximum_one_minute_move": "0.012",
            "entry_order_timeout_seconds": 45,
            "market_reference_symbols": ["SPY", "QQQ"],
            "profit_target": "0.010",
            "stop_loss": "0.006",
            "trailing_stop": "0.0045",
            "whole_shares": True,
        },
        universe_hash="runtime-broad-scan-non-china-candidates",
        code_hash="unverified-local-source-0.18.0",
        risk_budget_pct=0.05,
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        gate_passed=False,
        gate_reason=(
            "Market regime gate is a research control only; it has not "
            "passed independent evidence review and cannot be promoted."
        ),
    ),
    StrategySeed(
        strategy_id="legacy-sector-momentum",
        name="旧横截面结果 +205.6%",
        description="保留供审计；不得运行或晋级",
        semver="1.0.0-invalid",
        parameters={"legacy_artifact": True},
        universe_hash="legacy-current-listed",
        code_hash="legacy-v1",
        risk_budget_pct=0.10,
        status=StrategyStatus.LEGACY_INVALIDATED,
        gate_reason=(
            "替代品风险折算错误、违反10%风控、幸存者偏差"
        ),
    ),
    StrategySeed(
        strategy_id="buy-hold",
        name="买入并持有基准",
        description="单标的整股买入持有；用于比较策略增益和成本。",
        semver="1.0.0-research",
        parameters={"whole_shares": True},
        universe_hash="single-symbol-at-run-time",
        code_hash="unverified-local-source-0.7.0",
        risk_budget_pct=0.10,
        gate_reason="基准模型；不得作为自动交易策略晋级",
    ),
    StrategySeed(
        strategy_id="dual-ma-trend",
        name="双均线趋势",
        description="20/100 日均线多头持有，否则现金。",
        semver="1.0.0-research",
        parameters={
            "short_window": 20,
            "long_window": 100,
            "whole_shares": True,
        },
        universe_hash="single-symbol-at-run-time",
        code_hash="unverified-local-source-0.7.0",
        risk_budget_pct=0.10,
        gate_reason="仅单标的复权日K研究代理；尚未通过走样本外门",
    ),
    StrategySeed(
        strategy_id="donchian-breakout",
        name="唐奇安突破",
        description="55 日高点突破进入，20 日低点退出。",
        semver="1.0.0-research",
        parameters={
            "entry_window": 55,
            "exit_window": 20,
            "whole_shares": True,
        },
        universe_hash="single-symbol-at-run-time",
        code_hash="unverified-local-source-0.7.0",
        risk_budget_pct=0.10,
        gate_reason="日K不能证明盘中止损成交；尚未通过成本压力测试",
    ),
    StrategySeed(
        strategy_id="rsi-mean-reversion",
        name="RSI 短期均值回归",
        description="RSI(5) 超跌进入，恢复至 55 退出。",
        semver="1.0.0-research",
        parameters={
            "window": 5,
            "entry_threshold": "25",
            "exit_threshold": "55",
            "whole_shares": True,
        },
        universe_hash="single-symbol-at-run-time",
        code_hash="unverified-local-source-0.7.0",
        risk_budget_pct=0.10,
        gate_reason="仅单标的复权日K研究代理；最长持有与压力门待补",
    ),
)


__all__ = ["DEFAULT_STRATEGY_SEEDS", "StrategySeed"]
