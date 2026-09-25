"""Strategy governance orchestration: the desktop strategy capability's owner.

Before this module the strategy governance route's sequencing lived in
``MainWindow``: the window read the catalogue, rendered the page, decoded the
clone editor's JSON, executed the lifecycle transition, wrote the
``STATUS_CHANGE`` runtime event and explained the evidence gate for the
account notice.  G2-A moves all of that here.

The boundary that matters most:

* **there is no second catalogue truth.**  ``StrategyApplication`` stays the
  governance authority; this class caches nothing.  ``refresh`` reads the
  catalogue, hands it to the page and publishes ``catalog_changed`` -- and a
  guard fails on any ``self._versions`` here, because a second copy of the
  catalogue would disagree with the application the first time a transition
  happened off this route.
* **showing a version is not running it.**  ``select_version`` answers with
  the account-notice text for the version the operator is *looking at*.  This
  class never receives and never calls ``StrategySelectionService``: the
  runtime selections (auto rotation, targeted shadow, backtest) are pointed
  only by the combos that own them, and a governance view selection must
  never repoint any of them.
* **the page is the only page.**  This class is the one orchestration caller
  of ``StrategyPage.render``; the window only constructs and wires.

What it deliberately does not own:

* **cross-capability fan-out.**  Which other routes refill their strategy
  options after a catalogue change is not a strategy decision: the window
  subscribes to :attr:`catalog_changed` and routes it.
* **dialogs and the shell.**  Warnings are *requested* (the window shows
  them), logs are *requested*, and runtime events are requested through the
  generic router, so ``Strategy -> System`` and ``Strategy -> the shell``
  never become dependencies.
* **the account page.**  The finished notice text is published; painting it
  is the account capability's business, reached through the narrow
  ``AccountOrchestrator.set_notice`` the window wires.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.strategy.models import (
    StrategyRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.strategy.queries import (
    parse_clone_parameters,
    strategy_account_notice,
)
from us_quant.trading.application.strategies import (
    StrategyApplication,
    StrategyApplicationError,
    StrategyNotFoundError,
)

#: The reason stamped on every governance transition this route performs.
#: Carried over verbatim from the retired window handler.
GOVERNANCE_TRANSITION_REASON = "desktop governance action"


class StrategyGovernanceOrchestrator(QObject):
    """Owns the desktop strategy governance route and renders its page."""

    #: The catalogue was re-read and the page repainted.  The window fans
    #: this out to the routes that refill their strategy options (backtest,
    #: targeted, and -- until G2-B -- the execution combo).
    catalog_changed = Signal()

    #: The finished account-notice text for the version being viewed.  The
    #: window hands it to the account capability; this class never learns
    #: where it is painted.
    account_notice_requested = Signal(str)

    #: One dialog the window should show (title, message).  The capability
    #: may not import a widget, so it asks.
    warning_requested = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: One runtime event the window should route to its single owner.
    runtime_event_requested = Signal(object)

    def __init__(
        self,
        *,
        application: StrategyApplication,
        page: object,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._page = page

    # -- governance actions ----------------------------------------------

    def refresh(self) -> None:
        """Re-read the catalogue, repaint the page, announce the change.

        The read happens every time: there is no cached catalogue here to go
        stale.  A read failure is logged and nothing else happens -- a fake
        render of a catalogue nobody can read would be worse than a stale
        screen, because the operator would act on rows that are not there.
        """

        try:
            versions = self._application.list_versions()
        except StrategyApplicationError as error:
            self.log_requested.emit(f"策略目录读取失败：{error}")
            return
        self._page.render(versions)
        self.catalog_changed.emit()

    def select_version(self, version_id: str) -> None:
        """Publish the account notice for the version now being viewed.

        A governance *view* selection: it must never touch the runtime
        selection service.  A version that is not in the catalogue (a row
        that vanished between render and click) is a no-op, exactly as the
        retired window handler was.
        """

        if not version_id:
            return
        try:
            version = self._application.get_version(version_id)
        except StrategyNotFoundError:
            return
        self.account_notice_requested.emit(strategy_account_notice(version))

    def clone(
        self,
        version_id: str,
        semver: str,
        parameters_json: str,
    ) -> None:
        """Execute a clone the page asked for, or refuse it visibly.

        The JSON decoding is the adapter this class owns (see
        :func:`parse_clone_parameters`); whether the *parameters* are valid is
        the application's answer.  A refusal never refreshes: repainting the
        catalogue after a refused clone would present a success the operator
        cannot find.
        """

        try:
            parameters = parse_clone_parameters(parameters_json)
            created = self._application.clone_version(
                version_id,
                semver=semver,
                parameters=parameters,
            )
        except json.JSONDecodeError as error:
            self.warning_requested.emit(
                "创建失败", f"参数不是合法 JSON：{error}"
            )
            return
        except (ValueError, StrategyApplicationError) as error:
            self.warning_requested.emit("创建失败", str(error))
            return
        self.refresh()
        self.log_requested.emit(
            f"已创建 {created.strategy_id} {created.semver}；"
            "状态回到研究，需重新验证"
        )

    def transition(
        self,
        version_id: str,
        target_status: str,
    ) -> None:
        """Execute a lifecycle change the page asked for.

        The promotion gate belongs to the application: a blocked transition
        is a warning plus a log line, never a runtime event (nothing changed
        on the runtime's behalf) and never a faked status.  A successful one
        repaints, logs, and publishes exactly one ``STATUS_CHANGE``.
        """

        try:
            changed = self._application.transition(
                version_id,
                target_status,
                reason=GOVERNANCE_TRANSITION_REASON,
            )
        except StrategyApplicationError as error:
            self.warning_requested.emit(
                "晋级门阻断",
                f"{error}\n\n自动下单仍保持关闭。",
            )
            self.log_requested.emit(f"策略状态变更被阻断：{error}")
            return
        self.refresh()
        self.log_requested.emit(
            f"{changed.strategy_id} 已变更为 {changed.status}"
        )
        self.runtime_event_requested.emit(
            StrategyRuntimeEvent(
                severity="info",
                component="strategy",
                code="STATUS_CHANGE",
                message=(
                    f"{changed.strategy_id} {changed.semver} -> "
                    f"{changed.status}"
                ),
            )
        )


__all__ = ["StrategyGovernanceOrchestrator"]
