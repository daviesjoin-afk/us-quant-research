from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from us_quant.ibkr_stream import StreamQuote
from us_quant.minute_data import MinuteDataSummary
from us_quant.portfolio_view import AccountView
from us_quant.strategy_registry import StrategyRecord
from us_quant.universe import UniverseRecord


MAXIMUM_ACCOUNT_AGE_SECONDS = Decimal("300")


@dataclass(frozen=True, slots=True)
class TargetPreflightGate:
    code: str
    name: str
    passed: bool
    observed: str
    required: str
    category: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class TargetPreflightResult:
    generated_at: str
    symbol: str
    company_name: str
    security_type: str
    sector: str
    leader_tier: int | None
    country_status: str
    strategy_version_id: str | None
    strategy_semver: str | None
    strategy_status: str | None
    quote_provider: str | None
    bid: Decimal | None
    ask: Decimal | None
    quote_age_seconds: Decimal | None
    account_net_liquidation: Decimal | None
    account_age_seconds: Decimal | None
    position_budget: Decimal | None
    estimated_whole_shares: int | None
    estimated_entry_notional: Decimal | None
    minute_usable_rows: int
    minute_total_rows: int
    gates: tuple[TargetPreflightGate, ...]
    hard_gates_passed: int
    hard_gate_count: int
    shadow_ready: bool
    broker_orders_available: bool
    decision: str
    orders_submitted: bool = False


