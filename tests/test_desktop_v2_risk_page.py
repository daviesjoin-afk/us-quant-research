"""Desktop UI v2 risk page tests.

The page replaced ``MainWindow._safety_tab``.  Two claims are pinned here, and
they are the whole reason the page exists:

* it *renders* the limits the risk layer enforces, from a domain value the
  window supplies -- so the numbers on screen and the numbers being applied
  come from one source;
* it stays a pure renderer with no way to change anything.  A risk page that
  could widen a ceiling would make every ceiling advisory, so the absence of
  an editable control is asserted rather than assumed.

The safety prose is checked phrase by phrase, because "we moved the text" is
only true if the operator can still read the same boundaries.
"""

from __future__ import annotations

import ast
from decimal import Decimal
import pathlib

import pytest

from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QTextEdit,
)

from us_quant.desktop_v2.pages.risk import (
    RISK_BOUNDARY,
    SAFETY_ITEMS,
    RiskPage,
    percent,
)
from us_quant.trading.domain.risk import RiskLimits
from us_quant.ui_theme import theme_palette

_PAGE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "pages"
    / "risk.py"
)

_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def limits(
    *,
    gross: Decimal = Decimal("0.50"),
    position: Decimal = Decimal("0.10"),
    daily_loss: Decimal = Decimal("0.02"),
    drawdown: Decimal = Decimal("0.08"),
    margin: bool = False,
) -> RiskLimits:
    return RiskLimits(
        max_gross_exposure_pct=gross,
        max_position_exposure_pct=position,
        daily_loss_halt_pct=daily_loss,
        drawdown_halt_pct=drawdown,
        allow_margin_borrowing=margin,
    )


@pytest.fixture()
def page():
    _qapp()
    widget = RiskPage()
    yield widget
    widget.deleteLater()


def _imported(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# -- rendering ------------------------------------------------------------


def test_an_unloaded_page_shows_placeholders(page) -> None:
    """``None`` means "not supplied yet", not "a limit of zero"."""

    assert page.limits is None
    assert page.gross_card.value_label.text() == "—"
    assert page.margin_card.value_label.text() == "—"


def test_the_page_renders_every_configured_ceiling(page) -> None:
    page.render(limits())
    assert page.gross_card.value_label.text() == "50.0%"
    assert page.position_card.value_label.text() == "10.0%"
    assert page.daily_loss_card.value_label.text() == "2.0%"
    assert page.drawdown_card.value_label.text() == "8.0%"
    assert page.limits is not None


def test_the_page_renders_the_configured_limits_faithfully(page) -> None:
    page.render(
        limits(
            gross=Decimal("0.75"),
            position=Decimal("0.25"),
            daily_loss=Decimal("0.05"),
            drawdown=Decimal("0.15"),
        )
    )
    assert page.gross_card.value_label.text() == "75.0%"
    assert page.position_card.value_label.text() == "25.0%"
    assert page.daily_loss_card.value_label.text() == "5.0%"
    assert page.drawdown_card.value_label.text() == "15.0%"


def test_margin_borrowing_reads_as_forbidden_by_default(page) -> None:
    page.render(limits())
    assert page.margin_card.value_label.text() == "禁止"
    assert (
        theme_palette("light").success
        in page.margin_card.value_label.styleSheet()
    )


def test_enabling_margin_borrowing_is_flagged_not_hidden(page) -> None:
    """The one setting an operator must not miss gets the warning colour."""

    page.render(limits(margin=True))
    assert page.margin_card.value_label.text() == "允许"
    assert (
        theme_palette("light").warning
        in page.margin_card.value_label.styleSheet()
    )


def test_the_page_can_be_repainted_with_a_new_palette(page) -> None:
    page.render(limits())
    page.set_palette(theme_palette("dark"))
    assert (
        theme_palette("dark").success
        in page.margin_card.value_label.styleSheet()
    )
    # A repaint before any render must not raise or colour a placeholder.
    fresh = RiskPage()
    try:
        fresh.set_palette(theme_palette("dark"))
    finally:
        fresh.deleteLater()


# -- the safety copy survives the move ------------------------------------


def test_the_retired_safety_page_text_is_preserved_verbatim(page) -> None:
    page.render(limits())
    text = page.safety_text.toPlainText()
    assert len(SAFETY_ITEMS) == 7
    for item in SAFETY_ITEMS:
        assert item in text


@pytest.mark.parametrize(
    "phrase",
    (
        "IBKR Paper 模拟下单能力默认关闭",
        "模拟端口 4002",
        "不做碎股",
        "中国概念股不进入研究池或交易池",
        "大模型不会被放进实时下单链路",
    ),
)
def test_the_operator_still_sees_each_boundary(page, phrase: str) -> None:
    page.render(limits())
    assert phrase in page.safety_text.toPlainText()


def test_the_page_states_what_risk_can_and_cannot_do(page) -> None:
    """The operator's mental model is part of the split holding."""

    text = page.boundary_text.toPlainText()
    for line in RISK_BOUNDARY:
        assert line in text
    assert "不能：创建订单 ID" in text
    assert "绝不阻止合法减仓" in text


# -- read-only by construction --------------------------------------------


def test_the_page_has_no_control_that_could_change_a_limit(page) -> None:
    """No button, field, combo or spin box -- nothing to click into."""

    for kind in (
        QPushButton,
        QLineEdit,
        QComboBox,
        QAbstractSpinBox,
    ):
        assert page.findChildren(kind) == [], kind.__name__
    # The only text areas are the two read-only blocks.
    editors = page.findChildren(QTextEdit)
    assert editors
    assert all(editor.isReadOnly() for editor in editors)


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
        "us_quant.paper_session",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr",
        "us_quant.risk",
        "ibapi",
    ):
        assert forbidden not in imported, forbidden
        assert not any(
            name.startswith(f"{forbidden}.") for name in imported
        ), forbidden


def test_the_page_renders_domain_types_rather_than_its_own() -> None:
    imported = _imported(_PAGE_PATH)
    assert "us_quant.trading.domain.risk" in imported
    assert not any(
        name == "us_quant.desktop" for name in imported
    ), "the page must not reach back into the legacy window"


# -- formatting -----------------------------------------------------------


def test_percent_formats_a_ratio_for_the_operator() -> None:
    assert percent(Decimal("0.10")) == "10.0%"
    assert percent(Decimal("1")) == "100.0%"
    assert percent(Decimal("0.0025")) == "0.2%"
