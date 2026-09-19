"""Market data application service.

This is the single entry point the UI and the trading runtime use to start,
observe and stop a market-data feed.  It owns provider *policy*: which source
ids exist, the stale thresholds, the IBKR requested market-data type, the
session-aware venue, the source labels, the coverage copy, and which providers
get a push listener.  It owns provider *lifecycle*: prepared / running /
stop-requested / finished.

It deliberately does **not** know any concrete adapter.  Construction is
injected as a mapping of factory callables, so this module imports only the
domain and the ports.  The wiring that names IBKR/Alpaca/Finnhub lives in
``trading.composition.market_data``.

TRANSITIONAL DEPENDENCY
    ``IBKRConnectionConfig`` is imported here because Broker/Account v2 has
    not migrated yet: the connection settings transaction still hands the
    application an IBKR config, and ``ensure_config_update_allowed`` /
    ``update_config`` must keep working for it.  The Broker/Account v2 change
    removes this import.  Note what is *not* imported even so: no IBKR stream,
    no ``ibapi``, no adapter module.

Lifecycle semantics (preserved from the service this replaces):

* ``stop requested`` is **not** ``run finished``.  ``stop()`` only asks the
  adapter to wind down; ``run()`` can stay inside the adapter for an unbounded
  time afterwards.  Until it returns, no second stream may be prepared and no
  new connection config may be applied.
* a second ``prepare()`` while a stream is live raises
  ``MarketDataActiveError`` instead of silently replacing the old one.
* construction fails closed: an unknown source id raises ``ValueError`` (never
  a fallback to IBKR), and a failed construction leaves no half-initialised
  adapter behind and never reports the application as running.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.ports.market_data import (
    MarketDataActiveError,
    MarketDataPort,
    SnapshotListener,
)


# Stable source ids.  These are logic keys; the display names live in
# ``SOURCE_LABELS`` and are never compared.
SOURCE_ALPACA_IEX = "alpaca_iex"
SOURCE_FINNHUB_TRADES = "finnhub_trades"
SOURCE_IBKR = "ibkr"
SOURCE_IBKR_EXTENDED = "ibkr_extended"

#: The complete set of accepted source ids, in display order.  Anything
#: outside this tuple is rejected; there is no catch-all branch.
SUPPORTED_SOURCES: tuple[str, ...] = (
    SOURCE_ALPACA_IEX,
    SOURCE_FINNHUB_TRADES,
    SOURCE_IBKR,
    SOURCE_IBKR_EXTENDED,
)

SOURCE_LABELS: Mapping[str, str] = {
    SOURCE_ALPACA_IEX: "Alpaca",
    SOURCE_FINNHUB_TRADES: "Finnhub",
    SOURCE_IBKR: "IBKR",
    SOURCE_IBKR_EXTENDED: "IBKR 5×24",
}

# Sources whose adapters push snapshots through a listener.  IBKR is absent on
# purpose: it has always been polled by the desktop's snapshot timer, and
# giving it a listener now would publish each quote twice.
PUSH_LISTENER_SOURCES: frozenset[str] = frozenset(
    {SOURCE_ALPACA_IEX, SOURCE_FINNHUB_TRADES}
)

ALPACA_STALE_AFTER_SECONDS = 8.0
FINNHUB_STALE_AFTER_SECONDS = 20.0
IBKR_STALE_AFTER_SECONDS = 8.0

#: Type 1 = live (realtime) IBKR data, with the delayed fallback the stream
#: itself handles.  The adapter maps this onto the domain mode.
IBKR_REQUESTED_MARKET_DATA_TYPE = 1

#: Venue used by every non-extended source.  Only IBKR routes on it.
DEFAULT_MARKET_EXCHANGE = "SMART"


@dataclass(frozen=True, slots=True)
class MarketDataCredentials:
    """Provider credentials, kept out of the domain and out of reprs.

    ``repr=False`` on every secret is load-bearing: an exception message or a
    log line that formats this object must not print an API key.
    """

    alpaca_api_key: str = field(default="", repr=False)
    alpaca_api_secret: str = field(default="", repr=False)
    finnhub_api_key: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class MarketDataStartRequest:
    """What the operator asked for, with no provider knowledge attached.

    ``market_exchange`` is ``None`` for "let the application route this",
    which is the normal case: the extended IBKR source follows the US equity
    session (SMART during pre/regular/after hours, OVERNIGHT overnight) and
    everything else stays on SMART.
    """

    source_id: str
    symbols: tuple[str, ...]
    credentials: MarketDataCredentials = field(
        default_factory=MarketDataCredentials
    )
    market_exchange: str | None = None


@dataclass(frozen=True, slots=True)
class MarketDataLifecycle:
    """Immutable lifecycle view.  Carries no quote data."""

    source_id: str
    symbols: tuple[str, ...]
    running: bool
    last_error: str | None


#: Builds one adapter for a request.  ``listener`` is ``None`` for sources
#: that are polled rather than pushed.
#:
#: ``config`` is the application's *current* IBKR connection config, passed on
#: every call rather than captured by the factory.  A factory that closed over
#: the start-up config would silently ignore a later ``update_config`` and
#: rebuild with stale settings -- a bug the settings transaction cannot see,
#: because it only checks that the application accepted the new config.
#:
#: TRANSITIONAL DEPENDENCY: this parameter is IBKR-shaped and disappears when
#: Broker/Account v2 gives the connection its own port.
ProviderFactory = Callable[
    [MarketDataStartRequest, SnapshotListener | None, IBKRConnectionConfig],
    MarketDataPort,
]


class MarketDataApplication:
    """Owns provider policy and the lifecycle of the current feed."""

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        factories: Mapping[str, ProviderFactory],
        exchange_resolver: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self._factories = dict(factories)
        self._exchange_resolver = exchange_resolver
        self._adapter: MarketDataPort | None = None
        self._source_id: str = ""
        self._symbols: tuple[str, ...] = ()
        #: The venue the *current* prepared adapter was actually built with.
        #: This is committed only after the factory returns, so it always
        #: describes a real adapter rather than an attempted one.  It is the
        #: single source of truth for the venue: callers read it instead of
        #: re-resolving, because the resolver is session-dependent and a second
        #: call can legitimately return a different venue.
        self._market_exchange: str = DEFAULT_MARKET_EXCHANGE
        # Lifecycle is three separate flags rather than one state machine.
        # They are *not* interchangeable: ``stop()`` only asks the adapter to
        # wind down and says nothing about whether ``run()`` has returned.
        # Collapsing them into one "ended" flag is what once let a second
        # stream be built while the first was still executing.
        self._stop_requested = False
        #: Whether ``run()`` has been entered at least once for this adapter.
        self._run_started = False
        #: Set once ``run`` returns, however it returns.
        self._finished = False
        self._last_error: str | None = None

    # -- construction ---------------------------------------------------

    def prepare(
        self,
        request: MarketDataStartRequest,
        *,
        listener: SnapshotListener | None = None,
    ) -> None:
        """Create the adapter for ``request``, or raise.

        Raises ``ValueError`` for an unsupported source id and re-raises
        whatever the adapter raises for missing credentials or bad broker
        configuration.  In every failure case nothing is stored, so a later
        :meth:`lifecycle` cannot claim a stream that does not exist.
        """

        if self._adapter is not None and not self._lifecycle_ended:
            raise MarketDataActiveError(
                "market data stream is still active: stop it before "
                "preparing another one"
            )
        if request.source_id not in SUPPORTED_SOURCES:
            self._last_error = (
                f"unsupported market data source: {request.source_id!r}"
            )
            raise ValueError(
                "unsupported market data source: "
                f"{request.source_id!r}; expected one of "
                f"{', '.join(SUPPORTED_SOURCES)}"
            )
        factory = self._factories.get(request.source_id)
        if factory is None:
            # A supported source with no factory is a composition bug, and it
            # must not degrade into "no stream but reported running".
            self._last_error = (
                f"no factory registered for source: {request.source_id!r}"
            )
            raise ValueError(
                "no market data factory registered for source: "
                f"{request.source_id!r}"
            )
        # Resolve the venue exactly once for this prepare transaction and hand
        # the adapter the resolved request.  The resolved value is committed to
        # ``_market_exchange`` below, *after* the factory succeeds, and that is
        # what :attr:`prepared_market_exchange` reports -- so the venue the
        # adapter routes on and the venue the caller compares against cannot
        # drift apart.
        #
        # The resolver is session-dependent: calling it twice around a
        # SMART/OVERNIGHT boundary can legitimately return two different
        # answers.  A second resolution would therefore produce an adapter
        # built for one venue while the worker reported the other, and the
        # desktop's session rotation would then skip the reconnect it owes.
        resolved_exchange = self.market_exchange_for(request)
        resolved = replace(request, market_exchange=resolved_exchange)
        try:
            adapter = factory(
                resolved,
                self.listener_for(request.source_id, listener),
                self.config,
            )
        except Exception as error:
            # Fail closed: record why, then let the caller see the real
            # exception.  ``Exception`` only -- an operator aborting the
            # process must not be turned into a market data error.
            #
            # Prepared state is deliberately *not* touched here, including
            # ``_market_exchange``: a failed attempt must not leave a venue
            # that no adapter was built for.  This matches the existing
            # semantics, where a failed prepare also leaves the previous
            # source id and symbols in place.
            self._last_error = f"{type(error).__name__}: {error}"
            raise
        self._adapter = adapter
        self._market_exchange = resolved_exchange
        self._source_id = request.source_id
        # The adapters normalise (strip/upper/dedupe) their watchlist; read it
        # back so the lifecycle reports what was actually subscribed rather
        # than re-deriving the rules here.
        self._symbols = tuple(
            getattr(adapter, "symbols", request.symbols)
        )
        self._stop_requested = False
        self._run_started = False
        self._finished = False
        self._last_error = None

    # -- provider policy ------------------------------------------------

    @staticmethod
    def listener_for(
        source_id: str,
        listener: SnapshotListener | None,
    ) -> SnapshotListener | None:
        """Whether ``source_id`` should receive the push listener.

        IBKR does not, because it never has: the desktop polls it through its
        snapshot timer.  This is behaviour preservation, not a preference --
        adding a listener would double-publish every IBKR quote.
        """

        return listener if source_id in PUSH_LISTENER_SOURCES else None

    def market_exchange_for(
        self, request: MarketDataStartRequest
    ) -> str:
        """Resolve the IBKR venue for ``request``."""

        if request.market_exchange is not None:
            return request.market_exchange
        return self.desired_market_exchange(request.source_id)

    def desired_market_exchange(self, source_id: str) -> str:
        """The venue ``source_id`` would route on right now.

        Exposed separately because the desktop also has to notice that a
        *running* extended IBKR stream is on the wrong venue for the current
        session and rotate it.  That check is provider knowledge too, so the
        UI reads it from here rather than importing the session helper.

        This is a *query about the present*, not the venue of the prepared
        adapter: it re-runs the resolver on every call by design, because the
        caller is asking "has the session moved on?".  Compare it against
        :attr:`prepared_market_exchange`, which is the frozen record of what
        the live adapter was actually built with.
        """

        if source_id == SOURCE_IBKR_EXTENDED and (
            self._exchange_resolver is not None
        ):
            return self._exchange_resolver()
        return DEFAULT_MARKET_EXCHANGE

    @property
    def prepared_market_exchange(self) -> str:
        """The venue the currently prepared adapter was built with.

        This is the *only* correct source for "which venue is the live feed
        on?".  Re-deriving it by calling :meth:`market_exchange_for` or
        :meth:`desired_market_exchange` a second time is a bug: the resolver
        follows the US equity session, so around a SMART/OVERNIGHT boundary two
        calls can legitimately disagree.  An adapter built for one venue while
        the caller believed the other would make the desktop's session rotation
        skip a reconnect it owes.

        Before any successful prepare this reports
        :data:`DEFAULT_MARKET_EXCHANGE`, which is what a freshly constructed
        application has.
        """

        return self._market_exchange

    # -- lifecycle ------------------------------------------------------

    @property
    def _lifecycle_ended(self) -> bool:
        """Whether the prepared adapter may be replaced.

        Only two states qualify:

        * ``run()`` has returned -- normally or by raising -- so nothing is
          executing any more; or
        * the adapter was prepared but never run, and has since been asked to
          stop, so there is no execution left to protect.

        ``stop()`` on its own is deliberately *not* enough.  It only asks the
        adapter to wind down; ``run()`` can still be inside it for an
        unbounded time afterwards (an IBKR socket loop takes seconds to
        unwind, and a blocked network read takes as long as it takes).
        """

        if self._finished:
            return True
        return self._stop_requested and not self._run_started

    def run(self) -> None:
        """Run the prepared adapter to completion, then mark it finished.

        ``finally`` clears the running state whether the adapter returned
        normally or raised, so ``running`` cannot outlive the call.  An
        exception is recorded and re-raised -- the caller decides what to tell
        the operator.

        ``_run_started`` is what makes ``stop()`` safe to call from another
        thread while this is executing: until the ``finally`` has run, the
        application refuses to prepare a replacement or accept a new config.
        """

        adapter = self._adapter
        if adapter is None:
            raise RuntimeError(
                "no market data adapter has been prepared: call prepare "
                "before run"
            )
        self._run_started = True
        try:
            adapter.run()
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            raise
        finally:
            self._finished = True

    def ensure_config_update_allowed(
        self, config: IBKRConnectionConfig
    ) -> None:
        """Raise if ``config`` could not be applied right now.

        Split out of :meth:`update_config` so a caller can *check* before it
        commits to anything irreversible.  Saving settings has to know the
        change will be accepted before it writes the file.

        Checks only -- no state change, no I/O.  An identical config is always
        allowed, because re-saving unchanged settings must not be blocked by
        an unrelated live stream.
        """

        if config == self.config:
            return
        if self._adapter is not None and not self._lifecycle_ended:
            raise MarketDataActiveError(
                "cannot change the IBKR connection config while a market "
                "data stream is active: stop it first"
            )

    def update_config(self, config: IBKRConnectionConfig) -> None:
        """Replace the IBKR connection config used by future preparations.

        Fails closed while a stream is live: the running adapter is connected
        with the config it was built from.  Deliberately inert otherwise -- no
        network call, no reconnect, no stream creation.
        """

        self.ensure_config_update_allowed(config)
        self.config = config

    def stop(self) -> None:
        """Ask the prepared adapter to wind down.  Safe to call repeatedly."""

        self._stop_requested = True
        adapter = self._adapter
        if adapter is None:
            return
        adapter.stop()

    # -- queries --------------------------------------------------------

    def snapshot(self) -> MarketSnapshot:
        """The current market truth.

        Before anything is prepared this is an empty, disconnected snapshot
        rather than an exception: the UI polls this on a timer and must be
        able to render "nothing yet".
        """

        adapter = self._adapter
        if adapter is None:
            from datetime import datetime, timezone

            return MarketSnapshot(
                generation=0,
                connected=False,
                ready=False,
                reconnect_attempt=0,
                quotes=(),
                error_code=None,
                message=self._last_error or "尚未启动行情",
                observed_at=datetime.now(timezone.utc),
                source_id=self._source_id,
                source_label=SOURCE_LABELS.get(
                    self._source_id, self._source_id
                ),
                coverage="",
            )
        return adapter.snapshot()

    def lifecycle(self) -> MarketDataLifecycle:
        return MarketDataLifecycle(
            source_id=self._source_id,
            symbols=self._symbols,
            running=(
                self._adapter is not None and not self._lifecycle_ended
            ),
            last_error=self._last_error,
        )


__all__ = [
    "ALPACA_STALE_AFTER_SECONDS",
    "DEFAULT_MARKET_EXCHANGE",
    "FINNHUB_STALE_AFTER_SECONDS",
    "IBKR_REQUESTED_MARKET_DATA_TYPE",
    "IBKR_STALE_AFTER_SECONDS",
    "PUSH_LISTENER_SOURCES",
    "ProviderFactory",
    "SOURCE_ALPACA_IEX",
    "SOURCE_FINNHUB_TRADES",
    "SOURCE_IBKR",
    "SOURCE_IBKR_EXTENDED",
    "SOURCE_LABELS",
    "SUPPORTED_SOURCES",
    "MarketDataApplication",
    "MarketDataCredentials",
    "MarketDataLifecycle",
    "MarketDataStartRequest",
]
