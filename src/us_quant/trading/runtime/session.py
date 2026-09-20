"""The trading session's own state, and every transition it has.

One object holds the eleven facts that describe *where the session is* --
identity, activity, pause/stop flags, trade count, trading day, the minute the
scanner last ran, and the text a human reads -- and the five transitions those
facts are allowed to have.  Keeping them together is what makes "a session that
stopped cannot silently start trading again" checkable: every writer of
``active`` is one of the methods below.

It decides nothing outside itself.  A fill, a broker rejection or a refused
reduction is judged elsewhere and arrives here as a transition the session
runtime chose to take; the rollover check lives with the runtime too, because
it needs the book.  What this file owns is the state machine, not the policy
that drives it, and it never imports the strategy, risk, execution or a broker.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4


class SessionState:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.active = False
        self.trades_today = 0
        self.trading_day: date | None = None
        self.last_evaluation_minute: datetime | None = None
        self.stop_requested = False
        self.entries_paused = False
        self.status = "未启动"

    # -- lifecycle -------------------------------------------------------

    def begin(
        self, *, candidate_count: int, warmup_minutes: int
    ) -> None:
        """Open a session: a fresh identity and nothing carried over."""

        self.session_id = uuid4().hex
        self.active = True
        self.trades_today = 0
        self.trading_day = None
        self.last_evaluation_minute = None
        self.stop_requested = False
        self.entries_paused = False
        self.status = (
            f"已武装；扫描 {candidate_count} 个候选，"
            f"等待 {warmup_minutes} 个连续分钟"
        )

    def request_stop(self) -> None:
        """Stop means flatten first, then finish; entries close immediately."""

        self.stop_requested = True
        self.entries_paused = True
        self.status = "停止请求已登记；先处理在途单和 Paper 持仓"

    def pause_entries(self) -> None:
        """Pausing holds back new positions only; the exit gates keep running."""

        self.entries_paused = True
        self.status = (
            "已暂停新开仓；现有持仓的风控和平仓逻辑继续运行"
        )

    def resume_entries(self) -> None:
        self.entries_paused = False
        self.status = "已恢复新开仓；继续等待 fresh 行情和策略信号"

    def halt(self, reason: str) -> None:
        """Stop for a human.

        A halt is the strongest resting state: not active, entries closed and a
        stop already requested, so nothing can lift it except an explicit
        reconciliation resume.  Production code that finds a disagreement calls
        this rather than deciding for itself how far to wind the session down.
        """

        self.active = False
        self.stop_requested = True
        self.entries_paused = True
        self.status = f"{reason}；已停机，禁止新订单并等待人工对账"

    def resume_after_reconciliation(
        self, *, session_id: str, keeping_positions: bool
    ) -> None:
        """The only way back from a halt, and only for the same session."""

        if self.session_id != session_id:
            raise ValueError("会话 ID 不匹配，不允许恢复其他会话")
        self.stop_requested = False
        self.entries_paused = False
        self.active = True
        self.status = (
            "已恢复对账后运行；保留现有持仓，"
            "继续执行止盈止损和平仓逻辑"
            if keeping_positions
            else "已恢复对账后运行；继续扫描新开仓机会"
        )

    def finish(self, status: str) -> None:
        """The session ended on its own terms rather than by a halt."""

        self.active = False
        self.status = status

    # -- trading day -----------------------------------------------------

    def roll_trading_day(
        self, trading_day: date, *, has_open_work: bool
    ) -> bool:
        """Move to a new trading day, or refuse to.

        Returns ``True`` when the day really rolled.  Open work -- a position
        or a pending order -- blocks the rollover and halts the session
        instead: a book carried across the boundary is a disagreement between
        the session's day and the broker's, and only a human can settle it.
        """

        if self.trading_day is None:
            self.trading_day = trading_day
            return False
        if trading_day == self.trading_day:
            return False
        if has_open_work:
            self.active = False
            self.status = "跨交易日仍有持仓/在途单；已停机等待人工对账"
            return False
        self.trading_day = trading_day
        self.trades_today = 0
        self.last_evaluation_minute = None
        return True