from __future__ import annotations

import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic, sleep


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "US_QUANT_STATE_ROOT",
    str(ROOT / "runtime" / "desktop_preview_state"),
)
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.desktop import MainWindow, configure_chinese_font  # noqa: E402
from us_quant.ibkr_stream import StreamQuote, StreamSnapshot  # noqa: E402
from us_quant.auto_quant import AutoQuantCandidate  # noqa: E402


def main() -> int:
    application = QApplication.instance() or QApplication([])
    configure_chinese_font(application)
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    application.processEvents()
    output = ROOT / "research" / "artifacts" / "desktop_preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output)):
        raise RuntimeError("desktop preview could not be saved")

    def select(top_index: int, child, child_index: int) -> None:
        window.tabs.setCurrentIndex(top_index)
        child.setCurrentIndex(child_index)
        application.processEvents()

    preview_candidates = []
    if window.scan is not None:
        for row in sorted(
            (
                item
                for item in window.scan.results
                if item.trade_eligible
            ),
            key=lambda item: -item.score,
        ):
            if any(
                existing.symbol == row.execution_symbol
                for existing in preview_candidates
            ):
                continue
            preview_candidates.append(
                AutoQuantCandidate(
                    symbol=row.execution_symbol,
                    name=row.name,
                    sector=row.sector,
                    leader_tier=row.leader_tier,
                    scan_score=Decimal(str(row.score)),
                    signal=row.signal,
                )
            )
            if len(preview_candidates) == 8:
                break
    window.auto_quant_candidates = tuple(preview_candidates)
    window._populate_auto_quant_candidates()
    window.auto_summary_label.setText(
        f"已整理 {len(preview_candidates)} 个广域候选；"
        "等待实时订阅与用户逐会话武装 IBKR Paper。"
    )
    select(0, window.monitor_tabs, 1)
    auto_output = (
        ROOT / "research" / "artifacts" / "desktop_auto_quant_preview.png"
    )
    if not window.grab().save(str(auto_output)):
        raise RuntimeError("auto quant preview could not be saved")
    window.auto_detail_tabs.setCurrentIndex(2)
    application.processEvents()
    auto_orders_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_auto_orders_preview.png"
    )
    if not window.grab().save(str(auto_orders_output)):
        raise RuntimeError("auto orders preview could not be saved")
    window.auto_detail_tabs.setCurrentIndex(0)

    select(0, window.monitor_tabs, 2)
    application.processEvents()
    account_output = (
        ROOT / "research" / "artifacts" / "desktop_account_preview.png"
    )
    if not window.grab().save(str(account_output)):
        raise RuntimeError("account preview could not be saved")
    select(0, window.monitor_tabs, 3)
    quotes_output = (
        ROOT / "research" / "artifacts" / "desktop_quotes_preview.png"
    )
    if not window.grab().save(str(quotes_output)):
        raise RuntimeError("quotes preview could not be saved")
    select(0, window.monitor_tabs, 4)
    window.target_symbol_input.setText("AAPL")
    window._apply_target_symbol()
    replay_start = datetime(
        2026, 7, 20, 14, 0, tzinfo=timezone.utc
    )
    replay_prices = [
        Decimal("50")
        + Decimal(min(index, 12)) * Decimal("0.08")
        for index in range(346)
    ]
    day_offsets = []
    candidate_day = 0
    while len(day_offsets) < 25:
        observed_day = replay_start + timedelta(days=candidate_day)
        if observed_day.weekday() < 5:
            day_offsets.append(candidate_day)
        candidate_day += 1
    for day in day_offsets:
        for index, price in enumerate(replay_prices):
            observed = (
                replay_start
                + timedelta(days=day, minutes=index)
            )
            quote = StreamQuote(
                symbol="AAPL",
                request_id=1,
                generation=1,
                requested_market_data_type=1,
                effective_market_data_type=1,
                bid=price,
                ask=price + Decimal("0.02"),
                last=price,
                close=None,
                updated_at=observed.isoformat(),
                age_seconds=0,
                stale=False,
                stale_reason=None,
                provider="PreviewFeed",
                coverage="离屏预览 Level-I",
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
            )
            window.minute_quote_store.record_snapshot(
                StreamSnapshot(
                    generation=1,
                    socket_connected=True,
                    handshake_complete=True,
                    reconnect_attempt=0,
                    quotes=(quote,),
                    last_error_code=None,
                    last_message="preview",
                    observed_at=observed.isoformat(),
                    provider="PreviewFeed",
                    coverage="离屏预览 Level-I",
                ),
                evidence_origin="synthetic_preview",
            )
    window._refresh_minute_data_status("AAPL")
    window._run_targeted_replay()
    replay_deadline = monotonic() + 10
    while window.workers and monotonic() < replay_deadline:
        application.processEvents()
        sleep(0.05)
    application.processEvents()
    shadow_output = (
        ROOT / "research" / "artifacts" / "desktop_shadow_preview.png"
    )
    if not window.grab().save(str(shadow_output)):
        raise RuntimeError("shadow preview could not be saved")
    window._run_targeted_robustness()
    robustness_deadline = monotonic() + 240
    while window.workers and monotonic() < robustness_deadline:
        application.processEvents()
        sleep(0.05)
    window.targeted_research_tabs.setCurrentIndex(1)
    window.targeted_robustness_detail_tabs.setCurrentIndex(1)
    application.processEvents()
    robustness_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_robustness_preview.png"
    )
    if not window.grab().save(str(robustness_output)):
        raise RuntimeError("robustness preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(2)
    application.processEvents()
    walk_forward_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_walk_forward_preview.png"
    )
    if not window.grab().save(str(walk_forward_output)):
        raise RuntimeError("walk-forward preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(3)
    application.processEvents()
    overfit_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_overfit_preview.png"
    )
    if not window.grab().save(str(overfit_output)):
        raise RuntimeError("overfit preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(4)
    application.processEvents()
    quality_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_data_quality_preview.png"
    )
    if not window.grab().save(str(quality_output)):
        raise RuntimeError("data-quality preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(5)
    application.processEvents()
    stress_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_execution_stress_preview.png"
    )
    if not window.grab().save(str(stress_output)):
        raise RuntimeError("execution-stress preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(6)
    window.targeted_review_detail_tabs.setCurrentIndex(1)
    application.processEvents()
    review_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_review_preview.png"
    )
    if not window.grab().save(str(review_output)):
        raise RuntimeError("review preview could not be saved")
    window.targeted_workspace_tabs.setCurrentIndex(2)
    application.processEvents()
    preflight_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_target_preflight_preview.png"
    )
    if not window.grab().save(str(preflight_output)):
        raise RuntimeError("target preflight preview could not be saved")
    select(1, window.strategy_tabs, 0)
    manager_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_strategy_manager_preview.png"
    )
    if not window.grab().save(str(manager_output)):
        raise RuntimeError("strategy manager preview could not be saved")
    select(1, window.strategy_tabs, 1)
    window._run_backtest_workspace(False)
    deadline = monotonic() + 15
    while window.workers and monotonic() < deadline:
        application.processEvents()
        sleep(0.05)
    application.processEvents()
    backtest_output = (
        ROOT / "research" / "artifacts" / "desktop_backtest_preview.png"
    )
    if not window.grab().save(str(backtest_output)):
        raise RuntimeError("backtest preview could not be saved")
    select(2, window.research_tabs, 1)
    scanner_output = (
        ROOT / "research" / "artifacts" / "desktop_scanner_preview.png"
    )
    if not window.grab().save(str(scanner_output)):
        raise RuntimeError("scanner preview could not be saved")
    select(1, window.strategy_tabs, 2)
    strategy_output = (
        ROOT / "research" / "artifacts" / "desktop_strategy_preview.png"
    )
    if not window.grab().save(str(strategy_output)):
        raise RuntimeError("strategy preview could not be saved")
    select(3, window.operations_tabs, 0)
    runtime_output = (
        ROOT / "research" / "artifacts" / "desktop_runtime_preview.png"
    )
    if not window.grab().save(str(runtime_output)):
        raise RuntimeError("runtime preview could not be saved")
    window.tabs.setCurrentIndex(4)
    application.processEvents()
    settings_output = (
        ROOT / "research" / "artifacts" / "desktop_settings_dark.png"
    )
    if not window.grab().save(str(settings_output)):
        raise RuntimeError("dark settings preview could not be saved")

    window.settings_theme_combo.setCurrentIndex(
        window.settings_theme_combo.findData("light")
    )
    select(0, window.monitor_tabs, 0)
    light_output = (
        ROOT / "research" / "artifacts" / "desktop_preview_light.png"
    )
    if not window.grab().save(str(light_output)):
        raise RuntimeError("light preview could not be saved")
    select(0, window.monitor_tabs, 1)
    light_auto_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_auto_quant_preview_light.png"
    )
    if not window.grab().save(str(light_auto_output)):
        raise RuntimeError("light auto quant preview could not be saved")
    window.auto_detail_tabs.setCurrentIndex(2)
    application.processEvents()
    light_auto_orders_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_auto_orders_preview_light.png"
    )
    if not window.grab().save(str(light_auto_orders_output)):
        raise RuntimeError(
            "light auto orders preview could not be saved"
        )
    window.auto_detail_tabs.setCurrentIndex(0)
    select(0, window.monitor_tabs, 3)
    light_quotes_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_quotes_preview_light.png"
    )
    if not window.grab().save(str(light_quotes_output)):
        raise RuntimeError("light quotes preview could not be saved")
    select(0, window.monitor_tabs, 4)
    window.targeted_workspace_tabs.setCurrentIndex(0)
    window.targeted_research_tabs.setCurrentIndex(0)
    light_shadow_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_shadow_preview_light.png"
    )
    if not window.grab().save(str(light_shadow_output)):
        raise RuntimeError("light shadow preview could not be saved")
    window.targeted_workspace_tabs.setCurrentIndex(1)
    window.targeted_research_tabs.setCurrentIndex(1)
    window.targeted_robustness_detail_tabs.setCurrentIndex(1)
    application.processEvents()
    light_robustness_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_robustness_preview_light.png"
    )
    if not window.grab().save(str(light_robustness_output)):
        raise RuntimeError("light robustness preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(2)
    application.processEvents()
    light_walk_forward_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_walk_forward_preview_light.png"
    )
    if not window.grab().save(str(light_walk_forward_output)):
        raise RuntimeError("light walk-forward preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(3)
    application.processEvents()
    light_overfit_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_overfit_preview_light.png"
    )
    if not window.grab().save(str(light_overfit_output)):
        raise RuntimeError("light overfit preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(4)
    application.processEvents()
    light_quality_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_data_quality_preview_light.png"
    )
    if not window.grab().save(str(light_quality_output)):
        raise RuntimeError("light data-quality preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(5)
    application.processEvents()
    light_stress_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_execution_stress_preview_light.png"
    )
    if not window.grab().save(str(light_stress_output)):
        raise RuntimeError("light execution-stress preview could not be saved")
    window.targeted_research_tabs.setCurrentIndex(6)
    window.targeted_review_detail_tabs.setCurrentIndex(1)
    application.processEvents()
    light_review_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_review_preview_light.png"
    )
    if not window.grab().save(str(light_review_output)):
        raise RuntimeError("light review preview could not be saved")
    window.targeted_workspace_tabs.setCurrentIndex(2)
    application.processEvents()
    light_preflight_output = (
        ROOT
        / "research"
        / "artifacts"
        / "desktop_target_preflight_preview_light.png"
    )
    if not window.grab().save(str(light_preflight_output)):
        raise RuntimeError(
            "light target preflight preview could not be saved"
        )
    window.tabs.setCurrentIndex(4)
    application.processEvents()
    light_settings_output = (
        ROOT / "research" / "artifacts" / "desktop_settings_light.png"
    )
    if not window.grab().save(str(light_settings_output)):
        raise RuntimeError("light settings preview could not be saved")
    window.close()
    print(output)
    print(auto_output)
    print(auto_orders_output)
    print(account_output)
    print(quotes_output)
    print(shadow_output)
    print(robustness_output)
    print(walk_forward_output)
    print(overfit_output)
    print(quality_output)
    print(stress_output)
    print(review_output)
    print(preflight_output)
    print(manager_output)
    print(backtest_output)
    print(scanner_output)
    print(strategy_output)
    print(runtime_output)
    print(settings_output)
    print(light_output)
    print(light_auto_output)
    print(light_auto_orders_output)
    print(light_quotes_output)
    print(light_shadow_output)
    print(light_robustness_output)
    print(light_walk_forward_output)
    print(light_overfit_output)
    print(light_quality_output)
    print(light_stress_output)
    print(light_review_output)
    print(light_preflight_output)
    print(light_settings_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
