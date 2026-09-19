"""Desktop UI v2 risk page: the native risk route.

This page replaced ``MainWindow._safety_tab``, which was a static block of
text.  The safety prose it carried is preserved verbatim -- an operator who
learned the boundaries from that page must still find them -- and the values
the risk layer actually enforces are now shown alongside it.

Three boundaries are load-bearing:

* **it renders, it does not enforce.**  It holds no ``RiskApplication``, no
  runtime, no store and no broker import.  The window hands it a
  :class:`RiskLimits` and it draws the numbers.  A page that could evaluate a
  proposal would be a second risk authority, which is the arrangement this
  migration exists to end.
* **it cannot change a limit.**  There is no edit control, no override button
  and no "temporarily relax" action.  Risk parameters come from configuration;
  a UI that could widen a ceiling at runtime would make every limit advisory.
* **it reports the boundary, not just the numbers.**  What risk decides and
  what only execution may do are printed here, because the operator's mental
  model is what the split depends on.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_widgets import MetricCard
from us_quant.trading.domain.risk import RiskLimits
from us_quant.ui_theme import ThemePalette, theme_palette

#: Placeholder for a limit the page has not been given yet.
MISSING = "—"

#: The non-bypassable first-phase boundaries, carried over verbatim from the
#: retired ``MainWindow._safety_tab``.  Do not reword these: they are the copy
#: the operator was told to rely on, and a rephrasing is indistinguishable
#: from a relaxation.
SAFETY_ITEMS: tuple[str, ...] = (
    "1. IBKR Paper 模拟下单能力默认关闭。设置开关本身不会下单；"
    "只有“自动量化”页核验唯一 DU 账户、实时行情、候选、策略、"
    "金额上限并由用户逐会话确认后，独立适配器才允许发送 DAY 限价单。",
    "2. 券商链路只允许 IB Gateway 模拟端口 4002 和唯一 DU "
    "账户；Live 端口/账户硬阻断。行情链路可独立使用 Finnhub、"
    "Alpaca 或 IBKR Market Data。",
    "3. 不做碎股；所有资金可买数量均向下取整为整股。",
    "4. 中国概念股不进入研究池或交易池。日本、欧洲、加拿大、"
    "拉美等非中国发行人可研究；ADR 或 20-F 本身不构成排除理由。",
    "5. 龙头和优质二线分层独立于策略分数。后排股票即使技术"
    "指标得分高，也只能作为广域研究样本。",
    "6. 杠杆或替代执行品按通用风险倍数和最长持有期管理，"
    "不会被当作独立 Alpha 策略或重点标的。",
    "7. 历史收益不保证未来盈利；大模型不会被放进实时下单链路。"
    "策略优化采用可复现参数、走样本外验证和成本压力测试。",
)

#: What the risk layer decides, and what it is structurally unable to do.
#: Shown because the two halves are only meaningful together.
RISK_BOUNDARY: tuple[str, ...] = (
    "风控层可以决定：是否允许这笔提案；允许多少整股；"
    "以及拒绝或缩量的原因。",
    "风控层不能：创建订单 ID、创建券商订单、提交、撤单或对账。",
    "所有买入与卖出提案都先经过风控层；风险熔断只阻止加仓，"
    "绝不阻止合法减仓。",
    "风控参数来自本地配置，本页只读；不提供临时放宽或覆盖入口。",
)


def percent(value: Decimal) -> str:
    """Render a ``(0, 1]`` ratio as a percentage."""

    return f"{value:.1%}"


class RiskPage(QWidget):
    """Renders the configured risk limits and the standing safety boundaries."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("riskPage")
        self._palette = palette or theme_palette("light")
        self._limits: RiskLimits | None = None
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        cards = QHBoxLayout()
        self.gross_card = MetricCard(
            "总风险敞口上限", MISSING, "占账户净值的比例"
        )
        self.position_card = MetricCard(
            "单标的风险上限", MISSING, "单只标的最多占净值比例"
        )
        self.daily_loss_card = MetricCard(
            "单日亏损熔断", MISSING, "达到后禁止新开仓"
        )
        self.drawdown_card = MetricCard(
            "高水位回撤熔断", MISSING, "达到后禁止新开仓"
        )
        self.margin_card = MetricCard(
            "保证金借款", MISSING, "只允许用账户现金买入"
        )
        for card in (
            self.gross_card,
            self.position_card,
            self.daily_loss_card,
            self.drawdown_card,
            self.margin_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.source_label = QLabel(
            "风控参数来自本地配置；本页只读，不提供在线修改入口"
        )
        self.source_label.setObjectName("subtitle")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        layout.addWidget(self._build_boundary_panel(), 1)
        layout.addWidget(self._build_safety_panel(), 3)

    def _build_boundary_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("风控层边界")
        title.setObjectName("sectionTitle")
        self.boundary_text = QTextEdit()
        self.boundary_text.setReadOnly(True)
        self.boundary_text.setPlainText("\n".join(RISK_BOUNDARY))
        layout.addWidget(title)
        layout.addWidget(self.boundary_text)
        return panel

    def _build_safety_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("不可绕过的首期边界")
        title.setObjectName("sectionTitle")
        self.safety_text = QTextEdit()
        self.safety_text.setReadOnly(True)
        self.safety_text.setPlainText("\n\n".join(SAFETY_ITEMS))
        layout.addWidget(title)
        layout.addWidget(self.safety_text)
        return panel

    # -- rendering ------------------------------------------------------

    def render(self, limits: RiskLimits | None) -> None:
        """Draw ``limits``.

        ``None`` means the window has not supplied them yet, which is
        deliberately distinct from a limit of zero: an unset page shows the
        placeholder rather than a confident ``0%``.
        """

        self._limits = limits
        if limits is None:
            for card in (
                self.gross_card,
                self.position_card,
                self.daily_loss_card,
                self.drawdown_card,
            ):
                card.set_value(MISSING, "尚未读取配置")
            self.margin_card.set_value(MISSING, "尚未读取配置")
            return
        self.gross_card.set_value(
            percent(limits.max_gross_exposure_pct),
            f"账户全部持仓合计；净值 × "
            f"{percent(limits.max_gross_exposure_pct)}",
        )
        self.position_card.set_value(
            percent(limits.max_position_exposure_pct),
            "单标的按 数量 × 价格 × 风险倍数 计算",
        )
        self.daily_loss_card.set_value(
            percent(limits.daily_loss_halt_pct),
            "较当日开盘净值；只阻止买入，不阻止减仓",
        )
        self.drawdown_card.set_value(
            percent(limits.drawdown_halt_pct),
            "较高水位净值；只阻止买入，不阻止减仓",
        )
        self.margin_card.set_value(
            "允许" if limits.allow_margin_borrowing else "禁止",
            (
                "买入必须由账户现金全额支付"
                if not limits.allow_margin_borrowing
                else "配置已允许借款；Paper 安全要求应为禁止"
            ),
        )
        self._apply_margin_colour(limits.allow_margin_borrowing)

    def _apply_margin_colour(self, allowed: bool) -> None:
        """Flag an enabled margin flag in the warning colour.

        The Paper posture forbids it, so a page that showed "允许" in the same
        colour as every other value would hide the one setting an operator
        must not miss.
        """

        colour = (
            self._palette.warning if allowed else self._palette.success
        )
        self.margin_card.value_label.setStyleSheet(
            f"color: {colour};"
        )

    # -- queries --------------------------------------------------------

    @property
    def limits(self) -> RiskLimits | None:
        """The limits currently on screen, or ``None`` before a render."""

        return self._limits

    # -- theme ----------------------------------------------------------

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette and repaint the affected colours."""

        self._palette = palette
        if self._limits is not None:
            self._apply_margin_colour(
                self._limits.allow_margin_borrowing
            )


__all__ = [
    "MISSING",
    "RISK_BOUNDARY",
    "SAFETY_ITEMS",
    "RiskPage",
    "percent",
]
