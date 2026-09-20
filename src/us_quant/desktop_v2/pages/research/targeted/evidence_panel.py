"""The seven evidence tabs for the targeted validation workspace."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceView,
)
from us_quant.desktop_v2.pages.research.targeted.tables import TargetedTable
from us_quant.ui_theme import ThemePalette, theme_palette


class TargetedEvidencePanel(QWidget):
    """Owns the seven evidence tabs and their table components."""

    robustness_run_selected = Signal(str)
    review_run_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = theme_palette("dark")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.replay_table = TargetedTable(
            ("Run ID", "代码", "策略版本", "行情源", "分钟区间", "有效行", "缺口",
             "总收益", "最大回撤", "已实现P&L", "成交", "佣金"),
            palette=self._palette,
        )
        self.tabs.addTab(self._panel("分钟回放结果与证据", self.replay_table), "单会话回放")
        self._build_robustness()
        self._build_walk_forward()
        self._build_overfit()
        self._build_quality()
        self._build_stress()
        self._build_review()

    @staticmethod
    def _panel(title: str, *widgets: QWidget, summary: QLabel | None = None) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if summary is not None:
            layout.addWidget(summary)
        for widget in widgets:
            layout.addWidget(widget)
        return panel

    @staticmethod
    def _summary(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("subtitle")
        label.setWordWrap(True)
        return label

    def _build_robustness(self) -> None:
        self.robustness_summary = self._summary(
            "尚未运行多日稳健性评估；结果不会自动晋级策略。"
        )
        self.robustness_table = TargetedTable(
            ("Run ID", "代码", "策略版本", "行情源", "会话区间", "有效/总计",
             "收益方向一致", "证据等级"),
            sort_column=0,
            palette=self._palette,
        )
        self.robustness_table.run_selected.connect(self.robustness_run_selected.emit)
        self.scenario_table = TargetedTable(
            ("参数场景", "会话", "复合收益", "平均", "中位数", "最差会话",
             "盈利会话", "最大回撤", "成交", "佣金"),
            sort_column=0,
            palette=self._palette,
        )
        detail = QTabWidget()
        detail.setDocumentMode(True)
        detail.addTab(self.robustness_table, "评估历史")
        detail.addTab(self.scenario_table, "参数扰动")
        self.tabs.addTab(
            self._panel("多日稳健性评估", detail, summary=self.robustness_summary),
            "多日稳健性",
        )

    def _build_walk_forward(self) -> None:
        self.walk_forward_summary = self._summary(
            "时间隔离验证至少需要 20 个完整有效会话；测试集永不参与参数选择。"
        )
        self.walk_forward_table = TargetedTable(
            ("Run ID", "折", "训练选中", "训练区间", "验证区间", "验证策略",
             "验证基准", "验证门", "测试区间", "测试策略", "测试基准", "测试超额"),
            sort_column=0,
            tone_columns=(7,),
            palette=self._palette,
        )
        self.tabs.addTab(
            self._panel("时间隔离验证", self.walk_forward_table,
                        summary=self.walk_forward_summary),
            "时间隔离验证",
        )

    def _build_overfit(self) -> None:
        self.overfit_summary = self._summary(
            "PBO/CSCV 与 DSR 至少需要 20 个同步完整会话；"
            "统计条件不足时明确显示不可估计。"
        )
        self.overfit_table = TargetedTable(
            ("Run ID", "代码", "有效/总计", "候选", "CSCV分区", "组合", "PBO",
             "样本外亏损", "平均退化", "DSR概率", "DSR候选", "证据等级"),
            tone_columns=(6, 7, 9, 11),
            palette=self._palette,
        )
        self.tabs.addTab(
            self._panel("过拟合诊断", self.overfit_table, summary=self.overfit_summary),
            "过拟合诊断",
        )

    def _build_quality(self) -> None:
        self.quality_summary = self._summary(
            "数据质量报告检查每个会话的 346 个预期分钟、连续缺口、"
            "异常报价、行情年龄和一档数量覆盖。"
        )
        self.quality_table = TargetedTable(
            ("交易日", "原始行", "可用行", "完整率", "缺失", "最长缺口", "Stale",
             "异常报价", "Age P95", "一档数量覆盖", "状态"),
            tone_columns=(10,),
            palette=self._palette,
        )
        self.tabs.addTab(
            self._panel("数据质量", self.quality_table, summary=self.quality_summary),
            "数据质量",
        )

    def _build_stress(self) -> None:
        self.stress_summary = self._summary(
            "执行压力测试将配置成本与 5bps、10bps+双倍佣金场景对比，"
            "并检查最优价一档参与率。"
        )
        self.stress_table = TargetedTable(
            ("成本场景", "滑点", "单笔佣金", "会话", "复合收益", "相对退化",
             "最大回撤", "成交", "总佣金"),
            tone_columns=(4,),
            palette=self._palette,
        )
        self.tabs.addTab(
            self._panel("执行压力", self.stress_table, summary=self.stress_summary),
            "执行压力",
        )

    def _build_review(self) -> None:
        self.review_summary = self._summary(
            "独立评审汇总真实流来源、时间隔离、过拟合、序列相关性、"
            "成本与成交硬门；不会自动批准策略。"
        )
        self.review_history_table = TargetedTable(
            ("Run ID", "代码", "行情源", "证据来源", "完整会话", "测试会话",
             "有效样本", "HAC为正", "通过门", "结论"),
            tone_columns=(7, 9),
            palette=self._palette,
        )
        self.review_history_table.run_selected.connect(self.review_run_selected.emit)
        self.review_gate_table = TargetedTable(
            ("硬门", "状态", "观测值", "要求", "证据", "级别", "代码"),
            sorting_enabled=False,
            tone_columns=(1,),
            palette=self._palette,
        )
        detail = QTabWidget()
        detail.setDocumentMode(True)
        detail.addTab(self.review_history_table, "评审历史")
        detail.addTab(self.review_gate_table, "硬门明细")
        self.tabs.addTab(
            self._panel("独立评审", detail, summary=self.review_summary),
            "独立评审",
        )

    def render(self, view: TargetedEvidenceView) -> None:
        self.replay_table.render_rows(view.replay_rows)
        self.robustness_summary.setText(view.robustness_summary)
        self.robustness_table.render_rows(
            view.robustness_rows,
            selected_key=view.selected_robustness_run_id,
        )
        self.scenario_table.render_rows(view.robustness_scenario_rows)
        self.walk_forward_summary.setText(view.walk_forward_summary)
        self.walk_forward_table.render_rows(view.walk_forward_rows)
        self.overfit_summary.setText(view.overfit_summary)
        self.overfit_table.render_rows(view.overfit_rows)
        self.quality_summary.setText(view.quality_summary)
        self.quality_table.render_rows(view.quality_rows)
        self.stress_summary.setText(view.stress_summary)
        self.stress_table.render_rows(view.stress_rows)
        self.review_summary.setText(view.review_summary)
        self.review_history_table.render_rows(
            view.review_history_rows,
            selected_key=view.selected_review_run_id,
        )
        self.review_gate_table.render_rows(view.review_gate_rows)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        for table in (
            self.replay_table, self.robustness_table, self.scenario_table,
            self.walk_forward_table, self.overfit_table, self.quality_table,
            self.stress_table, self.review_history_table, self.review_gate_table,
        ):
            table.set_palette(palette)


__all__ = ["TargetedEvidencePanel"]
