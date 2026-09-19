"""Desktop UI v2 strategy page: the native strategy governance route.

This page replaced ``MainWindow._strategy_manager_tab`` outright, together
with the six handlers that used to read its widgets.  There is no legacy
builder behind it, which is the second time that has been true of a v2 route.

Three boundaries are load-bearing:

* **it renders, it does not decide.**  It has no application service, no
  repository, no ``sqlite3`` and no execution import.  The window hands it a
  tuple of :class:`StrategyVersion` and it draws them.  Governance rules --
  which transition is legal, whether the gate has passed, whether a clone is
  allowed -- are not re-implemented here; the page only decides which button
  to grey out, and the application decides what actually happens.
* **it reports intent through signals.**  ``version_selected``,
  ``clone_requested`` and ``transition_requested`` are the whole API back to
  the window.  A page that could call a service would be an orchestrator.
* **showing a version is not running it.**  Selecting a row means "this is the
  version I am looking at".  It must never repoint the auto-rotation or
  targeted-shadow runtime; that is the runtime selection service's job, and
  only the combos on those pages talk to it.

The JSON editor is a text box and nothing more.  Invalid JSON is not rejected
here -- it is refused by the application, so the rule lives in one place.  A
locally empty semver is the one thing this page will not send, because there
is no meaningful version to name.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_widgets import MetricCard
from us_quant.trading.domain.strategy import (
    StrategyStatus,
    StrategyVersion,
)
from us_quant.trading.domain.strategy_parameters import (
    strategy_schema_summary,
)
from us_quant.ui_theme import ThemePalette, theme_palette

#: Placeholder for a value that is not applicable.
MISSING = "—"

#: How each governance status is displayed.  Kept here because it is display
#: text, not domain vocabulary.
STATUS_LABELS: dict[str, str] = {
    StrategyStatus.RESEARCH.value: "研究",
    StrategyStatus.PAPER_SHADOW.value: "Paper影子",
    StrategyStatus.PAUSED.value: "暂停",
    StrategyStatus.STOPPED.value: "停止",
    StrategyStatus.LEGACY_INVALIDATED.value: "旧结果已失效",
}

VERSION_COLUMNS = (
    "策略 ID",
    "名称",
    "版本",
    "状态",
    "模式",
    "风险预算",
    "参数Hash",
    "股票池Hash",
    "研究门",
    "更新时间",
    "说明",
)

#: Statuses a version may be moved *from* by each governance button.  The
#: application is the authority; this only decides what looks clickable.
SHADOW_REQUEST_STATUSES = frozenset(
    {StrategyStatus.RESEARCH, StrategyStatus.PAUSED}
)
PAUSABLE_STATUSES = frozenset({StrategyStatus.PAPER_SHADOW})
STOPPABLE_STATUSES = frozenset(
    {
        StrategyStatus.RESEARCH,
        StrategyStatus.PAPER_SHADOW,
        StrategyStatus.PAUSED,
    }
)


def status_label(status: StrategyStatus) -> str:
    """The display text for one status."""

    return STATUS_LABELS.get(status.value, status.value)


def strategy_option_label(version: StrategyVersion) -> str:
    """The label a runtime-selection combo shows for ``version``.

    Lives here rather than in the window so the combo text and the page text
    cannot drift; the window imports this.
    """

    return (
        f"{version.name} · {version.semver} · "
        f"{status_label(version.status)}"
    )


class StrategyPage(QWidget):
    """Renders the strategy catalogue and reports governance intent."""

    version_selected = Signal(str)
    clone_requested = Signal(str, str, str)
    transition_requested = Signal(str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("strategyPage")
        self._versions: tuple[StrategyVersion, ...] = ()
        self._palette = palette or theme_palette("light")
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        cards = QHBoxLayout()
        self.total_card = MetricCard("策略版本", "0", "不可变版本")
        self.research_card = MetricCard("研究中", "0", "尚未通过晋级门")
        self.shadow_card = MetricCard("Paper Shadow", "0", "仅观察，不下单")
        self.blocked_card = MetricCard("已失效", "0", "保留审计，不可恢复")
        for card in (
            self.total_card,
            self.research_card,
            self.shadow_card,
            self.blocked_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.version_table = QTableWidget(0, len(VERSION_COLUMNS))
        self.version_table.setHorizontalHeaderLabels(list(VERSION_COLUMNS))
        self._configure_table(self.version_table)
        self.version_table.itemSelectionChanged.connect(
            self._selection_changed
        )
        layout.addWidget(self.version_table, 2)

        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.addWidget(self._build_editor_panel())
        lower.addWidget(self._build_governance_panel())
        lower.setSizes([690, 690])
        layout.addWidget(lower, 2)

    def _build_editor_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("参数版本编辑器")
        title.setObjectName("sectionTitle")
        version_row = QHBoxLayout()
        version_label = QLabel("新版本号")
        self.semver_input = QLineEdit()
        self.semver_input.setPlaceholderText("例如 2.0.1-research")
        version_row.addWidget(version_label)
        version_row.addWidget(self.semver_input)
        self.parameter_editor = QTextEdit()
        self.parameter_editor.setPlaceholderText(
            "选择策略后显示 JSON 参数；保存会创建新版本，不会覆盖旧版"
        )
        self.clone_button = QPushButton("从当前参数创建新版本")
        self.clone_button.clicked.connect(self._clone_requested)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("subtitle")
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(False)
        layout.addWidget(title)
        layout.addLayout(version_row)
        layout.addWidget(self.parameter_editor)
        layout.addWidget(self.clone_button)
        layout.addWidget(self.hint_label)
        return panel

    def _build_governance_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("生命周期与晋级门")
        title.setObjectName("sectionTitle")
        self.governance_text = QTextEdit()
        self.governance_text.setReadOnly(True)
        self.governance_text.setPlainText(
            "选择策略查看研究门、版本哈希与安全状态。"
        )
        buttons = QHBoxLayout()
        self.shadow_button = QPushButton("申请进入 Paper Shadow")
        self.shadow_button.clicked.connect(
            lambda: self._request_transition(StrategyStatus.PAPER_SHADOW)
        )
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(
            lambda: self._request_transition(StrategyStatus.PAUSED)
        )
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(
            lambda: self._request_transition(StrategyStatus.STOPPED)
        )
        for button in (
            self.shadow_button,
            self.pause_button,
            self.stop_button,
        ):
            buttons.addWidget(button)
        layout.addWidget(title)
        layout.addWidget(self.governance_text)
        layout.addLayout(buttons)
        return panel

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

    # -- rendering ------------------------------------------------------

    def render(self, versions: Sequence[StrategyVersion]) -> None:
        """Draw ``versions``, keeping the operator's current row if it survives."""

        self._versions = tuple(versions)
        self._render_cards()
        self._render_rows()

    def _render_cards(self) -> None:
        counts = Counter(version.status for version in self._versions)
        visible = sum(
            version.status is not StrategyStatus.LEGACY_INVALIDATED
            for version in self._versions
        )
        self.total_card.set_value(
            str(visible), "每次参数变化生成新版本"
        )
        self.research_card.set_value(
            str(counts[StrategyStatus.RESEARCH]), "仅离线评估"
        )
        self.shadow_card.set_value(
            str(counts[StrategyStatus.PAPER_SHADOW]), "无订单提交能力"
        )
        self.blocked_card.set_value(
            str(counts[StrategyStatus.LEGACY_INVALIDATED]), "永久只读审计"
        )

    def _render_rows(self) -> None:
        # Retired versions are counted above but not listed: they are audit
        # records, not candidates.  ``LEGACY_INVALIDATED`` can never be
        # selected, cloned or transitioned, so showing them as rows would only
        # invite a click that always fails.
        rows = [
            version
            for version in self._versions
            if version.status is not StrategyStatus.LEGACY_INVALIDATED
        ]
        previous = self.selected_version_id()
        # Signals are blocked for the whole refill, and the detail panel is
        # repainted once at the end.  A selection *index* can survive a refill
        # while the row underneath it changes, and Qt emits nothing for that --
        # so refreshing only on a selection-changed signal would leave the
        # editor and the button states describing a version no longer shown.
        self.version_table.blockSignals(True)
        try:
            self.version_table.setSortingEnabled(False)
            self.version_table.setRowCount(len(rows))
            for index, version in enumerate(rows):
                for column, value in enumerate(self._row_values(version)):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 0:
                        item.setData(
                            Qt.ItemDataRole.UserRole, version.version_id
                        )
                    if not version.gate_passed and column in {0, 3, 8}:
                        # A blocked gate is what an operator must not miss.
                        item.setForeground(QColor(self._palette.warning))
                    self.version_table.setItem(index, column, item)
            self.version_table.setSortingEnabled(True)
            target = previous or (rows[0].version_id if rows else None)
            if target is None:
                self.version_table.clearSelection()
            else:
                self._apply_selection(target)
        finally:
            self.version_table.blockSignals(False)

        self._refresh_detail()
        current = self.selected_version_id()
        if current:
            self.version_selected.emit(current)

    @staticmethod
    def _row_values(version: StrategyVersion) -> tuple[str, ...]:
        return (
            version.strategy_id,
            version.name,
            version.semver,
            status_label(version.status),
            str(version.mode),
            f"{version.risk_budget_pct:.1%}",
            version.parameter_hash[:12],
            version.universe_hash[:18],
            "通过" if version.gate_passed else "阻断",
            version.updated_at.isoformat(),
            version.description,
        )

    def _apply_selection(self, version_id: str) -> None:
        """Select the row for ``version_id``, else the first row.

        Falling back to the first row rather than leaving nothing selected
        keeps the page usable when the previous row is no longer listed; all
        four actions are disabled otherwise, with nothing to explain why.
        """

        for index in range(self.version_table.rowCount()):
            item = self.version_table.item(index, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == (
                version_id
            ):
                self.version_table.selectRow(index)
                return
        if self.version_table.rowCount():
            self.version_table.selectRow(0)
        else:
            self.version_table.clearSelection()

    # -- queries --------------------------------------------------------

    def versions(self) -> tuple[StrategyVersion, ...]:
        """The catalogue currently on screen."""

        return self._versions

    def selected_version_id(self) -> str | None:
        """The ``version_id`` of the selected row, or ``None``."""

        items = self.version_table.selectedItems()
        if not items:
            return None
        item = self.version_table.item(items[0].row(), 0)
        if item is None:
            return None
        version_id = item.data(Qt.ItemDataRole.UserRole)
        return str(version_id) if version_id else None

    def selected_version(self) -> StrategyVersion | None:
        """The selected version, or ``None`` when nothing is selected."""

        version_id = self.selected_version_id()
        if version_id is None:
            return None
        for version in self._versions:
            if version.version_id == version_id:
                return version
        return None

    # -- theme ----------------------------------------------------------

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette and repaint the affected colours."""

        self._palette = palette
        if self._versions:
            self._render_rows()

    # -- interactions ---------------------------------------------------

    def _selection_changed(self) -> None:
        self._refresh_detail()
        version_id = self.selected_version_id()
        if version_id:
            self.version_selected.emit(version_id)

    def _refresh_detail(self) -> None:
        version = self.selected_version()
        if version is None:
            self.parameter_editor.setPlainText("")
            self.semver_input.setText("")
            self.governance_text.setPlainText(
                "选择策略查看研究门、版本哈希与安全状态。"
            )
            for button in (
                self.clone_button,
                self.shadow_button,
                self.pause_button,
                self.stop_button,
            ):
                button.setEnabled(False)
            return
        self.parameter_editor.setPlainText(
            json.dumps(
                version.parameters,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        self.semver_input.setText(f"{version.semver}.next")
        self.governance_text.setPlainText(_governance_text(version))
        self.clone_button.setEnabled(
            version.status is not StrategyStatus.LEGACY_INVALIDATED
        )
        self.shadow_button.setEnabled(
            version.gate_passed
            and version.status in SHADOW_REQUEST_STATUSES
        )
        self.pause_button.setEnabled(
            version.status in PAUSABLE_STATUSES
        )
        self.stop_button.setEnabled(
            version.status in STOPPABLE_STATUSES
        )

    def _clone_requested(self) -> None:
        """Ask the window to clone, or say locally why we cannot."""

        version = self.selected_version()
        if version is None:
            self._show_hint("请先选择一个策略版本")
            return
        semver = self.semver_input.text().strip()
        if not semver:
            self._show_hint("请输入新版本号")
            return
        self._show_hint("")
        # The parameters are handed over as text: whether they are valid is
        # the application's answer, not this widget's.
        self.clone_requested.emit(
            version.version_id, semver, self.parameter_editor.toPlainText()
        )

    def _request_transition(self, target: StrategyStatus) -> None:
        version = self.selected_version()
        if version is None:
            return
        self.transition_requested.emit(version.version_id, target.value)

    def _show_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))


def _governance_text(version: StrategyVersion) -> str:
    """The governance detail block, carried over from the retired page.

    Semantics are unchanged: hashes, risk budget, parameter constraints, the
    gate verdict and the standing "no order surface" statement.
    """

    return (
        f"策略：{version.name}\n"
        f"Strategy ID：{version.strategy_id}\n"
        f"Version ID：{version.version_id}\n"
        f"状态 / 模式：{version.status} / {version.mode}\n"
        f"参数 Hash：{version.parameter_hash}\n"
        f"股票池 Hash：{version.universe_hash}\n"
        f"代码 Hash：{version.code_hash}\n"
        f"风险预算：{version.risk_budget_pct:.1%}\n"
        f"参数约束：{strategy_schema_summary(version.strategy_id)}\n\n"
        f"晋级门：{'通过' if version.gate_passed else '阻断'}\n"
        f"原因：{version.gate_reason}\n\n"
        "自动下单：关闭；本管理器没有订单提交接口。"
    )


__all__ = [
    "MISSING",
    "STATUS_LABELS",
    "VERSION_COLUMNS",
    "StrategyPage",
    "status_label",
    "strategy_option_label",
]
