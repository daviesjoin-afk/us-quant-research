"""The targeted session and preflight orchestration capability.

The Targeted workspace's *session* half used to be four loose facts on
``MainWindow`` -- the target status, the minute-evidence status, the last preflight
result, and "whatever the target ``QLineEdit`` currently says" -- plus the handlers
that maintained them and the session-side page paint.  All of it moved here; the
package docstring has the naming and the full rule set.  Two rules here are easy to
break: the **Shadow guard runs before the target commit**, and the **Shadow
snapshot is read at paint time and never stored**.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_targeted_session_service import (
    DesktopTargetedSessionService,
)
from us_quant.desktop_v2.orchestration.research.targeted.session import (
    queries,
)
from us_quant.desktop_v2.orchestration.research.targeted.session.models import (
    APPLY_LOG,
    DEFAULT_MINUTE_STATUS,
    INVALID_MESSAGE,
    INVALID_TITLE,
    MARKET_LIVE_MESSAGE,
    MARKET_LIVE_TITLE,
    REFUSAL_INFORMATION,
    REFUSAL_WARNING,
    SHADOW_ACTIVE_MESSAGE,
    SHADOW_ACTIVE_TITLE,
    SUBSCRIBE_LOG,
    SUBSCRIBE_NOTE,
    TargetedSessionSnapshot,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedStrategyOption,
)
from us_quant.desktop_v2.pages.research.targeted.session_presenter import (
    session_view,
)
from us_quant.shadow.models import ShadowSnapshot
from us_quant.trading.domain.account import BrokerAccountSnapshot
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.universe import UniverseSnapshot


class TargetedSessionOrchestrator(QObject):
    """Owns the targeted session truth, its commands and its render."""

    #: ``(level, title, message)``.  The level travels rather than being inferred,
    #: because the refusals do not share a severity: an invalid symbol is a
    #: *warning* (the operator mistyped) while a running Shadow session or a live
    #: feed is *information* (nothing is wrong, the request is unavailable).
    refused = Signal(str, str, str)

    #: A footer status line.  No event store: unlike the evidence round, apply and
    #: subscribe have never recorded a runtime event, and inventing one here would
    #: be a product change.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        page: object,
        service: DesktopTargetedSessionService,
        universe_provider: Callable[[], UniverseSnapshot | None],
        market_snapshot_provider: Callable[[], MarketSnapshot | None],
        market_is_live: Callable[[], bool],
        account_provider: Callable[[], BrokerAccountSnapshot | None],
        selected_strategy_provider: Callable[[], StrategyVersion | None],
        displayed_strategy_provider: Callable[[], StrategyVersion | None],
        strategy_options_provider: Callable[[], tuple[TargetedStrategyOption, ...]],
        strategy_selector: Callable[[str], None],
        exposure_multipliers_provider: Callable[[], dict[str, Decimal]],
        shadow_snapshot_provider: Callable[[], ShadowSnapshot | None],
        market_set_subscription: Callable[..., None],
        market_start: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._page = page
        self._service = service
        self._universe_provider = universe_provider
        self._market_snapshot_provider = market_snapshot_provider
        self._market_is_live = market_is_live
        self._account_provider = account_provider
        self._selected_strategy_provider = selected_strategy_provider
        self._displayed_strategy_provider = displayed_strategy_provider
        self._strategy_options_provider = strategy_options_provider
        self._strategy_selector = strategy_selector
        self._exposure_multipliers_provider = exposure_multipliers_provider
        self._shadow_snapshot_provider = shadow_snapshot_provider
        self._market_set_subscription = market_set_subscription
        self._market_start = market_start
        self._snapshot = TargetedSessionSnapshot()

    @property
    def snapshot(self) -> TargetedSessionSnapshot:
        """The canonical desktop session truth, as one immutable stored value."""

        return self._snapshot

    # -- strategy ----------------------------------------------------------
    def refresh_strategy_options(self) -> None:
        """Point the page's combo at the selection service.

        The only production caller of the page's ``set_strategy_options``; the
        window used to paint this combo itself.  The *options* are the policy's
        eligible versions and the *displayed* selection is ``restore_or_default``,
        so a version stopped since the last paint moves the combo on rather than
        showing something the runtime will not run.
        """

        displayed = self._displayed_strategy_provider()
        self._page.set_strategy_options(
            self._strategy_options_provider(),
            displayed.version_id if displayed is not None else None,
        )

    def request_strategy_selection(self, version_id: str) -> None:
        """Adopt the operator's choice, then re-derive the preflight.

        The id comes from the emitted signal, never re-read from the combo, so the
        widget cannot become the source of truth.
        """

        self._strategy_selector(version_id)
        self.refresh_preflight()

    # -- target draft ------------------------------------------------------
    def adopt_target_draft(self, value: str) -> None:
        """Record the operator's target input.  Nothing else: what the editor's
        ``textChanged`` reaches must not run a preflight, read the store, change
        the subscription or repaint.
        """

        self._snapshot = replace(
            self._snapshot,
            target_draft=queries.normalize_target_symbol(value),
        )

    # -- target commands ---------------------------------------------------
    def _validated(self, symbol: str) -> str | None:
        """Normalize ``symbol``; refuse it if unusable, else return it."""

        normalized = queries.normalize_target_symbol(symbol)
        if queries.is_valid_target_symbol(normalized):
            return normalized
        self.refused.emit(REFUSAL_WARNING, INVALID_TITLE, INVALID_MESSAGE)
        return None

    def request_target_apply(self, symbol: str) -> None:
        """Adopt ``symbol``: validate, guard, commit, refresh.

        The Shadow guard comes **before** the commit: switching under a running
        simulation would leave the page showing one symbol while the engine traded
        another, so the request is refused and both the page *and* the draft return
        to what the engine actually trades.  The subscription is pre-configured
        when the feed is *not* live; the feed is never started here, because
        applying a target is not asking for a stream.
        """
        normalized = self._validated(symbol)
        if normalized is None:
            return

        shadow = self._shadow_snapshot_provider()
        if shadow is not None and shadow.active:
            self.refused.emit(
                REFUSAL_INFORMATION, SHADOW_ACTIVE_TITLE, SHADOW_ACTIVE_MESSAGE
            )
            active = shadow.target_symbol
            self._snapshot = replace(self._snapshot, target_draft=active)
            self._page.set_target_symbol(active)
            return

        self._snapshot = replace(self._snapshot, target_draft=normalized)
        self._page.set_target_symbol(normalized)
        self._snapshot = replace(
            self._snapshot,
            target_status=queries.target_status_text(
                normalized, self._universe_provider()
            ),
        )
        if not self._market_is_live():
            self._market_set_subscription((normalized,))
        self.refresh_minute_status(normalized)
        self.refresh_preflight()
        self.log_requested.emit(APPLY_LOG.format(symbol=normalized))

    def request_target_subscribe(self, symbol: str) -> None:
        """Subscribe ``symbol`` and ask for the live feed.

        Refused while the feed is live: changing the subscription under a running
        stream would silently re-point a stream the operator believes runs on the
        old one.  Otherwise the target status is deliberately **not** recomputed --
        subscribing has never changed what the Universe says, and only 应用标的 does
        that.

        ``refresh_preflight`` runs **before** the start request, so the operator
        sees the gate verdict for the symbol they are about to stream; the first
        market snapshot then refreshes it again.
        """

        normalized = self._validated(symbol)
        if normalized is None:
            return
        if self._market_is_live():
            self.refused.emit(
                REFUSAL_INFORMATION, MARKET_LIVE_TITLE, MARKET_LIVE_MESSAGE
            )
            return

        self._snapshot = replace(self._snapshot, target_draft=normalized)
        self._page.set_target_symbol(normalized)
        self._market_set_subscription(
            (normalized,), note=SUBSCRIBE_NOTE.format(symbol=normalized)
        )
        self.log_requested.emit(SUBSCRIBE_LOG.format(symbol=normalized))
        self.refresh_minute_status(normalized)
        self.refresh_preflight()
        self._market_start()

    # -- refreshes ---------------------------------------------------------
    def refresh_minute_status(self, symbol: str | None = None) -> None:
        """Re-read the local minute evidence for the target and repaint.

        No preflight: recomputing it here "for consistency" would make one operator
        action refresh two unrelated facts.  An invalid target gets the standing
        explanation.
        """

        target = queries.normalize_target_symbol(
            symbol or self._snapshot.target_draft
        )
        if not queries.is_valid_target_symbol(target):
            self._snapshot = replace(
                self._snapshot, minute_status=DEFAULT_MINUTE_STATUS
            )
            self.render_current()
            return
        self._snapshot = replace(
            self._snapshot,
            minute_status=queries.minute_status_text(
                target, self._service.minute_summary(target)
            ),
        )
        self.render_current()

    def refresh_preflight(self) -> None:
        """Re-evaluate the preflight from every current canonical fact, then paint.

        Seven reads, each of which must be the *current* one -- a queued value
        would let the panel describe a market that has since moved.  The capital
        values pass through untouched: the multiplier and the account's
        ``net_liquidation`` stay ``Decimal`` end to end, because the evaluator does
        its own whole-share arithmetic and a ``float()`` would change it.  A
        failure keeps the last good result, so a provider error cannot present as
        all-pass.
        """

        symbol = self._snapshot.target_draft
        try:
            result = self._service.evaluate_preflight(
                symbol,
                universe_record=queries.find_universe_record(
                    self._universe_provider(), symbol
                ),
                quote=queries.find_market_quote(
                    self._market_snapshot_provider(), symbol
                ),
                account=self._account_provider(),
                strategy=self._selected_strategy_provider(),
                exposure_multiplier=(
                    self._exposure_multipliers_provider().get(
                        symbol, Decimal("1")
                    )
                ),
            )
        except Exception:
            return
        self._snapshot = replace(self._snapshot, preflight=result)
        self.render_current()

    # -- rendering ---------------------------------------------------------
    def render_current(self) -> None:
        """Draw the session from this capability's truth plus one external fact.

        The Shadow snapshot is fetched **here, per paint**, and handed straight to
        the presenter.  Storing it would make this capability a second owner of
        mutable Shadow truth, so shadow start/stop, stream ingress and position
        changes reach the page by calling this method instead.

        It paints **only** the session half; the evidence tables have their own
        owner.
        """

        shadow = self._shadow_snapshot_provider()
        self._page.render_session(
            session_view(
                snapshot=shadow,
                target_status=self._snapshot.target_status,
                minute_status=self._snapshot.minute_status,
                preflight=self._snapshot.preflight,
                controls=queries.control_view(
                    shadow is not None and shadow.active
                ),
            )
        )

__all__ = ["TargetedSessionOrchestrator"]
