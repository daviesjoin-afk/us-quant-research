from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from us_quant.sqlite_support import connect_sqlite
from us_quant.strategy_schema import validate_strategy_parameters


STRATEGY_STATUSES = {
    "research",
    "paper_shadow",
    "paused",
    "stopped",
    "legacy_invalidated",
}
ALLOWED_TRANSITIONS = {
    "research": {"paper_shadow", "stopped"},
    "paper_shadow": {"paused", "stopped"},
    "paused": {"paper_shadow", "stopped"},
    "stopped": set(),
    "legacy_invalidated": set(),
}


class StrategyRegistryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    strategy_id: str
    name: str
    description: str
    version_id: str
    semver: str
    status: str
    mode: str
    parameters: dict[str, Any]
    parameter_hash: str
    universe_hash: str
    code_hash: str
    risk_budget_pct: float
    gate_passed: bool
    gate_reason: str
    created_at: str
    updated_at: str


class StrategyRegistry:
    """Versioned strategy governance with no order execution surface."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def register(
        self,
        *,
        strategy_id: str,
        name: str,
        description: str,
        semver: str,
        parameters: dict[str, Any],
        universe_hash: str,
        code_hash: str,
        risk_budget_pct: float,
        status: str = "research",
        mode: str = "research",
        gate_passed: bool = False,
        gate_reason: str = "尚未通过研究晋级门",
    ) -> StrategyRecord:
        if status not in STRATEGY_STATUSES:
            raise ValueError(f"unsupported strategy status: {status}")
        if mode not in {"research", "paper_shadow"}:
            raise ValueError("only research and paper_shadow are allowed")
        if gate_passed:
            raise StrategyRegistryError(
                "调用方不能自行声明晋级门通过；当前独立 gate evaluator 尚未启用"
            )
        if not 0 < risk_budget_pct <= 0.10:
            raise ValueError(
                "strategy risk budget must be in (0, 10%]"
            )
        parameters = validate_strategy_parameters(
            strategy_id, parameters
        )
        now = datetime.now(timezone.utc).isoformat()
        version_id = str(uuid4())
        canonical = _canonical_parameters(parameters)
        parameter_hash = sha256(canonical.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO strategy_definition (
                        strategy_id, name, description, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (strategy_id, name, description, now),
                )
                existing = connection.execute(
                    """
                    SELECT 1 FROM strategy_version
                    WHERE strategy_id = ? AND semver = ?
                    """,
                    (strategy_id, semver),
                ).fetchone()
                if existing:
                    raise StrategyRegistryError(
                        f"{strategy_id} {semver} already exists"
                    )
                connection.execute(
                    """
                    INSERT INTO strategy_version (
                        version_id, strategy_id, semver,
                        parameters_json, parameter_hash,
                        universe_hash, code_hash, risk_budget_pct,
                        gate_passed, gate_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        strategy_id,
                        semver,
                        canonical,
                        parameter_hash,
                        universe_hash,
                        code_hash,
                        str(risk_budget_pct),
                        int(gate_passed),
                        gate_reason,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO strategy_deployment (
                        deployment_id, version_id, status, mode,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        version_id,
                        status,
                        mode,
                        now,
                    ),
                )
                self._audit(
                    connection,
                    strategy_id=strategy_id,
                    version_id=version_id,
                    event="registered",
                    detail=f"{semver} / {status}",
                    occurred_at=now,
                )
        return self.get_version(version_id)

    def clone_version(
        self,
        version_id: str,
        *,
        semver: str,
        parameters: dict[str, Any],
    ) -> StrategyRecord:
        source = self.get_version(version_id)
        if source.status == "legacy_invalidated":
            raise StrategyRegistryError(
                "已失效旧结果只能审计，不能克隆为新策略"
            )
        return self.register(
            strategy_id=source.strategy_id,
            name=source.name,
            description=source.description,
            semver=semver,
            parameters=parameters,
            universe_hash=source.universe_hash,
            code_hash=source.code_hash,
            risk_budget_pct=source.risk_budget_pct,
            status="research",
            mode="research",
            gate_passed=False,
            gate_reason="参数变化后必须重新研究验证",
        )

    def transition(
        self,
        version_id: str,
        target_status: str,
        *,
        reason: str,
    ) -> StrategyRecord:
        current = self.get_version(version_id)
        if target_status not in ALLOWED_TRANSITIONS[current.status]:
            raise StrategyRegistryError(
                f"{current.status} cannot transition to {target_status}"
            )
        if target_status == "paper_shadow" and not current.gate_passed:
            raise StrategyRegistryError(
                f"research gate blocked: {current.gate_reason}"
            )
        mode = (
            "paper_shadow"
            if target_status in {"paper_shadow", "paused"}
            else current.mode
        )
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE strategy_deployment
                    SET status = ?, mode = ?, updated_at = ?
                    WHERE version_id = ?
                    """,
                    (target_status, mode, now, version_id),
                )
                self._audit(
                    connection,
                    strategy_id=current.strategy_id,
                    version_id=version_id,
                    event="transition",
                    detail=f"{current.status}->{target_status}: {reason}",
                    occurred_at=now,
                )
        return self.get_version(version_id)

    def list_records(self) -> tuple[StrategyRecord, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(_RECORD_QUERY).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def get_version(self, version_id: str) -> StrategyRecord:
        with closing(self._connect()) as connection:
            row = connection.execute(
                _RECORD_QUERY + " WHERE v.version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _row_to_record(row)

    def seed_defaults(self) -> None:
        self._retire_embedded_symbol_versions()
        existing = {
            (record.strategy_id, record.semver)
            for record in self.list_records()
        }

        def add_default(**values: Any) -> None:
            key = (str(values["strategy_id"]), str(values["semver"]))
            if key not in existing:
                self.register(**values)
                existing.add(key)

        add_default(
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
        )
        add_default(
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
        )
        add_default(
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
        )
        add_default(
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
        )
        add_default(
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
        )
        add_default(
            strategy_id="legacy-sector-momentum",
            name="旧横截面结果 +205.6%",
            description="保留供审计；不得运行或晋级",
            semver="1.0.0-invalid",
            parameters={"legacy_artifact": True},
            universe_hash="legacy-current-listed",
            code_hash="legacy-v1",
            risk_budget_pct=0.10,
            status="legacy_invalidated",
            gate_reason=(
                "替代品风险折算错误、违反10%风控、幸存者偏差"
            ),
        )
        add_default(
            strategy_id="buy-hold",
            name="买入并持有基准",
            description="单标的整股买入持有；用于比较策略增益和成本。",
            semver="1.0.0-research",
            parameters={"whole_shares": True},
            universe_hash="single-symbol-at-run-time",
            code_hash="unverified-local-source-0.7.0",
            risk_budget_pct=0.10,
            gate_reason="基准模型；不得作为自动交易策略晋级",
        )
        add_default(
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
        )
        add_default(
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
        )
        add_default(
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
        )

    def _retire_embedded_symbol_versions(self) -> None:
        """Invalidate legacy versions that hard-code run-time symbols.

        Symbols belong to a research/backtest run, not to immutable strategy
        parameters.  The migration is deliberately schema-based so it does
        not privilege or special-case any ticker or historical strategy id.
        """
        candidates = tuple(
            record
            for record in self.list_records()
            if record.status != "legacy_invalidated"
            and _contains_embedded_symbol(record.parameters)
        )
        if not candidates:
            return
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                for record in candidates:
                    connection.execute(
                        """
                        UPDATE strategy_deployment
                        SET status = 'legacy_invalidated',
                            mode = 'research',
                            updated_at = ?
                        WHERE version_id = ?
                        """,
                        (now, record.version_id),
                    )
                    self._audit(
                        connection,
                        strategy_id=record.strategy_id,
                        version_id=record.version_id,
                        event="legacy_invalidated",
                        detail=(
                            "运行标的曾固化在策略参数中；"
                            "现统一改为每次运行时输入"
                        ),
                        occurred_at=now,
                    )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_definition (
                        strategy_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS strategy_version (
                        version_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        semver TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        parameter_hash TEXT NOT NULL,
                        universe_hash TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        risk_budget_pct TEXT NOT NULL,
                        gate_passed INTEGER NOT NULL,
                        gate_reason TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (strategy_id, semver),
                        FOREIGN KEY (strategy_id)
                            REFERENCES strategy_definition(strategy_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_deployment (
                        deployment_id TEXT PRIMARY KEY,
                        version_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (version_id)
                            REFERENCES strategy_version(version_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_audit (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        version_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        occurred_at TEXT NOT NULL
                    );
                    """
                )

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        strategy_id: str,
        version_id: str,
        event: str,
        detail: str,
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO strategy_audit (
                strategy_id, version_id, event, detail, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                version_id,
                event,
                detail,
                occurred_at,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)


