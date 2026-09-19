"""Desktop UI v2 strategy page tests.

The page replaced ``MainWindow._strategy_manager_tab`` outright.  These tests
pin both halves of that claim: it *renders* the governance catalogue correctly,
and it stays a pure renderer -- no application service, no repository, no
``sqlite3``, no execution import.

The signal boundary is asserted by driving the widgets, not by reading Qt's
connection table: a clone with no semver must emit nothing, and a clone with
one must hand the parameters over as text without validating them.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pathlib

import pytest

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.strategy import (
    STATUS_LABELS,
    VERSION_COLUMNS,
    StrategyPage,
    status_label,
    strategy_option_label,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.ui_theme import theme_palette

_PAGE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "pages"
    / "strategy.py"
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _version(
    version_id: str,
    *,
    strategy_id: str = "buy-hold",
    semver: str = "1.0.0-research",
    status: StrategyStatus = StrategyStatus.RESEARCH,
    gate_passed: bool = False,
    gate_reason: str = "尚未通过研究晋级门",
    parameters: dict | None = None,
    created_at: datetime = NOW,
) -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name=f"{strategy_id} 名称",
            description=f"{strategy_id} 说明",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=version_id,
            parameter_hash="0123456789abcdef0123456789abcdef",
        ),
        semver=semver,
        status=status,
        mode=(
            StrategyMode.PAPER_SHADOW
            if status is StrategyStatus.PAPER_SHADOW
            else StrategyMode.RESEARCH
        ),
        parameters=parameters if parameters is not None else {"whole_shares": True},
        universe_hash="universe-hash-value-1234567890",
        code_hash="code-hash",
        risk_budget_pct=Decimal("0.1"),
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.fixture()
def page():
    _qapp()
    widget = StrategyPage()
    yield widget
    widget.deleteLater()


def _cell(page: StrategyPage, row: int, column: int) -> str:
    item = page.version_table.item(row, column)
    return item.text() if item is not None else ""


def _record(signal, sink: list) -> None:
    signal.connect(lambda *args: sink.append(args))


# -- rendering ------------------------------------------------------------


def test_an_empty_catalogue_renders_placeholders(page) -> None:
    page.render(())
    assert _cell(page, 0, 0) == ""
    assert page.total_card.value_label.text() == "0"
    assert page.selected_version() is None


def test_the_cards_count_by_status(page) -> None:
    page.render(
        (
            _version("v1"),
            _version("v2", status=StrategyStatus.PAPER_SHADOW),
            _version("v3", status=StrategyStatus.PAUSED),
            _version("v4", status=StrategyStatus.LEGACY_INVALIDATED),
        )
    )
    assert page.total_card.value_label.text() == "3"
    assert page.research_card.value_label.text() == "1"
    assert page.shadow_card.value_label.text() == "1"
    assert page.blocked_card.value_label.text() == "1"


def test_retired_versions_are_counted_but_not_listed(page) -> None:
    """They are audit records, not candidates; every action on them is refused."""

    page.render(
        (
            _version("v1"),
            _version("v2", status=StrategyStatus.LEGACY_INVALIDATED),
        )
    )
    assert page.version_table.rowCount() == 1
    assert page.blocked_card.value_label.text() == "1"


def test_the_table_has_the_frozen_columns(page) -> None:
    page.render((_version("v1"),))
    assert page.version_table.columnCount() == len(VERSION_COLUMNS)
    assert [
        page.version_table.horizontalHeaderItem(index).text()
        for index in range(page.version_table.columnCount())
    ] == list(VERSION_COLUMNS)


def test_a_row_shows_the_governance_fields(page) -> None:
    page.render((_version("v1", strategy_id="dual-ma-trend"),))

    assert _cell(page, 0, 0) == "dual-ma-trend"
    assert _cell(page, 0, 1) == "dual-ma-trend 名称"
    assert _cell(page, 0, 2) == "1.0.0-research"
    assert _cell(page, 0, 3) == "研究"
    assert _cell(page, 0, 4) == "research"
    assert _cell(page, 0, 5) == "10.0%"
    assert _cell(page, 0, 6) == "0123456789ab"
    assert _cell(page, 0, 7) == "universe-hash-valu"
    assert _cell(page, 0, 8) == "阻断"
    assert _cell(page, 0, 9) == NOW.isoformat()
    assert _cell(page, 0, 10) == "dual-ma-trend 说明"


def test_a_passed_gate_reads_as_passed(page) -> None:
    page.render((_version("v1", gate_passed=True),))
    assert _cell(page, 0, 8) == "通过"


def test_a_blocked_gate_is_coloured(page) -> None:
    page.render((_version("v1"),))
    item = page.version_table.item(0, 0)
    assert item is not None
    assert (
        item.foreground().color().name()
        == page._palette.warning.lower()  # noqa: SLF001
    )


def test_a_passed_gate_is_not_coloured(page) -> None:
    page.render((_version("v1", gate_passed=True),))
    item = page.version_table.item(0, 0)
    assert item is not None
    palette = theme_palette("light")
    assert (
        item.foreground().color().name() != palette.warning.lower()
    )


def test_the_palette_can_be_replaced(page) -> None:
    """The window owns the theme; the page re-renders when told about it."""

    page.set_palette(theme_palette("dark"))
    page.render((_version("v1"),))
    item = page.version_table.item(0, 0)
    assert item is not None
    assert (
        item.foreground().color().name()
        == theme_palette("dark").warning.lower()
    )


# -- selection detail -----------------------------------------------------


def test_selecting_a_row_fills_the_editor(page) -> None:
    page.render((_version("v1", parameters={"whole_shares": True}),))
    assert '"whole_shares": true' in page.parameter_editor.toPlainText()
    assert page.semver_input.text() == "1.0.0-research.next"


def test_the_governance_block_carries_the_evidence(page) -> None:
    page.render((_version("v1", strategy_id="dual-ma-trend"),))
    text = page.governance_text.toPlainText()

    assert "Strategy ID：dual-ma-trend" in text
    assert "Version ID：v1" in text
    assert "参数 Hash：0123456789abcdef0123456789abcdef" in text
    assert "股票池 Hash：universe-hash-value-1234567890" in text
    assert "代码 Hash：code-hash" in text
    assert "风险预算：10.0%" in text
    assert "参数约束：short_window" in text
    assert "晋级门：阻断" in text
    assert "尚未通过研究晋级门" in text
    assert "自动下单：关闭" in text


def test_no_selection_disables_every_action(page) -> None:
    page.render(())
    for button in (
        page.clone_button,
        page.shadow_button,
        page.pause_button,
        page.stop_button,
    ):
        assert not button.isEnabled()


@pytest.mark.parametrize(
    "status,shadow,pause,stop",
    [
        (StrategyStatus.RESEARCH, False, False, True),
        (StrategyStatus.PAPER_SHADOW, False, True, True),
        (StrategyStatus.PAUSED, False, False, True),
        (StrategyStatus.STOPPED, False, False, False),
    ],
)
def test_the_button_rules_follow_the_status(
    page, status, shadow, pause, stop
) -> None:
    page.render((_version("v1", status=status),))
    assert page.clone_button.isEnabled()
    assert page.shadow_button.isEnabled() is shadow
    assert page.pause_button.isEnabled() is pause
    assert page.stop_button.isEnabled() is stop


def test_a_paused_gated_version_may_ask_for_shadow(page) -> None:
    page.render(
        (
            _version(
                "v1",
                status=StrategyStatus.PAUSED,
                gate_passed=True,
            ),
        )
    )
    assert page.shadow_button.isEnabled()


def test_a_research_version_may_not_ask_for_shadow(page) -> None:
    page.render((_version("v1", status=StrategyStatus.RESEARCH),))
    assert not page.shadow_button.isEnabled()


def test_the_operator_keeps_their_row_across_a_repaint(page) -> None:
    first = _version("v1", created_at=NOW)
    second = _version("v2", created_at=NOW + timedelta(days=1))
    page.render((first, second))
    page.version_table.selectRow(1)
    page.render((first, second))
    assert page.selected_version_id() == "v2"


# -- signals --------------------------------------------------------------


def test_selecting_a_row_reports_it(page) -> None:
    seen: list = []
    _record(page.version_selected, seen)
    page.render((_version("v1"), _version("v2")))
    page.version_table.selectRow(1)
    assert seen[-1] == ("v2",)


def test_a_clone_request_hands_over_text(page) -> None:
    seen: list = []
    _record(page.clone_requested, seen)
    page.render((_version("v1", parameters={"whole_shares": True}),))

    page.semver_input.setText("1.0.1-research")
    page.clone_button.click()

    assert len(seen) == 1
    version_id, semver, parameters_json = seen[0]
    assert version_id == "v1"
    assert semver == "1.0.1-research"
    assert '"whole_shares": true' in parameters_json


def test_an_empty_semver_asks_for_nothing(page) -> None:
    seen: list = []
    _record(page.clone_requested, seen)
    page.render((_version("v1"),))
    page.semver_input.setText("   ")
    page.clone_button.click()

    assert seen == []
    assert not page.hint_label.isHidden()
    assert "新版本号" in page.hint_label.text()


def test_a_clone_with_no_selection_asks_for_nothing(page) -> None:
    seen: list = []
    _record(page.clone_requested, seen)
    page.render(())
    page.clone_button.click()
    assert seen == []


def test_invalid_json_is_not_the_pages_business(page) -> None:
    """The page hands the text over; the application decides validity."""

    seen: list = []
    _record(page.clone_requested, seen)
    page.render((_version("v1"),))
    page.parameter_editor.setPlainText("{not json")
    page.clone_button.click()

    assert len(seen) == 1
    assert seen[0][2] == "{not json"


def test_a_transition_request_reports_the_target(page) -> None:
    seen: list = []
    _record(page.transition_requested, seen)
    page.render((_version("v1"),))

    page.stop_button.click()
    assert seen[-1] == ("v1", "stopped")

    seen.clear()
    page.render((_version("v2", status=StrategyStatus.PAPER_SHADOW),))
    page.pause_button.click()
    assert seen[-1] == ("v2", "paused")


def test_the_shadow_request_reports_the_target(page) -> None:
    seen: list = []
    _record(page.transition_requested, seen)
    page.render(
        (_version("v1", status=StrategyStatus.PAUSED, gate_passed=True),)
    )
    page.shadow_button.click()
    assert seen[-1] == ("v1", "paper_shadow")


def test_showing_a_version_does_not_touch_runtime_selection(page) -> None:
    """Spec 75: the page has no runtime-selection surface at all."""

    names = {
        node.id
        for node in ast.walk(ast.parse(_PAGE_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    assert "StrategySelectionService" not in names
    assert not hasattr(page, "select_runtime_strategy")
    assert not hasattr(page, "strategy_selection")


# -- labels ---------------------------------------------------------------


def test_every_status_has_a_translation() -> None:
    assert set(STATUS_LABELS) == {
        status.value for status in StrategyStatus
    }
    assert status_label(StrategyStatus.LEGACY_INVALIDATED) == "旧结果已失效"
    assert status_label(StrategyStatus.PAPER_SHADOW) == "Paper影子"


def test_the_combo_label_matches_the_pages_vocabulary() -> None:
    assert (
        strategy_option_label(_version("v1", semver="2.0.0-research"))
        == "buy-hold 名称 · 2.0.0-research · 研究"
    )


# -- purity ---------------------------------------------------------------


def _imported(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_page_imports_no_business_service() -> None:
    imported = _imported(_PAGE_PATH)
    for forbidden in (
        "sqlite3",
        "us_quant.sqlite_support",
        "us_quant.trading.adapters",
        "us_quant.trading.application",
        "us_quant.trading.composition",
        "us_quant.auto_quant",
        "us_quant.paper_trading_service",
        "us_quant.paper_workflow",
        "us_quant.ibkr",
        "us_quant.risk",
    ):
        assert forbidden not in imported, forbidden
        assert not any(
            name.startswith(f"{forbidden}.") for name in imported
        ), forbidden


def test_the_page_renders_domain_types() -> None:
    imported = _imported(_PAGE_PATH)
    assert "us_quant.trading.domain.strategy" in imported
    assert "us_quant.trading.domain.strategy_parameters" in imported
