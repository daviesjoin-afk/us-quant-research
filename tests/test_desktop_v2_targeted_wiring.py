"""Wiring and characterization tests for the targeted validation page."""

from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant import desktop
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.research import ResearchWorkspace
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedControlView,
)
from us_quant.paths import STATE_ROOT_ENV
from us_quant.trading.domain.strategy import StrategyStatus


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def dialogs(monkeypatch) -> list[tuple[str, tuple]]:
    seen: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: seen.append(("warning", args)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: seen.append(("information", args)),
    )
    return seen


@pytest.fixture()
def window(monkeypatch, tmp_path, dialogs):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _valid_strategy() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_id="intraday-targeted-t",
        status=StrategyStatus.RESEARCH,
        gate_passed=True,
        parameters={},
        version_id="targeted-v1",
        parameter_hash="hash-v1",
        semver="1.0.0",
    )


def _eligible_universe() -> SimpleNamespace:
    return SimpleNamespace(
        records=(SimpleNamespace(symbol="AAPL", eligible_for_research=True),)
    )


def _ready_quote() -> SimpleNamespace:
    return SimpleNamespace(symbol="AAPL", realtime_ready=True)


def _ready_stream(quote: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        realtime_ready=True,
        quotes=(quote or _ready_quote(),),
    )


class _RunningWorker:
    def isRunning(self) -> bool:
        return True


def _fake_live_market(window: MainWindow, stream: SimpleNamespace) -> None:
    """Make the market orchestrator report a live feed carrying ``stream``.

    The shadow gate reads the market truth through the orchestrator now, so the
    test supplies it there rather than by assigning a worker to the window.
    """

    orchestrator = window.market_orchestrator
    orchestrator._worker = _RunningWorker()
    orchestrator._snapshot = stream


class _Workflow:
    def __init__(self) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False


class _Engine:
    created: list["_Engine"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.active = False
        _Engine.created.append(self)

    def start(self):
        self.active = True
        return SimpleNamespace(active=True)

    def stop(self):
        self.active = False
        return SimpleNamespace(active=False)


# -- route and wiring ----------------------------------------------------


def test_research_route_is_the_native_aggregate(window: MainWindow) -> None:
    assert window.shell.page("research") is window.research_page
    assert (
        window.research_page.active_workspace()
        is ResearchWorkspace.TARGETED
    )
    window.research_page.set_active_workspace(ResearchWorkspace.BACKTEST)
    assert window.research_page._tabs.currentWidget() is window.backtest_page


def test_every_targeted_intent_reaches_the_window_handler(
    window: MainWindow, monkeypatch
) -> None:
    page = window.targeted_validation_page
    seen: list[str] = []
    wiring = (
        ("strategy_selected", "_shadow_strategy_selection_changed"),
        ("target_apply_requested", "_target_symbol_requested"),
        ("target_subscribe_requested", "_target_subscribe_requested"),
        ("shadow_start_requested", "_start_shadow"),
        ("shadow_stop_requested", "_stop_shadow"),
        ("replay_requested", "_run_targeted_replay"),
        ("robustness_requested", "_run_targeted_robustness"),
        ("robustness_run_selected", "_robustness_run_selected"),
        ("review_run_selected", "_review_run_selected"),
    )
    for _signal, handler in wiring:
        monkeypatch.setattr(
            window,
            handler,
            lambda *_args, handler=handler: seen.append(handler),
        )
    for signal, _handler in wiring:
        getattr(page, signal).disconnect()
    window._connect_targeted_validation_page()

    page.set_strategy_options(
        (
            SimpleNamespace(version_id="v1", label="Version 1"),
            SimpleNamespace(version_id="v2", label="Version 2"),
        ),
        "v1",
    )
    page.session_panel.controls.strategy_combo.setCurrentIndex(1)
    page.set_target_symbol("AAPL")
    page.session_panel.controls.target_symbol_apply_button.click()
    page.session_panel.controls.target_symbol_subscribe_button.click()
    page.session_panel.controls.render(
        TargetedControlView(
            strategy_enabled=True,
            target_enabled=True,
            subscribe_enabled=True,
            shadow_start_enabled=True,
            shadow_stop_enabled=True,
            replay_enabled=True,
            robustness_enabled=True,
        )
    )
    page.session_panel.controls.shadow_start_button.click()
    page.session_panel.controls.shadow_stop_button.click()
    page.session_panel.controls.replay_button.click()
    page.session_panel.controls.robustness_button.click()
    page.robustness_run_selected.emit("run-1")
    page.review_run_selected.emit("review-1")

    assert seen == [handler for _signal, handler in wiring]


def test_targeted_theme_switch_notifies_the_page(
    window: MainWindow, monkeypatch
) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "set_palette",
        lambda palette: seen.append(palette),
    )
    window._apply_theme("light")
    assert len(seen) == 1