def evaluate_target_preflight(
    symbol: str,
    *,
    universe_record: UniverseRecord | None,
    quote: StreamQuote | None,
    account: AccountView | None,
    minute_summary: MinuteDataSummary,
    strategy: StrategyRecord | None,
    exposure_multiplier: Decimal = Decimal("1"),
    broker_orders_available: bool = False,
    now: datetime | None = None,
) -> TargetPreflightResult:
    normalized = symbol.strip().upper()
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=timezone.utc)
    observed_now = observed_now.astimezone(timezone.utc)

    format_ok = (
        bool(normalized)
        and normalized[0].isalpha()
        and len(normalized) <= 10
        and all(
            character.isalnum() or character in ".-"
            for character in normalized
        )
    )
    known = universe_record is not None
    supported_asset = (
        universe_record is not None
        and universe_record.security_type in {"STK", "ETF"}
    )
    non_china_eligible = (
        universe_record is not None
        and universe_record.eligible_for_research
    )

    quote_subscribed = quote is not None
    quote_ready = quote is not None and quote.realtime_ready
    quote_age = (
        Decimal(str(quote.age_seconds))
        if quote is not None and quote.age_seconds is not None
        else None
    )

    account_age = _age_seconds(
        account.observed_at if account is not None else None,
        observed_now,
    )
    account_truth = (
        account is not None
        and account.environment == "paper"
        and account.net_liquidation is not None
        and account.net_liquidation > 0
        and account_age is not None
        and Decimal("0") <= account_age <= MAXIMUM_ACCOUNT_AGE_SECONDS
    )

    strategy_bound = strategy is not None
    strategy_eligible = (
        strategy is not None
        and (
            strategy.status == "research"
            or (
                strategy.status == "paper_shadow"
                and strategy.gate_passed
            )
        )
    )

    position_budget: Decimal | None = None
    quantity: int | None = None
    entry_notional: Decimal | None = None
    if (
        account_truth
        and account is not None
        and account.net_liquidation is not None
        and quote_ready
        and quote is not None
        and quote.ask is not None
        and strategy is not None
        and exposure_multiplier > 0
    ):
        fraction = Decimal(
            str(strategy.parameters.get("max_position_fraction", "0.10"))
        )
        commission = Decimal(
            str(strategy.parameters.get("commission_per_order", "0.35"))
        )
        slippage_bps = Decimal(
            str(strategy.parameters.get("slippage_bps", "2"))
        )
        position_budget = (
            account.net_liquidation * fraction / exposure_multiplier
        )
        estimated_fill = quote.ask * (
            Decimal("1")
            + slippage_bps / Decimal("10000")
        )
        available = max(
            Decimal("0"), position_budget - commission
        )
        quantity = int(
            (available / estimated_fill).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        entry_notional = estimated_fill * quantity
    whole_share_capacity = quantity is not None and quantity >= 1

    warmup = (
        int(strategy.parameters.get("warmup_minutes", 10))
        if strategy is not None
        else 10
    )
    minute_evidence_available = (
        minute_summary.usable_rows >= warmup
    )

    gates = (
        TargetPreflightGate(
            "symbol_format",
            "代码格式",
            format_ok,
            normalized or "空",
            "有效美股/ETF代码",
            "标的",
            True,
        ),
        TargetPreflightGate(
            "universe_identity",
            "官方标的身份",
            known,
            (
                f"{universe_record.exchange} · {universe_record.name}"
                if universe_record is not None
                else "当前快照未找到"
            ),
            "官方目录存在",
            "标的",
            True,
        ),
        TargetPreflightGate(
            "supported_asset",
            "资产类型",
            supported_asset,
            (
                universe_record.security_type
                if universe_record is not None
                else "未知"
            ),
            "STK 或 ETF",
            "标的",
            True,
        ),
        TargetPreflightGate(
            "non_china_eligible",
            "非中概资格",
            non_china_eligible,
            (
                f"{universe_record.country_status} · "
                f"{universe_record.country_evidence_level}"
                if universe_record is not None
                else "无国家证据"
            ),
            "eligible_for_research=true",
            "合规",
            True,
        ),
        TargetPreflightGate(
            "strategy_bound",
            "策略版本绑定",
            strategy_bound,
            (
                f"{strategy.semver} · {strategy.status}"
                if strategy is not None
                else "未选择"
            ),
            "指定标的日内 T 不可变版本",
            "策略",
            True,
        ),
        TargetPreflightGate(
            "strategy_status",
            "策略运行状态",
            strategy_eligible,
            (
                strategy.status if strategy is not None else "未选择"
            ),
            "research 探索或已过门 paper_shadow",
            "策略",
            True,
        ),
        TargetPreflightGate(
            "quote_subscription",
            "目标行情订阅",
            quote_subscribed,
            (
                quote.provider if quote is not None else "未订阅"
            ),
            "当前流包含目标代码",
            "行情",
            True,
        ),
        TargetPreflightGate(
            "realtime_quote",
            "实时行情质量",
            quote_ready,
            (
                f"age={quote_age}s · "
                f"bid={quote.bid} ask={quote.ask}"
                if quote is not None
                else "无报价"
            ),
            "fresh Type 1 + 有效 bid/ask",
            "行情",
            True,
        ),
        TargetPreflightGate(
            "paper_account_truth",
            "Paper 资金真值",
            account_truth,
            (
                f"NLV={account.net_liquidation} · age={account_age}s"
                if account is not None
                else "未读取"
            ),
            "DU Paper NLV > 0 且不超过 300 秒",
            "账户",
            True,
        ),
        TargetPreflightGate(
            "whole_share_capacity",
            "整股容量",
            whole_share_capacity,
            (
                f"{quantity} 股 · 预算 {position_budget}"
                if quantity is not None
                else "不可估算"
            ),
            "成本后至少可买 1 整股",
            "执行",
            True,
        ),
        TargetPreflightGate(
            "minute_evidence",
            "本地分钟证据",
            minute_evidence_available,
            (
                f"{minute_summary.usable_rows}/"
                f"{minute_summary.total_rows} 可用"
            ),
            f"至少 {warmup} 个可用分钟",
            "证据",
            False,
        ),
        TargetPreflightGate(
            "broker_order_route",
            "券商订单通道",
            not broker_orders_available,
            (
                "可用" if broker_orders_available else "不存在"
            ),
            "研究阶段必须关闭",
            "安全",
            True,
        ),
    )
    hard = tuple(gate for gate in gates if gate.blocking)
    passed = sum(gate.passed for gate in hard)
    shadow_ready = passed == len(hard)
    if shadow_ready and strategy is not None:
        decision = (
            "EXPLORATORY_SHADOW_READY"
            if strategy.status == "research"
            else "PAPER_SHADOW_READY"
        )
    else:
        decision = "BLOCKED"

    return TargetPreflightResult(
        generated_at=observed_now.isoformat(),
        symbol=normalized,
        company_name=(
            universe_record.name if universe_record is not None else ""
        ),
        security_type=(
            universe_record.security_type
            if universe_record is not None
            else ""
        ),
        sector=(
            universe_record.sector if universe_record is not None else ""
        ),
        leader_tier=(
            universe_record.leader_tier
            if universe_record is not None
            else None
        ),
        country_status=(
            universe_record.country_status
            if universe_record is not None
            else ""
        ),
        strategy_version_id=(
            strategy.version_id if strategy is not None else None
        ),
        strategy_semver=(
            strategy.semver if strategy is not None else None
        ),
        strategy_status=(
            strategy.status if strategy is not None else None
        ),
        quote_provider=quote.provider if quote is not None else None,
        bid=quote.bid if quote is not None else None,
        ask=quote.ask if quote is not None else None,
        quote_age_seconds=quote_age,
        account_net_liquidation=(
            account.net_liquidation if account is not None else None
        ),
        account_age_seconds=account_age,
        position_budget=position_budget,
        estimated_whole_shares=quantity,
        estimated_entry_notional=entry_notional,
        minute_usable_rows=minute_summary.usable_rows,
        minute_total_rows=minute_summary.total_rows,
        gates=gates,
        hard_gates_passed=passed,
        hard_gate_count=len(hard),
        shadow_ready=shadow_ready,
        broker_orders_available=broker_orders_available,
        decision=decision,
    )


def _age_seconds(
    timestamp: str | None,
    now: datetime,
) -> Decimal | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return Decimal(
        str(
            round(
                (
                    now - parsed.astimezone(timezone.utc)
                ).total_seconds(),
                3,
            )
        )
    )
