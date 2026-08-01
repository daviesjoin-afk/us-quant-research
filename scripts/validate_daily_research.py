from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "research" / "results" / "daily_k_research.json"
TOLERANCE = 1e-9


def _curve_metrics(rows: list[dict], equity_field: str) -> dict:
    initial = 1500.0
    equities = [initial] + [row[equity_field] for row in rows]
    returns = [
        current / previous - 1.0
        for previous, current in zip(equities, equities[1:])
    ]
    peak = initial
    max_drawdown = 0.0
    for equity in equities[1:]:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return {
        "total_return": equities[-1] / initial - 1.0,
        "max_drawdown": max_drawdown,
        "worst_day": min(returns),
    }


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(actual - expected) > TOLERANCE:
        raise AssertionError(
            f"{label}: expected {expected}, recomputed {actual}"
        )


def validate() -> dict:
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    rows = result["chart_data"]["out_of_sample_equity"]
    primary = result["out_of_sample"]["strategy"]
    buy_hold = result["out_of_sample"]["buy_and_hold_benchmark"]
    constant = result["out_of_sample"][
        "weekly_constant_risk_benchmark"
    ]

    for label, metrics, field in (
        ("strategy", primary, "strategy_equity"),
        ("buy_hold", buy_hold, "buy_hold_equity"),
    ):
        recomputed = _curve_metrics(rows, field)
        for metric_name, value in recomputed.items():
            _assert_close(
                value,
                metrics[metric_name],
                f"{label}.{metric_name}",
            )

    for metric_name in (
        "total_return",
        "max_drawdown",
        "worst_day",
        "commission",
        "trade_count",
    ):
        _assert_close(
            float(primary[metric_name]),
            float(constant[metric_name]),
            f"constant_baseline.{metric_name}",
        )

    folds = result["out_of_sample"]["folds"]
    if len(folds) != 3 or primary["days"] != 189:
        raise AssertionError("expected three complete 63-day folds")
    _assert_close(
        sum(fold["oos_commission"] for fold in folds),
        primary["commission"],
        "fold_commission_sum",
    )
    if sum(fold["oos_trade_count"] for fold in folds) != primary[
        "trade_count"
    ]:
        raise AssertionError("fold trade counts do not reconcile")

    no_cost = next(
        row
        for row in result["cost_stress"]
        if row["scenario"] == "no_cost"
    )
    configured = next(
        row
        for row in result["cost_stress"]
        if row["scenario"] == "configured"
    )
    if no_cost["oos_return"] < configured["oos_return"]:
        raise AssertionError("configured costs improved the backtest")
    for row in result["frequency_sensitivity"]:
        _assert_close(
            row["no_cost_oos_return"] - row["oos_return"],
            row["cost_drag"],
            f"frequency.{row['frequency']}.cost_drag",
        )

    return {
        "validated": True,
        "checked_equity_rows": len(rows),
        "checked_folds": len(folds),
        "strategy_return": primary["total_return"],
        "strategy_max_drawdown": primary["max_drawdown"],
        "buy_hold_max_drawdown": buy_hold["max_drawdown"],
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
