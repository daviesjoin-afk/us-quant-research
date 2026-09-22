"""Page-level tests for the native targeted validation workspace."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.targeted.models import (
    ReplayRow,
    ReviewGateRow,
    ReviewHistoryRow,
    RobustnessRunRow,
    RobustnessScenarioRow,
    TargetPreflightRow,
    TargetPreflightView,
    TargetedControlView,
    TargetedEvidenceView,
    TargetedEvidenceWorkspace,
    TargetedFillRow,
    TargetedMetricView,
    TargetedPositionRow,
    TargetedReviewDetail,
    TargetedRobustnessDetail,
    TargetedRowTone,
    TargetedSessionView,
    TargetedStrategyOption,
    TargetedValidationView,
    TargetedWorkspace,
)
from us_quant.desktop_v2.pages.research.targeted.page import TargetedValidationPage
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def page() -> TargetedValidationPage:
    widget = TargetedValidationPage(palette=theme_palette("dark"))
    yield widget
    widget.deleteLater()


def _controls(**overrides: bool) -> TargetedControlView:
    values = {
        "strategy_enabled": True,
        "target_enabled": True,
        "subscribe_enabled": True,
        "shadow_start_enabled": True,
        "shadow_stop_enabled": False,
        "replay_enabled": True,
        "robustness_enabled": True,
    }
    values.update(overrides)
    return TargetedControlView(**values)


def _session(**overrides: object) -> TargetedSessionView:
    values = {
        "status": TargetedMetricView("未启动", "不进入 IBKR 模拟账户"),
        "equity": TargetedMetricView("—", "启动时读取 IBKR Paper 净值"),
        "realized": TargetedMetricView("$0.00", "已扣双边模拟佣金"),
        "unrealized": TargetedMetricView("$0.00", "按最新有效 mark"),
        "trades": TargetedMetricView("0 / 4", "整股；最多一笔持仓"),
        "explanation": "未启动",
        "positions": (),
        "fills": (),
        "target_status": "未指定",
        "minute_status": "分钟证据：等待输入",
        "preflight": TargetPreflightView("尚未检查", ()),
        "controls": _controls(),
    }
    values.update(overrides)
    return TargetedSessionView(**values)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> TargetedEvidenceView:
    values = {
        "replay_rows": (),
        "robustness_summary": "尚未运行",
        "robustness_rows": (),
        "robustness_scenario_rows": (),
        "walk_forward_summary": "尚未运行",
        "walk_forward_rows": (),
        "overfit_summary": "尚未运行",
        "overfit_rows": (),
        "quality_summary": "尚未运行",
        "quality_rows": (),
        "stress_summary": "尚未运行",
        "stress_rows": (),
        "review_summary": "尚未运行",
        "review_history_rows": (),
        "review_gate_rows": (),
        "selected_robustness_run_id": None,
        "selected_review_run_id": None,
    }
    values.update(overrides)
    return TargetedEvidenceView(**values)  # type: ignore[arg-type]


def _view(
    session: TargetedSessionView | None = None,
    evidence: TargetedEvidenceView | None = None,
    **overrides: object,
) -> TargetedValidationView:
    values = {
        "session": session or _session(),
        "evidence": evidence or _evidence(),
        "active_workspace": None,
        "active_evidence_tab": None,
    }
    values.update(overrides)
    return TargetedValidationView(**values)  # type: ignore[arg-type]


def _select_key(table, key: str) -> None:
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.data(0x0100) == key:
            table.selectRow(row)
            return
    raise AssertionError(f"row {key!r} not found")


# -- controls -------------------------------------------------------------


def test_the_page_owns_every_targeted_control(page: TargetedValidationPage) -> None:
    controls = page.session_panel.controls
    assert controls.strategy_combo is not None
    assert controls.target_symbol_input is not None
    assert controls.target_symbol_apply_button is not None
    assert controls.target_symbol_subscribe_button is not None
    assert controls.shadow_start_button is not None
    assert controls.shadow_stop_button is not None
    assert controls.replay_button is not None
    assert controls.robustness_button is not None


def test_a_start_click_only_emits(page: TargetedValidationPage) -> None:
    seen: list[str] = []
    page.shadow_start_requested.connect(lambda: seen.append("start"))
    page.session_panel.controls.shadow_start_button.click()
    assert seen == ["start"]


def test_a_stop_click_only_emits(page: TargetedValidationPage) -> None:
    page.session_panel.controls.render(_controls(shadow_stop_enabled=True))
    seen: list[str] = []
    page.shadow_stop_requested.connect(lambda: seen.append("stop"))
    page.session_panel.controls.shadow_stop_button.click()
    assert seen == ["stop"]


def test_target_apply_and_subscribe_emit_normalized_intent(page: TargetedValidationPage) -> None:
    seen: list[tuple[str, str]] = []
    page.target_apply_requested.connect(lambda value: seen.append(("apply", value)))
    page.target_subscribe_requested.connect(lambda value: seen.append(("subscribe", value)))
    page.set_target_symbol(" nvda ")
    page.session_panel.controls.target_symbol_apply_button.click()
    page.session_panel.controls.target_symbol_subscribe_button.click()
    assert seen == [("apply", "NVDA"), ("subscribe", "NVDA")]


def test_replay_and_robustness_clicks_only_emit(page: TargetedValidationPage) -> None:
    seen: list[str] = []
    page.replay_requested.connect(lambda: seen.append("replay"))
    page.robustness_requested.connect(lambda: seen.append("robustness"))
    page.session_panel.controls.replay_button.click()
    page.session_panel.controls.robustness_button.click()
    assert seen == ["replay", "robustness"]


def test_strategy_selection_emits_the_version_id(page: TargetedValidationPage) -> None:
    seen: list[str] = []
    page.strategy_selected.connect(seen.append)
    page.set_strategy_options(
        (
            TargetedStrategyOption("v1", "Version 1"),
            TargetedStrategyOption("v2", "Version 2"),
        ),
        "v1",
    )
    combo = page.session_panel.controls.strategy_combo
    combo.setCurrentIndex(combo.findData("v2"))
    assert seen == ["v2"]


def test_programmatic_setters_are_silent(page: TargetedValidationPage) -> None:
    seen: list[str] = []
    page.strategy_selected.connect(seen.append)
    page.target_apply_requested.connect(seen.append)
    page.set_strategy_options((TargetedStrategyOption("v1", "Version 1"),), "v1")
    page.set_selected_strategy_version("v1")
    page.set_target_symbol("AAPL")
    assert seen == []


def test_control_view_enables_and_disables_each_control(page: TargetedValidationPage) -> None:
    controls = page.session_panel.controls
    controls.render(
        _controls(
            strategy_enabled=False,
            target_enabled=False,
            subscribe_enabled=False,
            shadow_start_enabled=False,
            shadow_stop_enabled=True,
            replay_enabled=False,
            robustness_enabled=False,
        )
    )
    assert not controls.strategy_combo.isEnabled()
    assert not controls.target_symbol_input.isEnabled()
    assert not controls.target_symbol_apply_button.isEnabled()
    assert not controls.target_symbol_subscribe_button.isEnabled()
    assert not controls.shadow_start_button.isEnabled()
    assert controls.shadow_stop_button.isEnabled()
    assert not controls.replay_button.isEnabled()
    assert not controls.robustness_button.isEnabled()


# -- rendering ------------------------------------------------------------


def test_render_updates_cards_tables_and_preflight(page: TargetedValidationPage) -> None:
    session = _session(
        status=TargetedMetricView("运行中", "内部影子成交；券商订单 0"),
        positions=(
            TargetedPositionRow(
                "AAPL",
                ("AAPL", "1", "100", "09:31", "101", "IBKR", "Type 1", "内部影子"),
            ),
        ),
        fills=(
            TargetedFillRow(
                "s1",
                ("09:31", "AAPL", "BUY", "1", "100", "0.35", "—", "信号", "IBKR", "Type 1", "s1"),
                tone=TargetedRowTone.SUCCESS,
            ),
        ),
        target_status="AAPL · 已设置",
        minute_status="分钟证据 · AAPL：可用 10 / 总计 10 行",
        preflight=TargetPreflightView(
            "可启动内部策略仿真 · 硬门 6/6",
            (TargetPreflightRow("identity", ("标的身份", "通过", "AAPL", "美股", "标的", "阻断启动")),),
        ),
    )
    page.render(_view(session=session))
    assert page.layout().indexOf(page.session_panel) >= 0
    assert page.session_panel.status_card.value_label.text() == "运行中"
    assert page.session_panel.position_table.rowCount() == 1
    assert page.session_panel.fill_table.rowCount() == 1
    assert page.preflight_table.rowCount() == 1
    assert page.preflight_summary.text().startswith("可启动")
    assert page.session_panel.fill_table.item(0, 1).foreground().color().name() == QColor(
        theme_palette("dark").success
    ).name()


def test_render_updates_all_seven_evidence_tabs(page: TargetedValidationPage) -> None:
    evidence = _evidence(
        replay_rows=(ReplayRow("r1", ("run", "AAPL")),),
        robustness_rows=(RobustnessRunRow("rob", ("rob", "AAPL")),),
        robustness_scenario_rows=(RobustnessScenarioRow("base", ("base", "3")),),
        review_history_rows=(ReviewHistoryRow("rev", ("rev", "AAPL")),),
        review_gate_rows=(ReviewGateRow("gate", ("硬门", "通过")),),
    )
    page.render(_view(evidence=evidence))
    assert page.evidence_panel.tabs.count() == 7
    assert page.evidence_panel.replay_table.rowCount() == 1
    assert page.evidence_panel.robustness_table.rowCount() == 1
    assert page.evidence_panel.scenario_table.rowCount() == 1
    assert page.evidence_panel.review_history_table.rowCount() == 1
    assert page.evidence_panel.review_gate_table.rowCount() == 1


def test_robustness_selection_emits_and_survives_a_repaint(page: TargetedValidationPage) -> None:
    rows = (
        RobustnessRunRow("run-a", ("aaa", "AAPL")),
        RobustnessRunRow("run-b", ("bbb", "MSFT")),
    )
    page.render(
        _view(
            evidence=_evidence(
                robustness_rows=rows,
                selected_robustness_run_id="run-a",
            )
        )
    )
    seen: list[str] = []
    page.robustness_run_selected.connect(seen.append)
    _select_key(page.evidence_panel.robustness_table, "run-b")
    assert seen == ["run-b"]
    page.render(
        _view(
            evidence=_evidence(
                robustness_rows=rows,
                selected_robustness_run_id="run-b",
            )
        )
    )
    assert page.evidence_panel.robustness_table.selected_key() == "run-b"


def test_review_selection_emits_and_survives_a_repaint(page: TargetedValidationPage) -> None:
    rows = (
        ReviewHistoryRow("review-a", ("aaa", "AAPL")),
        ReviewHistoryRow("review-b", ("bbb", "MSFT")),
    )
    page.render(
        _view(
            evidence=_evidence(
                review_history_rows=rows,
                selected_review_run_id="review-a",
            )
        )
    )
    seen: list[str] = []
    page.review_run_selected.connect(seen.append)
    _select_key(page.evidence_panel.review_history_table, "review-b")
    assert seen == ["review-b"]
    page.render(
        _view(
            evidence=_evidence(
                review_history_rows=rows,
                selected_review_run_id="review-b",
            )
        )
    )
    assert page.evidence_panel.review_history_table.selected_key() == "review-b"


def test_active_tabs_are_one_shot_and_do_not_reset_user_choice(page: TargetedValidationPage) -> None:
    page.workspace_tabs.setCurrentIndex(3)
    page.evidence_panel.tabs.setCurrentIndex(6)
    page.render(_view())
    assert page.workspace_tabs.currentIndex() == 3
    assert page.evidence_panel.tabs.currentIndex() == 6
    page.render(_view(active_workspace=1, active_evidence_tab=2))
    assert page.workspace_tabs.currentIndex() == 1
    assert page.evidence_panel.tabs.currentIndex() == 2


def test_palette_update_recolours_warning_cells(page: TargetedValidationPage) -> None:
    light = theme_palette("light")
    page.set_palette(light)
    page.render(
        _view(
            session=_session(
                preflight=TargetPreflightView(
                    "暂不可启动",
                    (TargetPreflightRow("identity", ("标的身份", "未通过", "—", "美股", "标的", "阻断启动"), tone=TargetedRowTone.ERROR),),
                )
            )
        )
    )
    colour = page.preflight_table.item(0, 1).foreground().color()
    assert colour.name() == QColor(light.error).name()


# -- semantic navigation ---------------------------------------------------
#
# The page publishes its own vocabulary for "show me this panel".  A caller --
# tooling, a shortcut, a future sidebar -- names a workspace instead of reaching
# for ``workspace_tabs`` or ``evidence_panel``, and names an index instead of
# writing a bare integer.  None of these methods runs an evaluation: that stays
# an explicit operator intent through ``replay_requested`` /
# ``robustness_requested``.


def test_the_workspace_keys_are_the_documented_panels(
    page: TargetedValidationPage,
) -> None:
    assert [workspace.name for workspace in TargetedWorkspace] == [
        "STRATEGY",
        "POSITIONS",
        "FILLS",
        "EVIDENCE",
        "PREFLIGHT",
    ]
    assert page.workspace_tabs.count() == len(TargetedWorkspace)
    assert [
        workspace.name for workspace in TargetedEvidenceWorkspace
    ] == [
        "REPLAY",
        "ROBUSTNESS",
        "WALK_FORWARD",
        "OVERFIT",
        "DATA_QUALITY",
        "EXECUTION_STRESS",
        "REVIEW",
    ]
    assert page.evidence_panel.tabs.count() == len(
        TargetedEvidenceWorkspace
    )


@pytest.mark.parametrize("workspace", list(TargetedWorkspace))
def test_every_workspace_key_selects_its_own_panel(
    page: TargetedValidationPage, workspace: TargetedWorkspace
) -> None:
    page.set_active_workspace(workspace)

    assert page.workspace_tabs.currentIndex() == int(workspace)


@pytest.mark.parametrize(
    "workspace", list(TargetedEvidenceWorkspace)
)
def test_every_evidence_key_selects_its_own_section(
    page: TargetedValidationPage,
    workspace: TargetedEvidenceWorkspace,
) -> None:
    page.set_active_evidence_workspace(workspace)

    assert page.evidence_panel.tabs.currentIndex() == int(workspace)


@pytest.mark.parametrize("detail", list(TargetedRobustnessDetail))
def test_every_robustness_detail_key_selects_its_own_view(
    page: TargetedValidationPage, detail: TargetedRobustnessDetail
) -> None:
    page.set_active_robustness_detail(detail)

    assert page.evidence_panel.robustness_detail_tabs.currentIndex() == int(
        detail
    )


@pytest.mark.parametrize("detail", list(TargetedReviewDetail))
def test_every_review_detail_key_selects_its_own_view(
    page: TargetedValidationPage, detail: TargetedReviewDetail
) -> None:
    page.set_active_review_detail(detail)

    assert page.evidence_panel.review_detail_tabs.currentIndex() == int(
        detail
    )


def _shows(panel_widget, table) -> bool:
    """Does the visible panel carry ``table``, directly or as its own child?

    Some sections add the table straight as the tab and some wrap it in a
    panel, so the test asks the question that actually matters -- is this the
    table the operator is now looking at -- rather than pinning the wrapper.
    """

    return panel_widget is table or panel_widget.isAncestorOf(table)


def test_the_named_details_address_the_tables_the_page_shows(
    page: TargetedValidationPage,
) -> None:
    """The semantic key lands on the table the operator expects to read."""

    page.set_active_robustness_detail(TargetedRobustnessDetail.SCENARIOS)
    assert _shows(
        page.evidence_panel.robustness_detail_tabs.currentWidget(),
        page.evidence_panel.scenario_table,
    )
    page.set_active_robustness_detail(TargetedRobustnessDetail.HISTORY)
    assert _shows(
        page.evidence_panel.robustness_detail_tabs.currentWidget(),
        page.evidence_panel.robustness_table,
    )

    page.set_active_review_detail(TargetedReviewDetail.GATES)
    assert _shows(
        page.evidence_panel.review_detail_tabs.currentWidget(),
        page.evidence_panel.review_gate_table,
    )
    page.set_active_review_detail(TargetedReviewDetail.HISTORY)
    assert _shows(
        page.evidence_panel.review_detail_tabs.currentWidget(),
        page.evidence_panel.review_history_table,
    )


def test_navigation_does_not_run_an_evaluation(
    page: TargetedValidationPage,
) -> None:
    """Showing the evidence archive is not running the evidence."""

    seen: list[str] = []
    for name in (
        "replay_requested",
        "robustness_requested",
        "shadow_start_requested",
        "shadow_stop_requested",
    ):
        getattr(page, name).connect(lambda _name=name: seen.append(_name))

    page.set_active_workspace(TargetedWorkspace.EVIDENCE)
    page.set_active_evidence_workspace(
        TargetedEvidenceWorkspace.ROBUSTNESS
    )
    page.set_active_robustness_detail(TargetedRobustnessDetail.SCENARIOS)
    page.set_active_review_detail(TargetedReviewDetail.GATES)

    assert seen == []


def test_the_navigation_keys_are_pinned_to_their_panels(
    page: TargetedValidationPage,
) -> None:
    """The mapping from key to panel label, not just "index equals index".

    Without this, a key pointed at the wrong panel would still satisfy every
    "``currentIndex() == int(workspace)``" assertion above.
    """

    workspace_labels = {
        TargetedWorkspace.STRATEGY: "策略",
        TargetedWorkspace.POSITIONS: "持仓",
        TargetedWorkspace.FILLS: "委托",
        TargetedWorkspace.EVIDENCE: "档案",
        TargetedWorkspace.PREFLIGHT: "风控",
    }
    for workspace, label in workspace_labels.items():
        page.set_active_workspace(workspace)
        assert page.workspace_tabs.tabText(page.workspace_tabs.currentIndex()) == label

    evidence_labels = {
        TargetedEvidenceWorkspace.REPLAY: "单会话回放",
        TargetedEvidenceWorkspace.ROBUSTNESS: "多日稳健性",
        TargetedEvidenceWorkspace.WALK_FORWARD: "时间隔离验证",
        TargetedEvidenceWorkspace.OVERFIT: "过拟合诊断",
        TargetedEvidenceWorkspace.DATA_QUALITY: "数据质量",
        TargetedEvidenceWorkspace.EXECUTION_STRESS: "执行压力",
        TargetedEvidenceWorkspace.REVIEW: "独立评审",
    }
    for workspace, label in evidence_labels.items():
        page.set_active_evidence_workspace(workspace)
        assert page.evidence_panel.tabs.tabText(
            page.evidence_panel.tabs.currentIndex()
        ) == label

    robustness_labels = {
        TargetedRobustnessDetail.HISTORY: "评估历史",
        TargetedRobustnessDetail.SCENARIOS: "参数扰动",
    }
    for detail, label in robustness_labels.items():
        page.set_active_robustness_detail(detail)
        assert page.evidence_panel.robustness_detail_tabs.tabText(
            page.evidence_panel.robustness_detail_tabs.currentIndex()
        ) == label

    review_labels = {
        TargetedReviewDetail.HISTORY: "评审历史",
        TargetedReviewDetail.GATES: "硬门明细",
    }
    for detail, label in review_labels.items():
        page.set_active_review_detail(detail)
        assert page.evidence_panel.review_detail_tabs.tabText(
            page.evidence_panel.review_detail_tabs.currentIndex()
        ) == label


def test_the_navigation_keys_carry_no_behaviour() -> None:
    """Plain ``IntEnum`` positions, not a workflow phase with methods."""

    from enum import IntEnum

    for enum_type in (
        TargetedWorkspace,
        TargetedEvidenceWorkspace,
        TargetedRobustnessDetail,
        TargetedReviewDetail,
    ):
        assert issubclass(enum_type, IntEnum), enum_type
        for member in enum_type:
            own = {
                name
                for name in vars(member)
                if not name.startswith("_") and name not in vars(IntEnum)
            }
            assert not own, (member.name, own)
