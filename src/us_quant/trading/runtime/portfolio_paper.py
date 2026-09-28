"""Paper lifecycle engine whose only decision route is PortfolioRuntime."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.orders import ExecutionFill, OrderEvent
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan
from us_quant.trading.runtime.artifacts import AutoQuantSnapshot, build_snapshot
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.paper_contracts import EngineSnapshot, PaperEngine
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.trading.runtime.session import SessionState
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime


PortfolioCycle = Callable[[MarketSnapshot, datetime, bool, bool], object]
NEW_YORK = ZoneInfo("America/New_York")


class PortfolioPaperEngine:
    """Own one Paper session/book and delegate every market decision to a portfolio cycle."""

    def __init__(
        self,
        *,
        config: TradingSessionConfig,
        candidate_count: int,
        book: SessionBook,
        session: SessionState,
        portfolio_runtime: PortfolioRuntime,
        launch_plan: PortfolioOperatingPlan,
        portfolio_cycle: PortfolioCycle,
    ) -> None:
        if not callable(portfolio_cycle):
            raise TypeError("portfolio_cycle must be callable")
        if not isinstance(portfolio_runtime, PortfolioRuntime):
            raise TypeError("portfolio_runtime must be the composed PortfolioRuntime")
        if not isinstance(launch_plan, PortfolioOperatingPlan):
            raise TypeError("launch_plan must be the frozen PortfolioOperatingPlan")
        if not isinstance(book, SessionBook) or not isinstance(session, SessionState):
            raise TypeError("PortfolioPaperEngine requires its unique Paper book and session")
        if type(candidate_count) is not int or candidate_count <= 0:
            raise ValueError("candidate_count must be positive")
        if not session.active or session.session_id is None:
            raise ValueError("PortfolioPaperEngine requires a started Paper session")
        self.config = config
        self.candidate_count = candidate_count
        self.book = book
        self.session = session
        self.portfolio_runtime = portfolio_runtime
        self.launch_plan = launch_plan
        self._portfolio_cycle = portfolio_cycle

    def snapshot(self, *, observed_at: datetime | None = None) -> AutoQuantSnapshot:
        return build_snapshot(
            session=self.session,
            book=self.book,
            config=self.config,
            identity=None,
            candidate_count=self.candidate_count,
            observed_at=_utc(observed_at),
        )

    def on_stream(
        self,
        snapshot: object,
        *,
        observed_at: datetime | None = None,
    ) -> EngineSnapshot:
        if not isinstance(snapshot, MarketSnapshot):
            return self._halt("portfolio Paper received an invalid market snapshot")
        now = _utc(observed_at)
        if not self.session.active:
            return self.snapshot(observed_at=now)
        if now.astimezone(NEW_YORK).time().replace(tzinfo=None) >= self.config.force_flat:
            self.session.request_stop()
        for quote in snapshot.quotes:
            if quote.realtime_ready and quote.bid is not None and quote.ask is not None:
                self.book.mark(quote.symbol, (quote.bid + quote.ask) / 2)
        self.book.update_high_water()
        self.book.update_peak_equity()
        try:
            result = self._portfolio_cycle(
                snapshot,
                now,
                not self.session.entries_paused,
                self.session.stop_requested,
            )
        except Exception as error:  # noqa: BLE001 - a broken portfolio path halts Paper
            return self._halt(f"portfolio cycle failed closed: {error}")
        if _cycle_halted(result):
            return self._halt("portfolio dispatch requires Paper reconciliation")
        self.finish_portfolio_stop_if_flat()
        return self.snapshot(observed_at=now)

    def on_execution(self, execution: ExecutionFill) -> EngineSnapshot:
        result = self.book.apply_fill(
            execution,
            intent=self.book.intent(execution.order_id),
        )
        if result.closed:
            self.session.trades_today += 1
        if result.halt:
            self.session.halt(result.status)
        elif result.status:
            self.session.status = result.status
        return self.snapshot()

    def on_order_event(self, event: OrderEvent) -> EngineSnapshot:
        result = self.book.apply_order_event(
            event,
            stopping=self.session.stop_requested,
        )
        if result.halt:
            self.session.halt(result.status)
        elif result.status:
            self.session.status = result.status
        return self.snapshot()

    def pause_entries(self) -> AutoQuantSnapshot:
        if self.session.active and not self.session.stop_requested:
            self.session.pause_entries()
        return self.snapshot()

    def resume_entries(self) -> AutoQuantSnapshot:
        if self.session.active and not self.session.stop_requested:
            self.session.resume_entries()
        return self.snapshot()

    def request_stop(self) -> AutoQuantSnapshot:
        if self.session.active:
            self.session.request_stop()
        self.finish_portfolio_stop_if_flat()
        return self.snapshot()

    def halt_for_reconciliation(self, reason: str) -> AutoQuantSnapshot:
        self.session.halt(reason)
        return self.snapshot()

    def resume_from_reconciliation(
        self, *, session_id: str, allow_force_flat_exit: bool = True
    ) -> AutoQuantSnapshot:
        if self.session.session_id != session_id:
            raise ValueError("会话 ID 不匹配，不允许恢复其他会话")
        if self.session.active:
            return self.snapshot()
        self.session.resume_after_reconciliation(
            session_id=session_id,
            keeping_positions=allow_force_flat_exit and bool(self.book.positions),
        )
        return self.snapshot()

    def finish_portfolio_stop_if_flat(self) -> AutoQuantSnapshot:
        if (
            self.session.stop_requested
            and not self.book.positions
            and not self.book.pending
        ):
            self.session.finish(
                "Portfolio Paper 已平仓；等待 broker execution report 完成对账"
            )
        return self.snapshot()

    def _halt(self, reason: str) -> AutoQuantSnapshot:
        self.session.halt(reason)
        return self.snapshot()


def _cycle_halted(result: object) -> bool:
    actions = getattr(result, "actions", ())
    return any(
        bool(getattr(dispatch, "halt", False))
        for action in actions
        if (dispatch := getattr(action, "dispatch", None)) is not None
    )


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("Portfolio Paper observation time must be timezone-aware")
    return observed.astimezone(timezone.utc)


__all__ = ["PortfolioPaperEngine", "PortfolioCycle"]
