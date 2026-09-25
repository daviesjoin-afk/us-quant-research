"""Offscreen screenshots of the Desktop UI v2 routes.

This is tooling, not a second window.  It navigates the real shell, seeds the
synthetic evidence a screenshot needs, emits the same operator intents a button
emits, selects a presentation section by its semantic key, and captures the
frame.  It does not hold business truth, does not write a business widget
directly, and does not re-decide anything the orchestration already decided.

Three boundaries are deliberate:

* the scan is read from ``window.scanner_orchestrator.scan`` -- the capability
  that owns it -- rather than from a window attribute that no longer exists;
* every panel switch goes through the page's own semantic navigation API
  (``set_active_detail``, ``set_active_workspace``, ...), so a page that later
  replaces a ``QTabWidget`` with a sidebar does not change this script;
* waiting for background work reads ``task_controller.active_count``, a public
  query, rather than the mutable worker collection, and fails loudly on timeout
  instead of capturing a screen whose task had not finished.
"""

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
from us_quant.desktop_v2.pages.execution import (  # noqa: E402
    ExecutionDetailWorkspace,
)
from us_quant.desktop_v2.pages.research import ResearchWorkspace  # noqa: E402
from us_quant.desktop_v2.pages.research.targeted import (  # noqa: E402
    TargetedEvidenceWorkspace,
    TargetedReviewDetail,
    TargetedRobustnessDetail,
    TargetedWorkspace,
)
from us_quant.desktop_v2.pages.system import SystemWorkspace  # noqa: E402
from us_quant.trading.domain.market import (  # noqa: E402
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.runtime.models import AutoQuantCandidate  # noqa: E402

ARTIFACTS = ROOT / "research" / "artifacts"

#: Per-step task budgets, in seconds.  They are deliberately generous: the
#: robustness evaluation is a real multi-session research run, and shortening it
#: to make this script quicker would trade review value for iteration speed.
REPLAY_TIMEOUT_SECONDS = 15.0
BACKTEST_TIMEOUT_SECONDS = 15.0
ROBUSTNESS_TIMEOUT_SECONDS = 240.0


def _start_backtest_preview(window) -> None:
    draft = window.backtest_page.current_draft()
    window.backtest_orchestrator.request_selected(draft)


def _process_events() -> None:
    application = QApplication.instance()
    if application is not None:
        application.processEvents()


def _save_preview(window, name: str) -> Path:
    """Capture the current frame to ``research/artifacts/<name>``."""

    output = ARTIFACTS / name
    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output)):
        raise RuntimeError(f"{name} could not be saved")
    return output


def _wait_for_tasks(
    window,
    application,
    *,
    timeout_seconds: float,
    step: str,
) -> None:
    """Pump events until no background task is running.

    The count reaching zero is not on its own enough to return.  A worker
    queues its completion signal to this thread *before* its thread stops, so
    the drain after the count drops is what actually publishes the result onto
    the page; returning on the bare zero would occasionally capture a screen
    whose task had finished but whose result had not been drawn yet.

    A timeout raises rather than falling through: a screenshot of an unfinished
    task looks like evidence while being none.
    """

    deadline = monotonic() + timeout_seconds
    while True:
        application.processEvents()
        if window.task_controller.active_count == 0:
            # One more drain: the worker's ``succeeded``/``finished`` signals
            # are queued, not direct, so the render happens after the thread
            # stops rather than before it.
            sleep(0.05)
            application.processEvents()
            return
        if monotonic() >= deadline:
            raise TimeoutError(
                f"{step} 未在 {timeout_seconds:g} 秒内完成；"
                "预览不会截图未完成的任务画面。"
            )
        sleep(0.05)


def select(window, route: str) -> None:
    """Show one first-level Desktop UI v2 route."""

    window.shell.navigate_to(route)
    _process_events()


def select_research(
    window,
    workspace: ResearchWorkspace,
) -> None:
    """Show one Research workspace by its stable semantic key."""

    window.shell.navigate_to("research")
    window.research_page.set_active_workspace(workspace)
    _process_events()


def select_system(
    window,
    workspace: SystemWorkspace,
) -> None:
    """Show one System workspace by its stable semantic key."""

    window.shell.navigate_to("system")
    window.system_page.set_active_workspace(workspace)
    _process_events()


def select_targeted_workspace(
    window,
    workspace: TargetedWorkspace,
) -> None:
    """Show one targeted workspace by its semantic key."""

    window.targeted_validation_page.set_active_workspace(workspace)
    _process_events()


