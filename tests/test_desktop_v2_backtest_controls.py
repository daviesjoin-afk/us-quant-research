"""Real-Qt tests for the native backtest controls."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.backtest.controls import BacktestControls
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestControlView,
    BacktestFormDraft,
    BacktestStrategyOption,
)


_APP = QApplication.instance() or QApplication([])


def _controls() -> BacktestControls:
    return BacktestControls(
        per_share_commission=Decimal("0.005"),
        minimum_commission=Decimal("1.25"),
        slippage_bps=Decimal("4.0"),
    )


def test_defaults_match_legacy() -> None:
    controls = _controls()
    assert controls.symbol_input.text() == "XLF"
    assert controls.start_date.date() == QDate(2018, 1, 1)
    assert controls.end_date.date() == QDate.currentDate()
    assert controls.capital_spin.minimum() == 100
    assert controls.capital_spin.maximum() == 100_000_000
    assert controls.capital_spin.value() == 1500
    assert controls.weight_spin.minimum() == 1
    assert controls.weight_spin.maximum() == 100
    assert controls.weight_spin.value() == 100
    assert controls.per_share_commission_spin.value() == 0.005
    assert controls.minimum_commission_spin.value() == 1.25
    assert controls.slippage_spin.value() == 4.0


def test_symbol_is_normalized_in_the_draft() -> None:
    controls = _controls()
    controls.symbol_input.setText("  aapl  ")
    assert controls.draft().symbol == "AAPL"


def test_strategy_options_preserve_selection_silently() -> None:
    controls = _controls()
    options = (
        BacktestStrategyOption("version-A", "A · 1.0.0"),
        BacktestStrategyOption("version-B", "B · 2.0.0"),
    )
    controls.set_strategy_options(options)
    controls.strategy_combo.setCurrentIndex(1)
    requested: list[BacktestFormDraft] = []
    compared: list[BacktestFormDraft] = []
    controls.run_selected_requested.connect(requested.append)
    controls.compare_all_requested.connect(compared.append)

    controls.set_strategy_options(options)

    assert controls.strategy_combo.currentData() == "version-B"
    assert requested == []
    assert compared == []


def test_removed_strategy_falls_back_to_first_option() -> None:
    controls = _controls()
    controls.set_strategy_options(
        (
            BacktestStrategyOption("version-A", "A · 1.0.0"),
            BacktestStrategyOption("version-B", "B · 2.0.0"),
        )
    )
    controls.strategy_combo.setCurrentIndex(1)
    controls.set_strategy_options(
        (
            BacktestStrategyOption("version-A", "A · 1.0.0"),
            BacktestStrategyOption("version-C", "C · 3.0.0"),
        )
    )
    assert controls.strategy_combo.currentData() == "version-A"


def test_run_selected_emits_immutable_form_draft() -> None:
    controls = _controls()
    controls.set_strategy_options(
        (BacktestStrategyOption("version-A", "A · 1.0.0"),)
    )
    captured: list[BacktestFormDraft] = []
    controls.run_selected_requested.connect(captured.append)
    controls.run_selected_button.click()
    draft = captured[0]
    assert draft.strategy_version_id == "version-A"
    assert draft.symbol == "XLF"
    assert draft.start_date == date(2018, 1, 1)
    assert draft.initial_equity == 1500
    assert draft.target_weight_percent == 100
    assert draft.per_share_commission == "0.005"
    assert draft.minimum_commission == "1.25"
    assert draft.slippage_bps == "4.0"


def test_compare_all_emits_the_same_draft_boundary() -> None:
    controls = _controls()
    controls.set_strategy_options(
        (BacktestStrategyOption("version-A", "A · 1.0.0"),)
    )
    captured: list[BacktestFormDraft] = []
    controls.compare_all_requested.connect(captured.append)
    controls.compare_all_button.click()
    assert captured[0].strategy_version_id == "version-A"


def test_busy_disables_only_run_buttons() -> None:
    controls = _controls()
    controls.set_controls(
        BacktestControlView(
            run_selected_enabled=False,
            compare_all_enabled=False,
        )
    )
    assert controls.run_selected_button.isEnabled() is False
    assert controls.compare_all_button.isEnabled() is False
    assert controls.strategy_combo.isEnabled() is True
    assert controls.symbol_input.isEnabled() is True
    assert controls.start_date.isEnabled() is True
    assert controls.end_date.isEnabled() is True
    assert controls.capital_spin.isEnabled() is True
    assert controls.weight_spin.isEnabled() is True
    assert controls.per_share_commission_spin.isEnabled() is True
    assert controls.minimum_commission_spin.isEnabled() is True
    assert controls.slippage_spin.isEnabled() is True
