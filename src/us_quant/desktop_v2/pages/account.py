"""Desktop UI v2 account page: the first natively-built v2 route.

This page renders broker account truth and nothing else.  It replaced
``MainWindow._account_tab`` (the page builder) and
``MainWindow._populate_account_view`` (the render method) outright; there is
no legacy builder left behind it, which is the first time that has been true
of a v2 route.

Two boundaries are load-bearing:

* **it renders, it does not fetch.**  It has no application service, no
  adapter and no ``ibapi``; ``AccountOrchestrator`` owns the refresh and hands
  this page an already-built :class:`BrokerAccountPortfolio`.  A page that
  could start a read would be an orchestrator again.
* **it shows no market-data state.**  There is no quote type, no mark source
  and no stale/fresh column, because the account path does not request
  market data at all.  Whether quotes are real-time is owned by Market Data
  v2 and shown on the market page.  The one price-like number here,
  ``Broker Mark``, is the broker's own ``market_value / quantity``.

A missing broker value renders as ``—``, never ``$0``: "the broker did not
report this" must not look like "this is worth nothing".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from us_quant.account_ledger import EquityPoint
from us_quant.desktop_widgets import MetricCard
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)

#: Placeholder for a value the broker did not report.
MISSING = "—"

POSITION_COLUMNS = (
    "代码",
    "数量",
    "均价",
    "Broker Mark",
    "Broker 市值",
    "风险敞口",
    "当日 P&L",
    "未实现 P&L",
    "已实现 P&L",
    "币种",
    "账户",
)

LEDGER_COLUMNS = (
    "时间",
    "环境",
    "账户",
    "净值",
    "现金",
    "当日P&L",
    "未实现P&L",
)


class AccountPage(QWidget):
    """Renders one broker account portfolio plus the equity ledger."""

    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("accountPage")
        self._portfolio: BrokerAccountPortfolio | None = None
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        cards = QHBoxLayout()
        self.net_liquidation_card = MetricCard(
            "IBKR 模拟账户净值", MISSING, "尚未读取"
        )
        self.research_capital_card = MetricCard(
            "历史研究资金情景", "$1,500", "可调整；不是账户真值"
        )
        self.daily_pnl_card = MetricCard(
            "当日盈亏", MISSING, "IBKR reqPnL"
        )
        self.unrealized_pnl_card = MetricCard(
            "未实现盈亏", MISSING, "IBKR reqPnL"
        )
        self.cash_card = MetricCard(
            "现金", MISSING, "券商账户摘要"
        )
        for card in (
            self.net_liquidation_card,
            self.research_capital_card,
            self.daily_pnl_card,
            self.unrealized_pnl_card,
            self.cash_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        self.refresh_button = QPushButton("只读刷新账户 / 持仓 / P&L")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.status_label = QLabel(
            "尚未读取 IBKR；账户号只显示脱敏别名"
        )
        self.status_label.setObjectName("subtitle")
        controls.addWidget(self.refresh_button, 0, 0)
        controls.addWidget(self.status_label, 0, 1)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        # A caller-supplied notice strip.  The page does not know what the
        # notice means -- the window sets strategy-evidence copy here -- which
        # is what keeps strategy state out of this page's imports.
        self.notice_label = QLabel("")
        self.notice_label.setObjectName("emptyState")
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)
        layout.addWidget(self.notice_label)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._build_positions_panel())
        split.addWidget(self._build_ledger_panel())
        split.setSizes([420, 220])
        layout.addWidget(split)

    def _build_positions_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("券商持仓")
        title.setObjectName("sectionTitle")
        self.positions_hint_label = QLabel(
            "尚未读取持仓；读取后若为空，会明确显示“当前账户无持仓”"
        )
        self.positions_hint_label.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(self.positions_hint_label)
        layout.addLayout(header)

        self.positions_table = QTableWidget(0, len(POSITION_COLUMNS))
        self.positions_table.setHorizontalHeaderLabels(
            list(POSITION_COLUMNS)
        )
        self._configure_table(self.positions_table)
        layout.addWidget(self.positions_table)
        return panel

    def _build_ledger_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("账户权益账本（Paper 与 Live 永久隔离）")
        title.setObjectName("sectionTitle")
        self.detail_label = QLabel(
            "净值、现金和三类 P&L 均保留来源与采集时间"
        )
        self.detail_label.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(self.detail_label)
        layout.addLayout(header)

        self.ledger_table = QTableWidget(0, len(LEDGER_COLUMNS))
        self.ledger_table.setHorizontalHeaderLabels(list(LEDGER_COLUMNS))
        self._configure_table(self.ledger_table)
        layout.addWidget(self.ledger_table)
        return panel

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

    # -- rendering ------------------------------------------------------

    def render(
        self,
        portfolio: BrokerAccountPortfolio | None,
        *,
        ledger_points: Sequence[EquityPoint] = (),
        exposure_multipliers: Mapping[str, Decimal] | None = None,
        error: str | None = None,
    ) -> None:
        """Draw ``portfolio``.

        ``None`` means "never read", which is deliberately distinct from an
        empty portfolio: the first leaves the placeholder text in place, the
        second says the account genuinely holds nothing.

        ``exposure_multipliers`` is a *presentation* projection -- the
        configured per-symbol multiplier applied to broker market value for
        display.  It is never written back into the domain.
        """

        self._portfolio = portfolio
        if error:
            self.status_label.setText(f"读取失败：{error}")
        if portfolio is None:
            self._render_empty()
            self._render_ledger(ledger_points)
            return
        self._render_account(portfolio.account)
        self._render_positions(
            portfolio.positions, exposure_multipliers or {}
        )
        self._render_ledger(ledger_points)

    def _render_empty(self) -> None:
        for card in (
            self.net_liquidation_card,
            self.daily_pnl_card,
            self.unrealized_pnl_card,
            self.cash_card,
        ):
            card.set_value(MISSING, "尚未读取")
        self.positions_hint_label.setText(
            "尚未读取持仓；读取后若为空，会明确显示“当前账户无持仓”"
        )
        self.positions_table.setRowCount(0)
        self.detail_label.setText(
            "净值、现金和三类 P&L 均保留来源与采集时间"
        )

    def _render_account(self, account: BrokerAccountSnapshot) -> None:
        self.net_liquidation_card.set_value(
            _money(account.net_liquidation),
            f"IBKR {account.environment.value.title()} · "
            f"{account.account_alias}",
        )
        self.cash_card.set_value(
            _money(account.cash),
            f"可用资金 {_money(account.available_funds)}",
        )
        self.daily_pnl_card.set_value(
            _money(account.daily_pnl, signed=True),
            f"{account.pnl_source} · 不与回测收益混合",
        )
        self.unrealized_pnl_card.set_value(
            _money(account.unrealized_pnl, signed=True),
            f"已实现 {_money(account.realized_pnl, signed=True)}",
        )
        self.status_label.setText(
            f"{account.environment.value.upper()} · "
            f"{account.account_alias} · "
            f"采集 {account.observed_at.isoformat()}"
        )
        self.detail_label.setText(
            f"购买力 {_money(account.buying_power)} · "
            f"总持仓 {_money(account.gross_position_value)} · "
            f"维持保证金 {_money(account.maintenance_margin)}"
        )

    def _render_positions(
        self,
        positions: Sequence[BrokerPositionSnapshot],
        multipliers: Mapping[str, Decimal],
    ) -> None:
        self.positions_hint_label.setText(
            "当前账户无持仓"
            if not positions
            else f"共 {len(positions)} 个券商持仓"
        )
        self.positions_table.setSortingEnabled(False)
        self.positions_table.setRowCount(len(positions))
        for index, position in enumerate(positions):
            multiplier = multipliers.get(
                position.symbol, Decimal("1")
            )
            exposure = (
                position.market_value * multiplier
                if position.market_value is not None
                else None
            )
            values = (
                position.symbol,
                str(position.quantity),
                _money(position.average_cost),
                _money(position.mark),
                _money(position.market_value),
                _money(exposure),
                _money(position.daily_pnl, signed=True),
                _money(position.unrealized_pnl, signed=True),
                _money(position.realized_pnl, signed=True),
                position.currency,
                position.account_alias,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if position.market_value is None and column in {3, 4, 5}:
                    # The broker did not value this position.  Colour it so
                    # an absent number is not read as a zero.
                    item.setForeground(QColor("#c0392b"))
                self.positions_table.setItem(index, column, item)
        self.positions_table.setSortingEnabled(True)

    def _render_ledger(
        self, points: Sequence[EquityPoint]
    ) -> None:
        self.ledger_table.setSortingEnabled(False)
        self.ledger_table.setRowCount(len(points))
        for index, point in enumerate(points):
            values = (
                point.observed_at,
                point.environment,
                point.account_alias,
                _money(point.net_liquidation),
                _money(point.cash),
                _money(point.daily_pnl, signed=True),
                _money(point.unrealized_pnl, signed=True),
            )
            for column, value in enumerate(values):
                self.ledger_table.setItem(
                    index, column, QTableWidgetItem(value)
                )
        self.ledger_table.setSortingEnabled(True)

    # -- queries --------------------------------------------------------

    def set_notice(self, text: str) -> None:
        """Show or clear the caller-supplied notice strip."""

        self.notice_label.setText(text)
        self.notice_label.setVisible(bool(text))

    def set_research_capital(self, value: str, note: str) -> None:
        """Write the research-capital card.

        Research capital is *not* account truth -- it is a historical research
        scenario the operator can edit, shown on this route only because that
        is where an account-shaped number belongs visually.  It reaches the
        page through a named method rather than a widget attribute so the
        window cannot reach through to ``research_capital_card`` and so the
        card's formatting stays this page's business.

        The window still owns the scalar and still decides when it changed;
        which cross-workflow owner finally publishes it is v2O-C Research's
        question, not this round's.
        """

        self.research_capital_card.set_value(value, note)

    @property
    def portfolio(self) -> BrokerAccountPortfolio | None:
        """The portfolio currently on screen, or ``None`` before a read."""

        return self._portfolio


def _money(value: Decimal | None, *, signed: bool = False) -> str:
    """Format a broker amount, or the missing placeholder.

    A missing value is ``—`` and never ``$0``: the two mean different
    things and the operator must be able to tell them apart.
    """

    if value is None:
        return MISSING
    number = float(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}${number:,.2f}"


__all__ = ["AccountPage"]