_RECORD_QUERY = """
    SELECT d.strategy_id, d.name, d.description,
           v.version_id, v.semver,
           p.status, p.mode,
           v.parameters_json, v.parameter_hash,
           v.universe_hash, v.code_hash, v.risk_budget_pct,
           v.gate_passed, v.gate_reason,
           v.created_at, p.updated_at
    FROM strategy_definition d
    JOIN strategy_version v ON v.strategy_id = d.strategy_id
    JOIN strategy_deployment p ON p.version_id = v.version_id
"""


def _row_to_record(row: tuple[Any, ...]) -> StrategyRecord:
    return StrategyRecord(
        strategy_id=row[0],
        name=row[1],
        description=row[2],
        version_id=row[3],
        semver=row[4],
        status=row[5],
        mode=row[6],
        parameters=json.loads(row[7]),
        parameter_hash=row[8],
        universe_hash=row[9],
        code_hash=row[10],
        risk_budget_pct=float(row[11]),
        gate_passed=bool(row[12]),
        gate_reason=row[13],
        created_at=row[14],
        updated_at=row[15],
    )


def _canonical_parameters(parameters: dict[str, Any]) -> str:
    return json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _contains_embedded_symbol(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"signal_symbol", "execution_symbol"}:
                return isinstance(nested, str) and bool(nested.strip())
            if _contains_embedded_symbol(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_embedded_symbol(item) for item in value)
    return False