def test_targeted_controls_preserve_legacy_shadow_availability(
    window: MainWindow,
) -> None:
    window.shadow_snapshot = SimpleNamespace(active=True)
    controls = window._targeted_controls()
    assert controls.strategy_enabled is False
    assert controls.target_enabled is False
    assert controls.subscribe_enabled is False
    assert controls.shadow_start_enabled is False
    assert controls.shadow_stop_enabled is True
    assert controls.replay_enabled is True
    assert controls.robustness_enabled is True


def test_pending_tab_switch_is_one_shot(window: MainWindow, monkeypatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render",
        lambda view: seen.append(view),
    )
    window._targeted_active_workspace = 3
    window._targeted_active_evidence_tab = 6
    window._publish_targeted_view()
    window._publish_targeted_view()
    assert seen[0].active_workspace == 3
    assert seen[0].active_evidence_tab == 6
    assert seen[1].active_workspace is None
    assert seen[1].active_evidence_tab is None


# -- shadow characterization --------------------------------------------


def test_shadow_start_rejects_active_trading_runtime(
    window: MainWindow, dialogs
) -> None:
    window.trading_runtime = SimpleNamespace(
        session=SimpleNamespace(active=True)
    )
    window._start_shadow()
    assert dialogs[0][0] == "warning"
    assert dialogs[0][1][1] == "IBKR Paper 自动量化运行中"


def test_shadow_start_rejects_missing_paper_capital(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: None)
    window._start_shadow()
    assert dialogs[0][1][1] == "缺少 IBKR Paper 资金真值"


def test_shadow_start_rejects_stale_market(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    monkeypatch.setattr(
        type(window.market_orchestrator),
        "is_live",
        property(lambda self: True),
    )
    monkeypatch.setattr(
        type(window.market_orchestrator),
        "snapshot",
        property(lambda self: SimpleNamespace(realtime_ready=False, quotes=())),
    )
    window._start_shadow()
    assert dialogs[0][1][1] == "行情门未通过"


def test_shadow_start_rejects_non_research_eligible_symbol(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    _fake_live_market(window, _ready_stream())
    window.universe = SimpleNamespace(
        records=(SimpleNamespace(symbol="AAPL", eligible_for_research=False),)
    )
    window.targeted_validation_page.set_target_symbol("AAPL")
    window._start_shadow()
    assert dialogs[0][1][1] == "标的门未通过"


def test_shadow_start_rejects_missing_fresh_target_quote(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    _fake_live_market(
        window,
        _ready_stream(SimpleNamespace(symbol="AAPL", realtime_ready=False)),
    )
    window.universe = _eligible_universe()
    window.targeted_validation_page.set_target_symbol("AAPL")
    window._start_shadow()
    assert dialogs[0][1][1] == "目标行情未就绪"


def test_shadow_start_allowed_path_builds_and_starts_engine(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    strategy = _valid_strategy()
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", lambda: strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    monkeypatch.setattr(desktop, "build_targeted_shadow_config", lambda *args, **kwargs: object())
    monkeypatch.setattr(desktop, "ShadowPaperEngine", _Engine)
    monkeypatch.setattr(window, "_publish_targeted_view", lambda: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    _fake_live_market(window, _ready_stream())
    window.universe = _eligible_universe()
    window.broker_account._portfolio = SimpleNamespace(
        account=SimpleNamespace(account_alias="Paper")
    )
    window.shadow_workflow = _Workflow()
    window.targeted_validation_page.set_target_symbol("AAPL")
    _Engine.created.clear()

    window._start_shadow()

    assert len(_Engine.created) == 1
    assert _Engine.created[0].active is True
    assert window.shadow_snapshot.active is True
    assert window.shadow_workflow.active is True


def test_shadow_stop_calls_engine_and_workflow(window: MainWindow, monkeypatch) -> None:
    stopped = SimpleNamespace(active=False)
    engine = SimpleNamespace(active=True, stop=lambda: stopped)
    workflow = _Workflow()
    workflow.active = True
    window.shadow_engine = engine
    window.shadow_workflow = workflow
    monkeypatch.setattr(window, "_publish_targeted_view", lambda: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)

    window._stop_shadow()

    assert window.shadow_snapshot is stopped
    assert workflow.active is False
