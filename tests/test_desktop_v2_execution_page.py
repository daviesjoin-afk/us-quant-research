"""Desktop UI v2 execution page tests.

The page replaced ``MainWindow._auto_quant_tab``.  Two claims are pinned here,
and they are the whole reason the page exists:

* it *renders* a view model it is handed -- the candidate table, the five detail
  sections, the cards -- so what an operator sees comes from one projection
  rather than from a dozen handlers writing widgets;
* it *reports intent and nothing else*.  Every button emits a signal, the page
  holds no service, and it cannot arm, connect or change a lifecycle phase.  The
  absence of those capabilities is asserted rather than assumed, because a page
  that could arm itself would make the launch confirmation advisory.

Nothing here constructs a broker, a store, a runtime or an application service:
the page is given view models and its own signals are observed.
"""

from __future__ import annotations

import ast
import os
import pathlib
from enum import IntEnum

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from us_quant.desktop_v2.pages.execution import ExecutionPage
from us_quant.desktop_v2.pages.execution.models import (
    CandidateRealtime,
    CandidateRow,
    ExecutionCandidatesView,
    ExecutionControlState,
    ExecutionDetailWorkspace,
    ExecutionRuntimeView,
    FillRow,
    LatencyRow,
    MetricView,
    OrderRow,
    PositionRow,
    ShadowRow,
    Tone,
)

_PAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "pages"
    / "execution"
)

_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def page():
    _qapp()
    widget = ExecutionPage()
    yield widget
    widget.deleteLater()


def _candidates_view(**overrides: object) -> ExecutionCandidatesView:
    values: dict[str, object] = {
        "candidates": (
            CandidateRow("AAA", "Alpha", "Tech", "龙头", "88.5", "buy"),
        ),
        "realtime": (CandidateRealtime("等待", Tone.WARNING),),
        "static_key": (("AAA", "Alpha", "Tech", "1", "88.5", "buy"),),
    }
    values.update(overrides)
    return ExecutionCandidatesView(**values)  # type: ignore[arg-type]


def _view(**overrides: object) -> ExecutionRuntimeView:
    values: dict[str, object] = {
        "status": MetricView("运行中", "armed"),
        "equity": MetricView("$1,500.00", "IBKR Paper 只读快照"),
        "realized": MetricView("+$5.00", "本地估算"),
        "unrealized": MetricView("+$7.00", "IBKR Paper 优先"),
        "position_count": MetricView("1", "在途 0 · 完成交易 2"),
        "summary": "运行中 · 候选 3",
        "positions": (
            PositionRow("AAA", "10", "100", "101", "+$10.00", "13:00", "session"),
        ),
        "fills": (
            FillRow("13:05", "AAA", "BUY", "10", "100", "$1.00", "不可用"),
        ),
        "shadow": (
            ShadowRow("AAA", "99.9", "100.1", "100.15", "99.85", "—", "等待行情", Tone.WARNING),
        ),
        "latency": (
            LatencyRow("intent-1", "AAA", "BUY", "42 ms", "13:05", Tone.SUCCESS),
        ),
        "candidates": _candidates_view(),
        "orders": (
            OrderRow("已核对", "AAA", "BUY", "10/10", "100.5", "filled", "42", Tone.SUCCESS),
        ),
    }
    values.update(overrides)
    return ExecutionRuntimeView(**values)  # type: ignore[arg-type]


def _state(**overrides: bool) -> ExecutionControlState:
    values = {
        "prepare_enabled": True,
        "start_enabled": True,
        "channel_check_enabled": True,
        "strategy_combo_enabled": True,
        "candidate_limit_enabled": True,
        "capital_limit_enabled": True,
        "arm_confirm_enabled": True,
        "pause_enabled": False,
        "resume_enabled": False,
        "stop_enabled": False,
        "stop_stream_enabled": False,
        "reconcile_enabled": False,
        "resume_reconciliation_enabled": False,
    }
    values.update(overrides)
    return ExecutionControlState(**values)  # type: ignore[arg-type]


