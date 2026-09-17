"""Provider selection and lifecycle for the read-only market data feed.

Before this module ``desktop.py`` owned the whole construction decision::

    if provider == "alpaca_iex":
        AlpacaIEXStream(...)
    elif provider == "finnhub_trades":
        FinnhubTradeStream(...)
    else:
        IBKRReadOnlyStream(...)

so the UI layer knew every provider's constructor, its credentials, its
stale timeout, the IBKR extended-hours venue, the provider label and the
coverage copy.  None of that is a presentation decision.  It lives here
instead, and the UI only supplies *what the operator asked for*.

The three adapters themselves are untouched: ``ibkr_stream``,
``alpaca_stream`` and ``finnhub_stream`` keep their own WebSocket/API
handling.  This module is an application boundary, not a rewrite.

Design constraints (deliberate, kept small on purpose):

* no GUI toolkit import, no ``QThread``, no widget and no ``MainWindow``:
  the module is a plain object graph, so it is directly unit testable;
* construction **fails closed**.  ``build_stream`` raises on an unknown
  provider instead of falling back to IBKR, and a failed construction
  leaves no half-initialised stream behind and never reports the service
  as running -- a typo must not silently become a broker connection;
* behaviour is preserved exactly, not "improved": the stale thresholds,
  the requested market data type, the provider labels and the coverage
  copy are the same values the UI used to pass.  IBKR is deliberately
  built *without* a push listener because that is how it ran before --
  the desktop's snapshot timer polls it, and adding a listener would
  double-publish every quote;
* :class:`MarketDataServiceSnapshot` describes the *service* lifecycle
  only.  Quote truth stays with ``StreamSnapshot``; thread liveness stays
  with the Qt adapter's ``QThread.isRunning()``.
* the service owns the lifecycle half of the adapter -- ``build_stream``,
  ``run`` and ``stop`` -- so ``running`` cannot outlive the stream.  A
  stream that returns or raises is marked finished by ``run``'s
  ``finally``; ``QThread`` stays in the Qt shell.
* two operations fail closed rather than guess: a second
  ``build_stream`` while a stream is live raises instead of silently
  dropping the first one out of management, and ``update_config`` refuses
  while a stream is live because the open connection is the one the
  stream was built with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from us_quant.alpaca_stream import (
    AlpacaCredentialsMissing,
    AlpacaIEXStream,
)
from us_quant.extended_hours import ibkr_market_data_exchange
from us_quant.finnhub_stream import (
    FinnhubCredentialsMissing,
    FinnhubTradeStream,
)
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_stream import IBKRReadOnlyStream, StreamSnapshot


PROVIDER_IBKR = "ibkr"
PROVIDER_IBKR_EXTENDED = "ibkr_extended"
PROVIDER_ALPACA_IEX = "alpaca_iex"
PROVIDER_FINNHUB_TRADES = "finnhub_trades"

#: The complete set of accepted providers, in display order.  Anything
#: outside this tuple is rejected; there is no catch-all branch.
SUPPORTED_PROVIDERS: tuple[str, ...] = (
    PROVIDER_ALPACA_IEX,
    PROVIDER_FINNHUB_TRADES,
    PROVIDER_IBKR,
    PROVIDER_IBKR_EXTENDED,
)

_SUPPORTED_PROVIDER_SET = frozenset(SUPPORTED_PROVIDERS)

# Providers whose adapters push snapshots through a listener.  IBKR is
# absent on purpose: it has always been polled by the desktop's snapshot
# timer, and giving it a listener now would publish each quote twice.
_PUSH_LISTENER_PROVIDERS = frozenset(
    {PROVIDER_ALPACA_IEX, PROVIDER_FINNHUB_TRADES}
)

ALPACA_STALE_AFTER_SECONDS = 8.0
FINNHUB_STALE_AFTER_SECONDS = 20.0
IBKR_STALE_AFTER_SECONDS = 8.0

#: Type 1 = live (realtime) IBKR data, with the delayed fallback that the
#: stream itself handles.
IBKR_REQUESTED_MARKET_DATA_TYPE = 1

IBKR_PROVIDER_LABEL = "IBKR"
IBKR_EXTENDED_PROVIDER_LABEL = "IBKR 5×24"

IBKR_COVERAGE = "由 IBKR 订阅权限决定"
IBKR_EXTENDED_COVERAGE = (
    "IBKR 5×24：盘前/盘后 SMART；隔夜直接 OVERNIGHT；"
    "实际权限与标的资格以券商回调为准"
)

#: Venue used by every non-extended provider.  Only IBKR routes on it.
DEFAULT_MARKET_EXCHANGE = "SMART"


class MarketDataStreamActive(RuntimeError):
    """A live stream would be disturbed by the operation.

    A ``RuntimeError`` subclass on purpose: refusing is a runtime state
    conflict, not a programming error in the caller's arguments.  It is a
    distinct class so the UI can catch *this* refusal without also
    swallowing the ``RuntimeError``s the IBKR adapter raises for a real
    connection failure.
    """


@dataclass(frozen=True, slots=True)
class MarketDataRequest:
    """What the operator asked for, with no provider knowledge attached.

    ``market_exchange`` is ``None`` for "let the service route this",
    which is the normal case: the extended IBKR provider follows the US
    equity session (SMART during pre/regular/after hours, OVERNIGHT
    overnight) and everything else stays on SMART.  An explicit value is
    only for callers that genuinely know the venue they want.
    """

    provider: str
    symbols: tuple[str, ...]
    market_exchange: str | None = None
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    finnhub_api_key: str = ""


@dataclass(frozen=True, slots=True)
class MarketDataServiceSnapshot:
    """Immutable lifecycle view of the service.

    This carries no quote data: ``StreamSnapshot`` remains the single
    source of truth for prices, staleness and coverage.
    """

    provider: str
    symbols: tuple[str, ...]
    running: bool
    last_error: str | None


class MarketDataService:
    """Builds and owns the market data adapter for a request.

    ``running`` means "a stream was built here and has not been asked to
    stop".  It is the service's own bookkeeping -- the authoritative
    answer to "is the feed thread alive" stays with the Qt adapter, which
    owns the ``QThread``.
    """

    def __init__(self, config: IBKRConnectionConfig) -> None:
        self.config = config
        self._stream: Any | None = None
        self._provider: str = ""
        self._symbols: tuple[str, ...] = ()
        # Lifecycle is three separate flags rather than a state machine, but
        # they are *not* interchangeable: ``stop()`` only asks the adapter to
        # wind down and says nothing about whether ``run()`` has returned.
        # Collapsing them into one "ended" flag is what let a second stream
        # be built while the first was still executing.  The four states are
        # ``built`` (neither flag), ``running`` (started, not finished),
        # ``stop_requested`` (its own flag) and ``finished``.
        self._stop_requested = False
        #: Whether ``run()`` has been entered at least once for this stream.
        self._run_started = False
        #: Set once ``run`` returns, however it returns.  Without it a
        #: stream that ended on its own would keep reading as running.
        self._finished = False
        self._last_error: str | None = None

    # -- construction ---------------------------------------------------

    def build_stream(
        self,
        request: MarketDataRequest,
        *,
        listener: Callable[[StreamSnapshot], None] | None = None,
    ) -> Any:
        """Create the adapter for ``request``, or raise.

        Raises ``ValueError`` for an unsupported provider and re-raises
        whatever the adapter raises for missing credentials or bad broker
        configuration.  In every failure case nothing is stored, so a
        later :meth:`snapshot` cannot claim a stream that does not exist.

        Raises ``RuntimeError`` if the service still holds a stream that
        has neither finished nor been stopped before it ever ran.
        Overwriting it would take that stream out of the service's
        lifecycle management -- it would keep running while the service
        reported the new one -- so the order is enforced instead of
        guessed: ``build``, ``run``, ``stop``/finished, then the next
        ``build``.  A ``stop`` request alone is not enough: until ``run``
        has returned, the old stream is still executing.  The old stream is
        deliberately *not* stopped or silently replaced here; the caller
        owns that decision.
        """

        if self._stream is not None and not self._lifecycle_ended:
            raise MarketDataStreamActive(
                "market data stream is still active: stop it before "
                "building another one"
            )
        if request.provider not in _SUPPORTED_PROVIDER_SET:
            self._last_error = (
                f"unsupported market data provider: {request.provider!r}"
            )
            raise ValueError(
                "unsupported market data provider: "
                f"{request.provider!r}; expected one of "
                f"{', '.join(SUPPORTED_PROVIDERS)}"
            )
        try:
            stream = self._create(request, listener=listener)
        except Exception as error:
            # Fail closed: record why, then let the caller see the real
            # exception.  ``Exception`` only -- an operator aborting the
            # process must not be turned into a market data error.
            self._last_error = f"{type(error).__name__}: {error}"
            raise
        self._stream = stream
        self._provider = request.provider
        # The adapters normalise (strip/upper/dedupe) their watchlist;
        # read it back so the snapshot reports what was actually
        # subscribed rather than re-deriving the rules here.
        self._symbols = tuple(
            getattr(stream, "symbols", request.symbols)
        )
        self._stop_requested = False
        self._run_started = False
        self._finished = False
        self._last_error = None
        return stream

    def _create(
        self,
        request: MarketDataRequest,
        *,
        listener: Callable[[StreamSnapshot], None] | None,
    ) -> Any:
        if request.provider == PROVIDER_ALPACA_IEX:
            return AlpacaIEXStream(
                symbols=request.symbols,
                api_key=request.alpaca_api_key,
                api_secret=request.alpaca_api_secret,
                stale_after_seconds=ALPACA_STALE_AFTER_SECONDS,
                listener=self.listener_for(request.provider, listener),
            )
        if request.provider == PROVIDER_FINNHUB_TRADES:
            return FinnhubTradeStream(
                symbols=request.symbols,
                api_key=request.finnhub_api_key,
                stale_after_seconds=FINNHUB_STALE_AFTER_SECONDS,
                listener=self.listener_for(request.provider, listener),
            )
        return self._create_ibkr(request)

    def _create_ibkr(self, request: MarketDataRequest) -> IBKRReadOnlyStream:
        is_extended = request.provider == PROVIDER_IBKR_EXTENDED
        return IBKRReadOnlyStream(
            self.config,
            symbols=request.symbols,
            requested_market_data_type=IBKR_REQUESTED_MARKET_DATA_TYPE,
            stale_after_seconds=IBKR_STALE_AFTER_SECONDS,
            market_exchange=self.market_exchange_for(request),
            provider_label=(
                IBKR_EXTENDED_PROVIDER_LABEL
                if is_extended
                else IBKR_PROVIDER_LABEL
            ),
            coverage=(
                IBKR_EXTENDED_COVERAGE if is_extended else IBKR_COVERAGE
            ),
        )

    # -- provider policy ------------------------------------------------

    def listener_for(
        self,
        provider: str,
        listener: Callable[[StreamSnapshot], None] | None,
    ) -> Callable[[StreamSnapshot], None] | None:
        """Whether ``provider`` should receive the push listener.

        IBKR does not, because it never has: the desktop polls it through
        its snapshot timer.  This is behaviour preservation, not a
        preference -- see the module docstring.
        """

        return listener if provider in _PUSH_LISTENER_PROVIDERS else None

    def market_exchange_for(self, request: MarketDataRequest) -> str:
        """Resolve the IBKR venue for ``request``.

        The session-aware choice used to sit in the UI; it belongs with
        the provider that routes on it.
        """

        if request.market_exchange is not None:
            return request.market_exchange
        return self.desired_market_exchange(request.provider)

    def desired_market_exchange(self, provider: str) -> str:
        """The venue ``provider`` would route on right now.

        Exposed separately from :meth:`market_exchange_for` because the
        desktop also has to notice that a *running* extended IBKR stream
        is on the wrong venue for the current session and rotate it.  That
        check is provider knowledge too, so it reads it from here rather
        than importing the session helper into the UI.
        """

        if provider == PROVIDER_IBKR_EXTENDED:
            return ibkr_market_data_exchange()
        return DEFAULT_MARKET_EXCHANGE

    # -- lifecycle ------------------------------------------------------

    @property
    def _lifecycle_ended(self) -> bool:
        """Whether the built stream may be replaced.

        Only two states qualify:

        * ``run()`` has returned -- normally or by raising -- so nothing is
          executing any more; or
        * the stream was built but never run, and has since been asked to
          stop, so there is no execution left to protect.

        ``stop()`` on its own is deliberately *not* enough.  It only asks
        the adapter to wind down; ``run()`` can still be inside the adapter
        for an unbounded time afterwards (an IBKR socket loop takes seconds
        to unwind, and a blocked network read takes as long as it takes).
        Treating "stop requested" as "finished" would let a second stream be
        built -- and a new connection config be applied -- while the first
        one is still running.

        A separate "is running right now" flag is not kept: it would be
        exactly ``self._run_started and not self._finished``, and two flags
        that must always agree are one more way to get the lifecycle wrong.
        """

        if self._finished:
            return True
        return self._stop_requested and not self._run_started

    def run(self) -> None:
        """Run the built stream to completion, then mark it finished.

        The service owns the lifecycle half of the adapter, so ``running``
        cannot outlive the call: ``finally`` clears it whether the stream
        returned normally or raised.  An exception is recorded and
        re-raised -- the caller decides what to tell the operator.

        ``_run_started`` is what makes ``stop()`` safe to call from another
        thread while this is executing: until the ``finally`` below has run,
        the service refuses to build a replacement stream or accept a new
        connection config.
        """

        stream = self._stream
        if stream is None:
            raise RuntimeError(
                "no market data stream has been built: call build_stream "
                "before run"
            )
        self._run_started = True
        try:
            stream.run()
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
        change will be accepted before it writes the file: applying first
        and failing the write leaves the runtime on values the operator was
        told were not saved.

        Checks only -- it changes no state and performs no I/O.  An
        identical config is always allowed, because re-saving unchanged
        settings must not be blocked by an unrelated live stream.
        """

        if config == self.config:
            return
        if self._stream is not None and not self._lifecycle_ended:
            raise MarketDataStreamActive(
                "cannot change the IBKR connection config while a market "
                "data stream is active: stop it first"
            )

    def update_config(self, config: IBKRConnectionConfig) -> None:
        """Replace the IBKR connection config used by future builds.

        Fails closed while a stream is live: the running adapter is
        connected with the config it was built from, so changing it here
        would make the service describe a connection that is not the one
        actually open.  The caller stops the stream first.

        Deliberately inert otherwise -- no network call, no reconnect, no
        stream creation.  Only the *next* :meth:`build_stream` sees it.
        """

        self.ensure_config_update_allowed(config)
        self.config = config

    def stop(self) -> None:
        """Ask the built adapter to wind down.  Safe to call repeatedly."""

        self._stop_requested = True
        stream = self._stream
        if stream is None:
            return
        stream.stop()

    def snapshot(self) -> MarketDataServiceSnapshot:
        return MarketDataServiceSnapshot(
            provider=self._provider,
            symbols=self._symbols,
            running=self._stream is not None and not self._lifecycle_ended,
            last_error=self._last_error,
        )


__all__ = [
    "ALPACA_STALE_AFTER_SECONDS",
    "DEFAULT_MARKET_EXCHANGE",
    "FINNHUB_STALE_AFTER_SECONDS",
    "IBKR_COVERAGE",
    "IBKR_EXTENDED_COVERAGE",
    "IBKR_EXTENDED_PROVIDER_LABEL",
    "IBKR_PROVIDER_LABEL",
    "IBKR_REQUESTED_MARKET_DATA_TYPE",
    "IBKR_STALE_AFTER_SECONDS",
    "MarketDataRequest",
    "MarketDataService",
    "MarketDataServiceSnapshot",
    "MarketDataStreamActive",
    "PROVIDER_ALPACA_IEX",
    "PROVIDER_FINNHUB_TRADES",
    "PROVIDER_IBKR",
    "PROVIDER_IBKR_EXTENDED",
    "SUPPORTED_PROVIDERS",
    # Re-exported so the UI can catch a credential failure without
    # importing the provider adapter modules directly: naming *which*
    # provider failed is provider knowledge, and it belongs here.
    "AlpacaCredentialsMissing",
    "FinnhubCredentialsMissing",
]