def select_targeted_evidence(
    window,
    workspace: TargetedEvidenceWorkspace,
    *,
    robustness_detail: TargetedRobustnessDetail | None = None,
    review_detail: TargetedReviewDetail | None = None,
) -> None:
    """Show one targeted evidence section, and its nested detail if named."""

    page = window.targeted_validation_page
    page.set_active_workspace(TargetedWorkspace.EVIDENCE)
    page.set_active_evidence_workspace(workspace)
    if robustness_detail is not None:
        page.set_active_robustness_detail(robustness_detail)
    if review_detail is not None:
        page.set_active_review_detail(review_detail)
    _process_events()


def seed_auto_quant_preview(window) -> None:
    """Publish the preview shortlist from the scanner's canonical scan.

    The scan is read once, from the capability that owns it.  The candidate
    tuple is still written onto the window because AutoQuant's own preparation
    path lives there until v2O-E Paper; this is a deliberate pre-v2O-E bridge,
    not a new surface.
    """

    preview_candidates: list[AutoQuantCandidate] = []
    scan = window.scanner_orchestrator.scan
    if scan is not None:
        for row in sorted(
            (
                item
                for item in scan.results
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
    # The shortlist has one owner now (G2-B): seeding it and repainting are the
    # execution route's own calls, and the script reaches the same retained tuple
    # the route does rather than a window copy that no longer exists.
    orchestrator = window.execution_orchestrator
    orchestrator._candidates = tuple(preview_candidates)
    orchestrator.refresh_all()
    orchestrator.render_launch_context(
        f"已整理 {len(preview_candidates)} 个广域候选；"
        "等待实时订阅与用户逐会话武装 IBKR Paper。"
    )


def seed_targeted_minute_quotes(window) -> None:
    """Record the synthetic minute session the targeted replay consumes.

    346 rows per weekday over 25 weekdays, at the fixed preview start, is the
    fixture this script has always used; it is not re-derived here.
    """

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
            quote = MarketQuote(
                symbol="AAPL",
                bid=price,
                ask=price + Decimal("0.02"),
                last=price,
                close=None,
                bid_size=Decimal("1000"),
                ask_size=Decimal("1000"),
                mode=MarketDataMode.REALTIME,
                updated_at=observed,
                age_seconds=0,
                stale=False,
                stale_reason=None,
                generation=1,
                source_id="preview_feed",
                source_label="PreviewFeed",
                coverage="离屏预览 Level-I",
            )
            window.minute_quote_store.record_snapshot(
                MarketSnapshot(
                    generation=1,
                    connected=True,
                    ready=True,
                    reconnect_attempt=0,
                    quotes=(quote,),
                    error_code=None,
                    message="preview",
                    observed_at=observed,
                    source_id="preview_feed",
                    source_label="PreviewFeed",
                    coverage="离屏预览 Level-I",
                ),
                evidence_origin="synthetic_preview",
            )
    window.targeted_session_orchestrator.refresh_minute_status("AAPL")


def _capture_targeted_suite(window, suffix: str) -> list[Path]:
    """Capture the seven evidence screenshots, dark (``""``) or light.

    The Shadow frame is not here: it is captured on the strategy workspace, and
    at a different point in each theme's flow, so it stays at its own call site.
    """

    saved: list[Path] = []

    select_targeted_evidence(
        window,
        TargetedEvidenceWorkspace.ROBUSTNESS,
        robustness_detail=TargetedRobustnessDetail.SCENARIOS,
    )
    saved.append(
        _save_preview(window, f"desktop_robustness_preview{suffix}.png")
    )

    select_targeted_evidence(window, TargetedEvidenceWorkspace.WALK_FORWARD)
    saved.append(
        _save_preview(window, f"desktop_walk_forward_preview{suffix}.png")
    )

    select_targeted_evidence(window, TargetedEvidenceWorkspace.OVERFIT)
    saved.append(
        _save_preview(window, f"desktop_overfit_preview{suffix}.png")
    )

    select_targeted_evidence(window, TargetedEvidenceWorkspace.DATA_QUALITY)
    saved.append(
        _save_preview(window, f"desktop_data_quality_preview{suffix}.png")
    )

    select_targeted_evidence(
        window, TargetedEvidenceWorkspace.EXECUTION_STRESS
    )
    saved.append(
        _save_preview(window, f"desktop_execution_stress_preview{suffix}.png")
    )

    select_targeted_evidence(
        window,
        TargetedEvidenceWorkspace.REVIEW,
        review_detail=TargetedReviewDetail.GATES,
    )
    saved.append(
        _save_preview(window, f"desktop_review_preview{suffix}.png")
    )

    select_targeted_workspace(window, TargetedWorkspace.PREFLIGHT)
    saved.append(
        _save_preview(window, f"desktop_target_preflight_preview{suffix}.png")
    )
    return saved


def _capture_targeted_console(window, suffix: str) -> Path:
    """Capture the strategy workspace: the target, minute evidence and replay.

    The Shadow frame has always shown the console rather than the evidence
    archive, because the target status and the minute-evidence line live there.
    """

    page = window.targeted_validation_page
    page.set_active_workspace(TargetedWorkspace.STRATEGY)
    page.set_active_evidence_workspace(TargetedEvidenceWorkspace.REPLAY)
    _process_events()
    return _save_preview(window, f"desktop_shadow_preview{suffix}.png")


def main() -> int:
    application = QApplication.instance() or QApplication([])
    configure_chinese_font(application)
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    application.processEvents()

    saved: list[Path] = []
    saved.append(_save_preview(window, "desktop_preview.png"))

    seed_auto_quant_preview(window)
    select(window, "execution")
    saved.append(
        _save_preview(window, "desktop_auto_quant_preview.png")
    )
    window.execution_page.set_active_detail(ExecutionDetailWorkspace.ORDERS)
    application.processEvents()
    saved.append(
        _save_preview(window, "desktop_auto_orders_preview.png")
    )
    window.execution_page.set_active_detail(
        ExecutionDetailWorkspace.PORTFOLIO
    )

    select(window, "account")
    application.processEvents()
    saved.append(_save_preview(window, "desktop_account_preview.png"))
    select(window, "market")
    saved.append(_save_preview(window, "desktop_quotes_preview.png"))

    select_research(window, ResearchWorkspace.TARGETED)
    # Emit the operator intent, not the handler: this is the same path the
    # "应用标的" button takes, through the page's real wiring.
    window.targeted_validation_page.target_apply_requested.emit("AAPL")
    application.processEvents()
    seed_targeted_minute_quotes(window)
    window.targeted_validation_page.replay_requested.emit()
    _wait_for_tasks(
        window,
        application,
        timeout_seconds=REPLAY_TIMEOUT_SECONDS,
        step="分钟回放（shadow preview）",
    )
    application.processEvents()
    saved.append(_capture_targeted_console(window, ""))

    window.targeted_validation_page.robustness_requested.emit()
    _wait_for_tasks(
        window,
        application,
        timeout_seconds=ROBUSTNESS_TIMEOUT_SECONDS,
        step="多日稳健性评估",
    )
    saved.extend(_capture_targeted_suite(window, ""))

    select(window, "strategy")
    saved.append(
        _save_preview(window, "desktop_strategy_manager_preview.png")
    )

    select_research(window, ResearchWorkspace.BACKTEST)
    _start_backtest_preview(window)
    _wait_for_tasks(
        window,
        application,
        timeout_seconds=BACKTEST_TIMEOUT_SECONDS,
        step="回测（backtest preview）",
    )
    application.processEvents()
    saved.append(_save_preview(window, "desktop_backtest_preview.png"))

    select_research(window, ResearchWorkspace.SCANNER)
    saved.append(_save_preview(window, "desktop_scanner_preview.png"))
    select_research(window, ResearchWorkspace.CROSS_SECTION)
    saved.append(_save_preview(window, "desktop_strategy_preview.png"))

    select_system(window, SystemWorkspace.RUNTIME_EVENTS)
    saved.append(_save_preview(window, "desktop_runtime_preview.png"))
    select_system(window, SystemWorkspace.SETTINGS)
    application.processEvents()
    saved.append(_save_preview(window, "desktop_settings_dark.png"))

    window.settings_page.set_theme("light", emit_change=True)
    select(window, "dashboard")
    saved.append(_save_preview(window, "desktop_preview_light.png"))
    select(window, "execution")
    saved.append(
        _save_preview(window, "desktop_auto_quant_preview_light.png")
    )
    window.execution_page.set_active_detail(ExecutionDetailWorkspace.ORDERS)
    application.processEvents()
    saved.append(
        _save_preview(window, "desktop_auto_orders_preview_light.png")
    )
    window.execution_page.set_active_detail(
        ExecutionDetailWorkspace.PORTFOLIO
    )
    select(window, "market")
    saved.append(_save_preview(window, "desktop_quotes_preview_light.png"))

    select_research(window, ResearchWorkspace.TARGETED)
    saved.append(_capture_targeted_console(window, "_light"))
    saved.extend(_capture_targeted_suite(window, "_light"))

    select_system(window, SystemWorkspace.SETTINGS)
    application.processEvents()
    saved.append(_save_preview(window, "desktop_settings_light.png"))

    window.close()
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