def _imported(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# -- rendering ------------------------------------------------------------


def test_the_page_starts_with_the_documented_control_state(page) -> None:
    controls = page.controls
    assert controls.prepare_button.isEnabled()
    assert controls.start_button.isEnabled()
    assert not controls.pause_button.isEnabled()
    assert not controls.resume_button.isEnabled()
    assert not controls.stop_button.isEnabled()
    assert not controls.stop_stream_button.isEnabled()
    assert not page.details.reconcile_button.isEnabled()
    assert not controls.resume_reconciliation_button.isEnabled()


def test_the_two_inputs_keep_their_documented_defaults_and_reads(page) -> None:
    assert page.controls.candidate_limit_input.value() == 30
    assert page.controls.candidate_limit_input.minimum() == 3
    assert page.controls.candidate_limit_input.maximum() == 30
    assert page.controls.capital_limit_input.value() == 0
    assert page.candidate_limit() == 30
    assert page.capital_limit() == Decimal("0")


def test_the_launch_confirmation_control_is_written_only_by_the_window(page) -> None:
    assert not page.controls.arm_confirm.isVisible()
    assert page.arm_confirmed() is False

    page.set_arm_confirmed(True)

    assert page.controls.arm_confirm.isChecked()
    assert page.arm_confirmed() is True


def test_the_page_draws_the_cards_from_the_view(page) -> None:
    page.render(_view())
    assert page.status_card.value_label.text() == "运行中"
    assert page.equity_card.value_label.text() == "$1,500.00"
    assert page.realized_card.value_label.text() == "+$5.00"
    assert page.unrealized_card.value_label.text() == "+$7.00"
    assert page.position_card.value_label.text() == "1"
    assert page.position_card.note_label.text() == "在途 0 · 完成交易 2"


def test_the_page_draws_every_detail_table(page) -> None:
    page.render(_view())

    assert page.details.position_model.rowCount() == 1
    assert page.details.fill_model.rowCount() == 1
    assert page.details.shadow_table.rowCount() == 1
    assert page.details.latency_table.rowCount() == 1
    assert page.details.candidate_table.rowCount() == 1
    assert page.details.order_table.rowCount() == 1


def test_the_tables_show_the_projected_values(page) -> None:
    page.render(_view())

    assert page.details.position_table.model().index(0, 0).data() == "AAA"
    assert page.details.shadow_table.item(0, 6).text() == "等待行情"
    assert page.details.latency_table.item(0, 3).text() == "42 ms"
    assert page.details.candidate_table.item(0, 0).text() == "AAA"
    assert page.details.candidate_table.item(0, 6).text() == "等待"
    assert page.details.order_table.item(0, 0).text() == "已核对"


def test_the_candidate_table_is_renderable_without_a_session(page) -> None:
    """A shortlist exists before a session does, and must be inspectable.

    The operator approves candidates and only then arms a session, so the table
    cannot be gated on a runtime snapshot: at the moment the shortlist is the
    thing being approved, no snapshot exists.
    """

    page.render_candidates(_candidates_view())

    assert page.details.candidate_table.rowCount() == 1
    assert page.details.candidate_table.item(0, 0).text() == "AAA"
    assert page.details.candidate_table.item(0, 6).text() == "等待"


def test_rendering_candidates_alone_leaves_the_session_tables_untouched(page) -> None:
    """The session tables must not be cleared by a pre-launch repaint."""

    page.render(_view())
    assert page.details.position_model.rowCount() == 1
    assert page.details.order_table.rowCount() == 1

    page.render_candidates(
        _candidates_view(
            candidates=(CandidateRow("BBB", "Beta", "Energy", "层级3", "70.0", "hold"),),
            realtime=(CandidateRealtime("当前 fresh"),),
            static_key=(("BBB", "Beta", "Energy", "3", "70.0", "hold"),),
        )
    )

    assert page.details.candidate_table.item(0, 0).text() == "BBB"
    assert page.details.position_model.rowCount() == 1
    assert page.details.order_table.rowCount() == 1


def test_the_health_cards_are_migrated_as_placeholders(page) -> None:
    """They were static in the legacy page; this round does not re-derive them."""

    titles = [
        card.findChild(type(card.value_label)).text()
        for card in page.health_cards
    ]
    assert len(page.health_cards) == 5
    assert page.health_cards[0].value_label.text() == "未评估"
    assert len(titles) == 5


def test_the_reconcile_button_reports_intent_and_does_not_act(page) -> None:
    seen: list[str] = []
    page.reconcile_requested.connect(lambda: seen.append("reconcile"))
    page.set_control_state(_state(reconcile_enabled=True))

    page.details.reconcile_button.click()

    assert seen == ["reconcile"]


def test_the_session_buttons_report_intent(page) -> None:
    seen: list[str] = []
    for signal, name in (
        (page.prepare_requested, "prepare"),
        (page.channel_check_requested, "channel"),
        (page.start_requested, "start"),
        (page.stop_stream_requested, "stop_stream"),
        (page.pause_requested, "pause"),
        (page.resume_requested, "resume"),
        (page.stop_requested, "stop"),
        (page.resume_reconciliation_requested, "resume_reconciliation"),
    ):
        signal.connect(lambda name=name: seen.append(name))

    for button in (
        page.controls.prepare_button,
        page.controls.channel_check_button,
        page.controls.start_button,
        page.controls.stop_stream_button,
        page.controls.pause_button,
        page.controls.resume_button,
        page.controls.stop_button,
        page.controls.resume_reconciliation_button,
    ):
        button.setEnabled(True)
        button.click()

    assert seen == [
        "prepare",
        "channel",
        "start",
        "stop_stream",
        "pause",
        "resume",
        "stop",
        "resume_reconciliation",
    ]


def test_a_click_that_is_disabled_emits_nothing(page) -> None:
    seen: list[str] = []
    page.pause_requested.connect(lambda: seen.append("pause"))

    page.controls.pause_button.click()

    assert seen == []


# -- the control state ----------------------------------------------------


def test_the_control_state_is_applied_exactly(page) -> None:
    page.set_control_state(
        _state(
            prepare_enabled=False,
            start_enabled=False,
            pause_enabled=True,
            stop_enabled=True,
            reconcile_enabled=True,
        )
    )

    controls = page.controls
    assert not controls.prepare_button.isEnabled()
    assert not controls.start_button.isEnabled()
    assert controls.pause_button.isEnabled()
    assert controls.stop_button.isEnabled()
    assert page.details.reconcile_button.isEnabled()
    assert not controls.resume_reconciliation_button.isEnabled()


def test_the_manual_resume_control_follows_its_own_flag(page) -> None:
    page.set_control_state(
        _state(reconcile_enabled=True, resume_reconciliation_enabled=True)
    )

    assert page.details.reconcile_button.isEnabled()
    assert page.controls.resume_reconciliation_button.isEnabled()


# -- the strategy combo is a view -----------------------------------------


def test_the_combo_displays_the_options_it_is_given(page) -> None:
    page.set_strategy_options(
        [("intraday-auto-rotation 1.0.0", "v1"), ("intraday-auto-rotation 1.1.0", "v2")],
        "v2",
    )

    combo = page.controls.strategy_combo
    assert combo.count() == 2
    assert page.selected_strategy_version_id() == "v2"


def test_refilling_the_combo_is_not_an_operator_choice(page) -> None:
    """A repaint must not be reported as a selection."""

    seen: list[object] = []
    page.strategy_selected.connect(seen.append)

    page.set_strategy_options([("a", "v1"), ("b", "v2")], "v1")

    assert seen == []


def test_choosing_an_entry_is_reported_as_an_intent(page) -> None:
    page.set_strategy_options([("a", "v1"), ("b", "v2")], "v1")
    seen: list[object] = []
    page.strategy_selected.connect(seen.append)

    page.controls.strategy_combo.setCurrentIndex(1)

    assert seen == ["v2"]


def test_the_combo_reports_when_the_preflight_inputs_moved(page) -> None:
    page.set_strategy_options([("a", "v1"), ("b", "v2")], "v1")
    seen: list[str] = []
    page.preflight_inputs_changed.connect(lambda: seen.append("changed"))

    page.controls.strategy_combo.setCurrentIndex(1)
    page.controls.candidate_limit_input.setValue(10)
    page.controls.capital_limit_input.setValue(500)

    assert seen == ["changed", "changed", "changed"]


# -- rendering the context lines ------------------------------------------


def test_the_context_lines_are_written_individually(page) -> None:
    page.render_context(summary="扫描中", scope="候选 0")

    assert page.controls.summary_label.text() == "扫描中"
    assert page.controls.scope_label.text() == "候选 0"
    # An omitted line is left alone rather than blanked.
    before = page.controls.session_label.text()
    page.render_context(summary="再次扫描")
    assert page.controls.session_label.text() == before


def test_the_preflight_line_carries_the_tally_and_the_detail(page) -> None:
    page.render_preflight(3, 5, "✓ 账户：已读取")

    text = page.controls.preflight_label.text()
    assert "准备检查 3/5" in text
    assert "✓ 账户：已读取" in text


def test_the_execution_health_line_is_rendered(page) -> None:
    page.render_execution_health("执行对账：未连接。")

    assert page.details.execution_health_text == "执行对账：未连接。"


# -- semantic detail navigation -------------------------------------------
#
# ``set_active_detail`` is the page's own vocabulary for "show me this
# section".  It exists so a caller -- tooling, a test, a future shortcut -- does
# not have to know which ``QTabWidget`` holds the section or at what index.
# These tests check the two halves of that contract: the key selects the right
# section, and the key is not a lifecycle verb.


def test_the_detail_workspace_keys_are_the_documented_sections(page) -> None:
    assert [workspace.name for workspace in ExecutionDetailWorkspace] == [
        "PORTFOLIO",
        "SHADOW",
        "LATENCY",
        "CANDIDATES",
        "ORDERS",
    ]
    # And the page really has that many sections, so a key cannot silently
    # address a tab that is not there.
    assert page.details.tabs.count() == len(ExecutionDetailWorkspace)


def _shows(panel_widget, table) -> bool:
    """Does the visible section carry ``table``, directly or as its child?

    Sections differ in whether they add the table straight as the tab or wrap
    it in a page, so the test asks what the operator is looking at rather than
    pinning the wrapper.
    """

    return panel_widget is table or panel_widget.isAncestorOf(table)


def test_selecting_the_orders_detail_shows_the_order_table(page) -> None:
    page.set_active_detail(ExecutionDetailWorkspace.ORDERS)

    assert page.details.tabs.currentIndex() == int(
        ExecutionDetailWorkspace.ORDERS
    )
    assert _shows(page.details.tabs.currentWidget(), page.details.order_table)


def test_selecting_the_portfolio_detail_shows_the_position_table(page) -> None:
    page.set_active_detail(ExecutionDetailWorkspace.ORDERS)
    page.set_active_detail(ExecutionDetailWorkspace.PORTFOLIO)

    assert page.details.tabs.currentIndex() == int(
        ExecutionDetailWorkspace.PORTFOLIO
    )
    assert _shows(
        page.details.tabs.currentWidget(), page.details.position_table
    )


@pytest.mark.parametrize("workspace", list(ExecutionDetailWorkspace))
def test_every_detail_key_selects_its_own_section(page, workspace) -> None:
    """Each key addresses exactly one distinct section, and no two collide."""

    page.set_active_detail(workspace)
    assert page.details.tabs.currentIndex() == int(workspace)
    assert page.details.tabs.currentIndex() == workspace


def test_navigation_does_not_run_anything(page) -> None:
    """Selecting a section is presentation; it cannot arm or start a session."""

    seen: list[str] = []
    for name in (
        "prepare_requested",
        "start_requested",
        "channel_check_requested",
        "pause_requested",
        "resume_requested",
        "stop_requested",
        "reconcile_requested",
        "resume_reconciliation_requested",
    ):
        getattr(page, name).connect(lambda _name=name: seen.append(_name))

    for workspace in ExecutionDetailWorkspace:
        page.set_active_detail(workspace)

    assert seen == []


def test_the_detail_keys_are_pinned_to_their_sections(page) -> None:
    """The mapping from key to section, not just "index equals index".

    Without this, a key pointed at the wrong tab would still satisfy every
    "``currentIndex() == int(workspace)``" assertion above.
    """

    expected = {
        ExecutionDetailWorkspace.PORTFOLIO: "组合与盈亏",
        ExecutionDetailWorkspace.SHADOW: "影子执行带",
        ExecutionDetailWorkspace.LATENCY: "提交延迟",
        ExecutionDetailWorkspace.CANDIDATES: "候选与信号",
        ExecutionDetailWorkspace.ORDERS: "Paper订单",
    }
    for workspace, label in expected.items():
        page.set_active_detail(workspace)
        assert page.details.tabs.tabText(page.details.tabs.currentIndex()) == label


def test_the_detail_keys_carry_no_behaviour() -> None:
    """An ``IntEnum`` of positions, not a state machine with methods."""

    assert issubclass(ExecutionDetailWorkspace, IntEnum)
    assert set(ExecutionDetailWorkspace.__members__) == {
        "PORTFOLIO",
        "SHADOW",
        "LATENCY",
        "CANDIDATES",
        "ORDERS",
    }
    for member in ExecutionDetailWorkspace:
        own = {
            name
            for name in vars(member)
            if not name.startswith("_") and name not in vars(IntEnum)
        }
        assert not own, (member.name, own)


# -- the page is a renderer ------------------------------------------------


def test_the_page_has_no_control_that_could_start_a_session_by_itself(page) -> None:
    """Every button emits; none of them is wired to anything but a signal."""

    buttons = page.findChildren(QPushButton)
    assert buttons, "the page must offer the session controls"
    # And the page cannot reach a service: no such attribute exists.
    for forbidden in (
        "paper_trading",
        "paper_workflow",
        "trading_runtime",
        "order_repository",
        "risk",
        "execution",
    ):
        assert not hasattr(page, forbidden), forbidden


def test_the_page_package_imports_no_business_service() -> None:
    for path in sorted(_PAGE_DIR.glob("*.py")):
        imported = _imported(path)
        for forbidden in (
            "us_quant.trading.application",
            "us_quant.trading.composition",
            "us_quant.trading.adapters",
            "us_quant.trading.runtime",
            "us_quant.paper_trading_service",
            "us_quant.workflow_controller",
            "us_quant.ibkr",
            "us_quant.desktop",
            "ibapi",
            "sqlite3",
        ):
            assert forbidden not in imported, (path.name, forbidden)
            assert not any(
                name.startswith(f"{forbidden}.") for name in imported
            ), (path.name, forbidden)


def test_the_presenter_is_qt_free() -> None:
    """The projection must be testable without a widget toolkit."""

    for name in ("models.py", "presenter.py", "rows.py"):
        imported = _imported(_PAGE_DIR / name)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in imported
        ), name


def test_the_page_adopts_the_window_palette(page) -> None:
    from us_quant.ui_theme import theme_palette

    page.set_palette(theme_palette("dark"))

    assert page._palette is theme_palette("dark")
    assert page.details._palette is theme_palette("dark")
