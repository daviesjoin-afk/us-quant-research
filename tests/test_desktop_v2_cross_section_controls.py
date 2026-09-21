"""Real-Qt tests for the native cross-section controls."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.cross_section.controls import (
    CrossSectionControls,
)
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)


_APP = QApplication.instance() or QApplication([])


def _controls(research_capital: int = 1500) -> CrossSectionControls:
    return CrossSectionControls(research_capital=research_capital)


def test_default_capital_comes_from_the_constructor() -> None:
    controls = _controls(2750)
    assert controls.capital_spin.value() == 2750
    assert controls.current_draft() == CrossSectionResearchDraft(2750)


def test_capital_range_and_affixes_are_frozen() -> None:
    controls = _controls()
    assert controls.capital_spin.minimum() == 100
    assert controls.capital_spin.maximum() == 100_000_000
    assert controls.capital_spin.prefix() == "$"
    assert controls.capital_spin.suffix() == " 历史研究情景"
    assert controls.run_button.text() == "运行复权价研究代理"
    assert "晋级门硬阻断" in controls.warning_label.text()


def test_operator_capital_change_emits_intent() -> None:
    controls = _controls()
    seen: list[int] = []
    controls.capital_changed.connect(seen.append)
    controls.capital_spin.setValue(2500)
    assert seen == [2500]


def test_set_research_capital_is_silent_by_default() -> None:
    controls = _controls()
    seen: list[int] = []
    controls.capital_changed.connect(seen.append)
    controls.set_research_capital(3000)
    assert controls.capital_spin.value() == 3000
    assert seen == []


def test_set_research_capital_can_emit_when_explicitly_requested() -> None:
    controls = _controls()
    seen: list[int] = []
    controls.capital_changed.connect(seen.append)
    controls.set_research_capital(3000, emit_change=True)
    assert controls.capital_spin.value() == 3000
    assert seen == [3000]


def test_current_draft_is_immutable_and_emits_nothing() -> None:
    controls = _controls()
    seen: list[int] = []
    controls.capital_changed.connect(seen.append)
    draft = controls.current_draft()
    assert draft == CrossSectionResearchDraft(1500)
    assert not hasattr(draft, "__dict__")
    assert seen == []


def test_run_emits_the_current_draft() -> None:
    controls = _controls()
    controls.set_research_capital(2750)
    seen: list[CrossSectionResearchDraft] = []
    controls.run_requested.connect(lambda: seen.append(controls.current_draft()))
    controls.run_button.click()
    assert seen == [CrossSectionResearchDraft(2750)]


def test_run_requested_is_a_plain_intent_signal() -> None:
    controls = _controls()
    seen: list[None] = []
    controls.run_requested.connect(lambda: seen.append(None))
    controls.run_button.click()
    assert seen == [None]