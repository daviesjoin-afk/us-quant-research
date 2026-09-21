"""The market route's projection into its page, and its quote-recency memory.

The orchestrator owns the feed; this owns what the feed looks like.  Splitting
them keeps the runtime file inside its line budget *and* keeps two genuinely
different jobs apart: deciding to start or stop a stream is not the same as
turning one snapshot into the four cards, the quote grid and the control strip.

Two pieces of state live here because they are rendering state, not runtime
truth:

* the **recently-ready cache**.  A quote that is fresh now stays "recently
  ready" for thirty seconds, so a momentarily stale tick does not black out a
  candidate.  The cache is keyed by symbol and pruned against the subscription,
  because a symbol the operator removed must stop answering "recently ready".
* the **scope line and watchlist note**.  Both are text written on the page
  outside a full render.

What it must never do is decide anything.  It cannot start a feed, resolve a
credential, or ask whether a stop is allowed; it is handed a snapshot and
draws it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from time import monotonic

from us_quant.desktop_v2.orchestration.market.models import (
    MarketReadinessInputs,
)
from us_quant.desktop_v2.pages.market.models import (
    MarketConnectingFacts,
    MarketReadinessFacts,
)
from us_quant.desktop_v2.pages.market.presenter import (
    build_connecting_view,
    build_market_view,
    control_view,
)
from us_quant.desktop_v2.pages.market.rows import quote_rows
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.runtime.preflight import (
    calculate_quote_readiness_breakdown,
)


#: How long a symbol stays "recently ready" after its last fresh quote.
RECENTLY_READY_SECONDS = 30.0


class MarketRenderer:
    """Draws the market page from finished facts and remembers quote recency."""

    def __init__(self, *, page: object) -> None:
        self._page = page
        self._inputs = MarketReadinessInputs()
        self._scope = ""
        self._watchlist_note: str | None = None
        self._recent_ready: dict[str, float] = {}

    # -- inputs the composition root pushes in ---------------------------

    def set_readiness_inputs(self, inputs: MarketReadinessInputs) -> None:
        """Hand over the cross-domain symbols the readiness breakdown needs.

        These come from the auto-quant shortlist and the selected strategy's
        parameters.  The renderer must not go looking for them itself: that
        would make the market layer depend on the execution page and the
        strategy application.
        """

        self._inputs = inputs

    def set_scope(self, text: str) -> None:
        """Set the scope line written outside a full render."""

        self._scope = text
        self._page.render_scope(text)

    def set_subscription_symbols(
        self, symbols: tuple[str, ...], *, note: str | None = None
    ) -> None:
        """Write the subscription subset; not an operator intent.

        Research and the targeted workflow both change what is subscribed, and
        neither may touch the page directly.
        """

        self._watchlist_note = note
        self._page.set_subscription_symbols(symbols)

    def set_selected_provider(self, source_id: str) -> None:
        """Point the page's provider combo without emitting an intent."""

        self._page.set_selected_provider(source_id)

    def selected_provider(self) -> str:
        """The provider the route currently points at, as finished input."""

        return self._page.selected_provider()

    def subscription_symbols(self) -> tuple[str, ...]:
        """The subscription subset the route currently holds.

        A *query*, not the truth: what is actually subscribed is what the live
        worker was built with.  This is the operator's typed draft, which the
        switch and settings paths need before they decide anything.
        """

        return self._page.subscription_symbols()

    # -- the recency cache ----------------------------------------------

    def reset(self) -> None:
        """Forget every recency stamp; a new feed has no history."""

        self._recent_ready.clear()

    def was_recently_ready(self, symbol: str) -> bool:
        """Whether this symbol had a fresh quote inside the recency window."""

        observed = self._recent_ready.get(symbol)
        return (
            observed is not None
            and monotonic() - observed <= RECENTLY_READY_SECONDS
        )

    def recently_ready_symbols(self) -> tuple[str, ...]:
        """The symbols inside the recency window, as finished data."""

        return tuple(
            symbol
            for symbol in self._recent_ready
            if self.was_recently_ready(symbol)
        )

    # -- rendering -------------------------------------------------------

    def control_view(self, *, live: bool, stop_pending: bool):
        """Which market controls the page may offer, from the runtime facts."""

        return control_view(
            worker_running=live,
            stop_pending=stop_pending,
            symbols_enabled=not live,
            provider_enabled=True,
        )

    def render_connecting(
        self,
        *,
        source_id: str,
        symbol_count: int,
        controls,
    ) -> None:
        """Draw the worker-started, first-snapshot-pending state.

        ``snapshot=None`` means "nothing has ever started"; once the worker is
        about to run that is no longer true, so the connecting view is drawn
        rather than the idle one.
        """

        self._page.render(
            build_connecting_view(
                facts=MarketConnectingFacts(
                    source_id=source_id,
                    symbol_count=symbol_count,
                ),
                scope=self._scope,
                controls=controls,
                watchlist_note="Level I 持续订阅",
            )
        )

    def render_snapshot(self, *, snapshot: MarketSnapshot, controls):
        """Project one snapshot into the page; returns the readiness breakdown.

        The breakdown is returned rather than kept because the shell badges
        narrate it and the badges are the window's.
        """

        self._update_recency(snapshot)
        readiness = calculate_quote_readiness_breakdown(
            snapshot,
            candidate_symbols=self._inputs.candidate_symbols,
            reference_symbols=self._inputs.reference_symbols,
            recently_ready_symbols=self.recently_ready_symbols(),
        )
        self._page.render(
            build_market_view(
                snapshot=snapshot,
                readiness=MarketReadinessFacts.from_breakdown(readiness),
                scope=self._scope,
                rows=quote_rows(snapshot),
                controls=controls,
                watchlist_note=self._watchlist_note,
            )
        )
        return readiness

    def render_controls(self, controls) -> None:
        """Publish just the control state and the watchlist note."""

        self._page.render_controls(
            controls,
            watchlist=self._watchlist_note,
        )

    def render_stopped(self, reason: str) -> None:
        """Show the invalidated feed's health line."""

        self._page.render_health(f"已停止：{reason}")

    def render_stop_pending(self) -> None:
        """Show the stop that has not confirmed exit yet."""

        self._page.render_health(
            "停止中：网络线程尚未确认退出；禁止重复启动"
        )

    def render_failure(self, message: str) -> None:
        """Show a feed failure; the window owns the badge and the log."""

        self._page.render_failure(message)

    def invalidate(
        self, snapshot: MarketSnapshot, reason: str, *, controls
    ) -> tuple[MarketSnapshot, object]:
        """Return the snapshot marked stale, and the readiness it then shows.

        The stamping and the repaint are one operation: a stale snapshot that
        was never drawn would leave the grid showing live quotes under a
        stopped header.
        """

        invalid_quotes = tuple(
            replace(quote, stale=True, stale_reason=reason)
            for quote in snapshot.quotes
        )
        stale = replace(
            snapshot,
            connected=False,
            ready=False,
            quotes=invalid_quotes,
            message=reason,
            observed_at=datetime.now(timezone.utc),
        )
        return stale, self.render_snapshot(snapshot=stale, controls=controls)

    def _update_recency(self, snapshot: MarketSnapshot) -> None:
        """Refresh the recently-ready cache and prune what is unsubscribed.

        Two rules are load-bearing and both come from the route this replaced:
        a fresh quote refreshes its symbol's timestamp, and a symbol leaves the
        cache as soon as it stops being subscribed.  Without the prune the
        cache would keep answering "recently ready" for a symbol the operator
        removed from the feed.
        """

        now_monotonic = monotonic()
        current_symbols = {
            quote.symbol for quote in snapshot.quotes if quote.realtime_ready
        }
        for symbol in current_symbols:
            self._recent_ready[symbol] = now_monotonic
        subscribed = {quote.symbol for quote in snapshot.quotes}
        self._recent_ready = {
            symbol: observed
            for symbol, observed in self._recent_ready.items()
            if symbol in subscribed
            and now_monotonic - observed <= RECENTLY_READY_SECONDS
        }


__all__ = ["MarketRenderer"]
