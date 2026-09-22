"""Account orchestration: the one owner of the desktop account capability.

Before this module the account route's desktop truth was spread across
``MainWindow``: the last portfolio, the refresh task, the ledger append, the
page render and the shell badges were all touched from a handful of handlers.
This class now owns them.

The boundary it draws is the point of the extraction, and it has one rule that
matters more than the rest:

* **there is no second account truth.**  ``BrokerAccountApplication`` already
  owns the config, the last good portfolio, ``last_error`` and the refresh
  lifecycle; that is the application-layer owner and it stays that way.  This
  class stores *nothing* about the account -- :attr:`portfolio` delegates, and
  a guard fails on any ``self._portfolio`` here.  A copy would be a second
  owner, and the two copies would disagree the first time one path forgot to
  update the other -- in an account surface, that is a safety bug rather than
  a cosmetic one.

What it deliberately does not own:

* **the generic task lifecycle.**  ``TaskThread``, the controller, the worker
  list, the closing admission gate, the busy dialog and the cancellation
  handling stay on the window.  This class receives one narrow callable,
  ``submit_task``, and knows none of that;
* **the shell.**  The badges are published as an ``AccountShellHealthView``
  fact and painted by the window, and runtime events are *requested* rather
  than written, so ``Account -> System`` never becomes a dependency;
* **cross-workflow fan-out.**  Whether the dashboard, the auto-quant preflight
  or the targeted preflight react to a new account is not an account decision:
  the window subscribes to :attr:`portfolio_changed` and routes it;
* **the risk/strategy configuration, and the research scenario figure.**  The
  exposure multiplier shown next to a position is a *presentation* input pushed
  in by the composition root, and so is the research scenario capital the page
  displays beside the broker numbers.  Both are pushed rather than fetched, so
  this module never imports Risk, Strategy, the app config, or the capability
  that edits the research scalar.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Signal

from us_quant.account_ledger import AccountLedger
from us_quant.desktop_v2.orchestration.account.models import (
    AccountPresentationInputs,
    AccountRuntimeEvent,
    AccountShellHealthView,
)
from us_quant.desktop_v2.orchestration.account.queries import (
    fresh_paper_net_liquidation,
)
from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter
from us_quant.trading.application.accounts import BrokerAccountApplication
from us_quant.trading.domain.account import BrokerAccountPortfolio

#: How long the read-only handshake may take before it is abandoned.
REFRESH_TIMEOUT_SECONDS = 20

#: How many ledger points the page draws.
LEDGER_POINT_LIMIT = 100

#: The message the background task reports while it is running.
REFRESH_PROGRESS_MESSAGE = "正在进行 IBKR 只读握手并读取账户、持仓和 P&L…"

#: The status line written when the task is admitted.
REFRESH_START_MESSAGE = "IBKR 只读账户刷新开始…"

#: The resource group that serializes broker work; a second concurrent read
#: would open another client-id socket and race the first to publish truth.
REFRESH_RESOURCE_GROUP = "broker"


class AccountOrchestrator(QObject):
    """Owns the desktop account capability and renders the account page."""

    #: A refresh succeeded and the account truth is published.  The window
    #: fans this out to whatever else consumes account facts (dashboard,
    #: auto-quant preflight and runtime view, targeted preflight).
    portfolio_changed = Signal(object)

    #: The global header facts changed.  The shell badges are not ours.
    shell_health_changed = Signal(object)

    #: One runtime event the window should record.
    runtime_event_requested = Signal(object)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        application: BrokerAccountApplication,
        ledger: AccountLedger,
        page: object,
        submit_task: TaskSubmitter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._ledger = ledger
        self._page = page
        self._submit_task = submit_task
        self._inputs = AccountPresentationInputs()

    # -- read-only facts the rest of the desktop may consume --------------

    @property
    def portfolio(self) -> BrokerAccountPortfolio | None:
        """The canonical account truth, read through its owner.

        A delegation, not a copy: ``BrokerAccountApplication`` is where the
        portfolio lives, and this property exists so callers can reach it
        without holding the application.  The identity is load-bearing --
        ``orchestrator.portfolio is application.portfolio`` -- because a
        mirrored copy would be a second truth.
        """

        return self._application.portfolio

    def fresh_paper_net_liquidation(
        self, *, now: datetime | None = None
    ) -> Decimal | None:
        """A usable fresh Paper net liquidation, or ``None``.

        The rule itself is a Qt-free function in :mod:`queries`; this is the
        capability-level entry point so Paper and Shadow sizing never has to
        reach for the application or repeat the freshness check.
        """

        return fresh_paper_net_liquidation(self.portfolio, now=now)

    # -- inputs the composition root pushes in ---------------------------

    def set_presentation_inputs(
        self, inputs: AccountPresentationInputs
    ) -> None:
        """Hand over the finished presentation facts the page draws with.

        The configured exposure multipliers are a *risk* fact, so this class
        must not go looking for them: that would make the account route depend
        on ``RiskApplication`` and the app config.  The composition root knows
        where they come from and pushes the frozen result.

        The research scenario capital arrives the same way, and for the same
        reason: the capability that edits it is the Cross Section workspace, and
        the account route must not learn that it exists.
        """

        self._inputs = inputs

    # -- lifecycle -------------------------------------------------------

    def request_refresh(self) -> None:
        """Read broker account truth on the shared background task boundary.

        This class owns *what* the refresh is -- a read-only IBKR handshake and
        a portfolio -- and nothing about how a background task is admitted,
        tracked or torn down.  That is what ``submit_task`` is for.
        """

        def task(
            progress: Callable[[str], None],
        ) -> BrokerAccountPortfolio:
            progress(REFRESH_PROGRESS_MESSAGE)
            return self._application.refresh(
                timeout_seconds=REFRESH_TIMEOUT_SECONDS
            )

        self._submit_task(
            task,
            on_success=self._refresh_succeeded,
            start_message=REFRESH_START_MESSAGE,
            resource_group=REFRESH_RESOURCE_GROUP,
        )

    # -- rendering -------------------------------------------------------

    def render_current(self) -> None:
        """Draw the account page from the canonical portfolio and the ledger.

        It never fetches: the portfolio is whatever the application last
        committed, and the ledger points are read for the account that
        portfolio describes.  A render that could start a read would be a
        second refresh path.
        """

        portfolio = self._application.portfolio
        points: tuple = ()
        if portfolio is not None:
            points = self._ledger.list_points(
                environment=portfolio.account.environment.value,
                account_alias=portfolio.account.account_alias,
                limit=LEDGER_POINT_LIMIT,
            )
        self._page.render(
            portfolio,
            ledger_points=points,
            exposure_multipliers=self._inputs.as_mapping(),
            research_capital=self._inputs.research_capital,
        )

    # -- success path ----------------------------------------------------

    def _refresh_succeeded(self, result: object) -> None:
        """Publish one successful read: ledger, page, header, event, log.

        The portfolio is *not* stored here.  ``BrokerAccountApplication``
        committed it before this handler ran, so the canonical truth is already
        in place and copying it would create the second owner this extraction
        exists to prevent.
        """

        if not isinstance(result, BrokerAccountPortfolio):
            raise TypeError("unexpected broker account portfolio")
        account = result.account
        self._ledger.append(account)
        self.runtime_event_requested.emit(
            AccountRuntimeEvent(
                severity="info",
                component="account",
                code="SNAPSHOT_OK",
                message=(
                    f"{account.account_alias} 只读快照完成；"
                    f"{len(result.positions)} 个持仓"
                ),
            )
        )
        self.render_current()
        self.portfolio_changed.emit(result)
        self.shell_health_changed.emit(self._health_view(account))
        self.log_requested.emit(
            f"已读取 {account.account_alias}："
            f"{len(result.positions)} 个持仓"
        )

    @staticmethod
    def _health_view(account) -> AccountShellHealthView:
        """The header facts one successful account read produces.

        A read is a one-shot connect/read/disconnect, so "已握手" describes the
        *most recent account refresh* rather than a socket that is still open.
        This is also why the account chain never touches the market badge: it
        has no opinion about whether quotes are real-time.
        """

        return AccountShellHealthView(
            handshake_text="协议 · 已握手",
            handshake_state="ok",
            handshake_tooltip="最近一次账户刷新握手成功",
            account_text=f"Paper · {account.account_alias}",
            account_state="ok",
        )


__all__ = ["AccountOrchestrator"]
