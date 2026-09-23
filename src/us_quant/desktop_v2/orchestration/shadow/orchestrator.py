"""Shadow orchestration: the one owner of the internal simulation's runtime.

Before this module the Shadow runtime was a set of loose attributes on
``MainWindow`` -- ``shadow_engine``, ``shadow_snapshot``, ``shadow_store``, the
``shadow_workflow`` lease handle and the two handlers that drove them -- read and
written from six places: the two page intents, the market snapshot bridge, the
market stop interlock, the auto-quant start gate, ``closeEvent`` and the terminal
export.  This class now owns them.

The boundary it draws is the point of the extraction, and it has one rule that
matters more than the rest:

* **there is no second Shadow truth.**  ``ShadowPaperEngine`` already owns the
  session id, the active flag, the cash, the P&L, the open position, the fills and
  the marks; that is the state owner and it stays that way.  This class stores a
  *reference* to the engine and the last snapshot the engine produced, and
  :attr:`snapshot` reads whichever one the engine currently holds -- it never
  recomputes a P&L, never simulates a fill and never writes to the store.  The
  rules that decide *whether* a run may start are in :mod:`.queries`, and the
  sentences the operator sees are in :mod:`.models`.

What it deliberately does not own:

* **the generic task lifecycle.**  ``TaskThread``, the controller, the worker
  list, the closing admission gate and the cancellation handling stay on the
  window.  A start here is synchronous on the calling thread, exactly as the
  retired handler was -- the simulation is driven by market snapshots, not by a
  worker of its own -- so nothing needs submitting;
* **the shell and the event store.**  Runtime events are *requested* through
  :attr:`runtime_event_requested` and the footer line through
  :attr:`log_requested`, so neither ``Shadow -> System`` nor Shadow -> shell
  becomes a dependency;
* **the targeted session's paint.**  A snapshot change makes the session panel
  stale, but the session page belongs to ``TargetedSessionOrchestrator``.  This
  class asks for a repaint through an injected callable and is never handed the
  page, which is what keeps Shadow truth out of the session snapshot;
* **the Paper lifecycle.**  The lease it acquires is shared with Paper, and
  acquiring it while Paper holds it is refused by the lease itself.  Connecting,
  reconciling, halting and finalizing a Paper session are not this layer's, and
  this layer names none of them;
* **the market feed.**  It consumes finished snapshots through a provider and
  never owns a subscription, a worker or a price.

The window injects callables rather than objects (see ``__init__``) for the same
reason every other capability does: this module must not import the account
route, the market route, the research route or the targeted workspace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.shadow import queries
from us_quant.desktop_v2.orchestration.shadow.models import (
    SHADOW_COMPONENT,
    SHADOW_START_CODE,
    SHADOW_STOP_CODE,
    START_EVENT_MESSAGE,
    START_FAILED_TITLE,
    START_LOG_MESSAGE,
    STOP_EVENT_MESSAGE,
    ShadowLease,
    ShadowRuntimeEvent,
    ShadowCapitalFact,
    ShadowStartRefusal,
    ShadowStartRequest,
)
from us_quant.shadow.config import (
    ShadowSimulationConfig,
    build_targeted_shadow_config,
)
from us_quant.shadow.engine import ShadowPaperEngine
from us_quant.shadow.models import ShadowFill, ShadowSnapshot
from us_quant.shadow.store import ShadowPaperStore
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.universe import UniverseSnapshot


class ShadowOrchestrator(QObject):
    """Owns the internal simulation's lifecycle and its published facts.

    It is *not* the shadow engine: it builds one, starts it, feeds it market
    snapshots and publishes what it produced.  Everything that decides what a
    simulation *is* -- the entry and exit behaviour, the fills, the marks, the
    P&L and the persistence -- stays in ``us_quant.shadow``.
    """

    #: A start was refused before anything was built.  The window shows the
    #: dialog because this class owns no widgets of its own.
    refused = Signal(str, str)

    #: One runtime event the window should record.
    runtime_event_requested = Signal(object)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        store: ShadowPaperStore,
        lease: ShadowLease,
        strategy_provider: Callable[[], StrategyVersion | None],
        target_provider: Callable[[], str],
        capital_provider: Callable[[], ShadowCapitalFact | None],
        account_alias_provider: Callable[[], str],
        market_stream_provider: Callable[[], MarketSnapshot | None],
        market_is_live: Callable[[], bool],
        universe_provider: Callable[[], UniverseSnapshot | None],
        runtime_is_active: Callable[[], bool],
        exposure_multipliers_provider: Callable[[], dict[str, Decimal]],
        render_session: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._lease = lease
        self._strategy_provider = strategy_provider
        self._target_provider = target_provider
        self._capital_provider = capital_provider
        self._account_alias_provider = account_alias_provider
        self._market_stream_provider = market_stream_provider
        self._market_is_live = market_is_live
        self._universe_provider = universe_provider
        self._runtime_is_active = runtime_is_active
        self._exposure_multipliers_provider = exposure_multipliers_provider
        self._render_session = render_session
        # The engine and the last snapshot it produced.  A reference and a
        # value, never a copy of the engine's own state: the engine is the only
        # owner of the session id, the cash, the P&L, the position and the fills.
        self._engine: ShadowPaperEngine | None = None
        self._snapshot: ShadowSnapshot | None = None

    # -- read-only facts the rest of the desktop may consume -------------

    @property
    def snapshot(self) -> ShadowSnapshot | None:
        """The last snapshot the engine produced, or ``None`` before any run.

        This is the *only* Shadow fact the rest of the desktop may read, and it
        is a value rather than a handle: the session capability renders it and
        the terminal export records the fills, but neither may reach the engine.
        """

        return self._snapshot

    @property
    def is_active(self) -> bool:
        """Whether a simulation is running right now.

        The answer comes from the engine, which is the state owner: a locally
        mirrored busy flag would be a second truth that the first failed start
        would desynchronize.
        """

        engine = self._engine
        return engine is not None and engine.active

    def recent_fills(self, limit: int = 200) -> tuple[ShadowFill, ...]:
        """The simulator's recorded fills, read through its own store.

        A delegation, not a copy.  The export reads this rather than reaching for
        the store itself, so the store has one reader in the desktop layer.
        """

        return self._store.recent_fills(limit)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Run the gates, build the engine and begin the simulation.

        Every fact is read **once** and frozen into the request before the engine
        exists, so a run cannot be assembled from a strategy the operator changed
        halfway through or from a target typed after the gates were checked.  A
        refusal is published and nothing is built; a failure to actually start is
        published and the lease is handed back, so a retry is possible and the
        previous state is not overwritten by a fabricated one.
        """

        request = queries.plan_start(
            runtime_is_active=self._runtime_is_active(),
            strategy=self._strategy_provider(),
            capital=self._capital_provider(),
            market_is_live=self._market_is_live(),
            market_stream=self._market_stream_provider(),
            universe=self._universe_provider(),
            target_symbol=self._target_provider(),
            symbol_risk_multipliers=self._exposure_multipliers_provider(),
        )
        if isinstance(request, ShadowStartRefusal):
            self.refused.emit(request.title, request.message)
            return
        engine = self._build_engine(request)
        try:
            # The lease is acquired *before* anything is published as running,
            # and it is shared with Paper: if Paper holds it, this raises and the
            # run does not start at all.
            self._lease.start()
            self._engine = engine
            self._snapshot = engine.start()
        except Exception as error:
            # Recover only what this layer owns.  The engine's own truth is left
            # alone -- a failed start must not be reported as a stopped session.
            if self._lease.active:
                self._lease.stop()
            self._engine = None
            self.refused.emit(START_FAILED_TITLE, str(error))
            return
        self._render_session()
        self.runtime_event_requested.emit(
            ShadowRuntimeEvent(
                severity="info",
                component=SHADOW_COMPONENT,
                code=SHADOW_START_CODE,
                message=START_EVENT_MESSAGE.format(
                    status=request.strategy_status,
                    symbol=request.target_symbol,
                    capital=queries.format_money(request.initial_cash),
                ),
            )
        )
        self.log_requested.emit(
            START_LOG_MESSAGE.format(
                symbol=request.target_symbol,
                semver=request.semver,
                capital=queries.format_money(request.initial_cash),
            )
        )

    def stop(self) -> None:
        """Stop a running simulation and publish the result.

        A no-op when nothing is running, and deliberately *not* a reset: the
        engine keeps its last snapshot and the stopped lease is the only thing
        released, exactly as before.
        """

        engine = self._engine
        if engine is None:
            return
        self._snapshot = engine.stop()
        if self._lease.active:
            self._lease.stop()
        self._render_session()
        self.runtime_event_requested.emit(
            ShadowRuntimeEvent(
                severity="info",
                component=SHADOW_COMPONENT,
                code=SHADOW_STOP_CODE,
                message=STOP_EVENT_MESSAGE,
            )
        )

    def shutdown(self) -> None:
        """Close-time teardown: stop the engine and release the lease.

        Deliberately *quieter* than :meth:`stop`, and deliberately not a
        re-publication.  Shutdown does not repaint, does not publish and does not
        record an event, because at this point there is no window left to paint
        and the runtime teardown reports itself -- which is what stops a close
        from writing a "stopped" event over a session the operator never stopped.

        The snapshot is likewise left as it stands.  The retired ``closeEvent``
        stopped the engine and discarded the result, so the last published
        snapshot was always the last one the feed produced; assigning the
        post-stop reading here would change what a reader of
        :attr:`snapshot` sees during teardown.
        """

        engine = self._engine
        if engine is None or not engine.active:
            return
        engine.stop()
        if self._lease.active:
            self._lease.stop()

    # -- ingress ---------------------------------------------------------

    def on_market_snapshot(self, stream: MarketSnapshot) -> None:
        """Feed one market snapshot to the running simulation.

        Nothing happens when no simulation runs: the bridge that calls this fans
        one market fact out to several capabilities, so it must stay a cheap
        no-op rather than becoming a second place that decides whether Shadow is
        up.  A snapshot change asks for the session repaint because the cards,
        positions and fills it draws are now stale -- and it does not repaint the
        evidence tables, because a market tick is a session event.
        """

        engine = self._engine
        if engine is None or not engine.active:
            return
        self._snapshot = engine.on_stream(stream)
        self._render_session()

    # -- internals -------------------------------------------------------

    def _build_engine(
        self, request: ShadowStartRequest
    ) -> ShadowPaperEngine:
        """Construct the engine from a frozen request; nothing is started here.

        This is where the run's *provenance* is composed, and it is composed last
        on purpose.  The capital amount was gated earlier, but the account the
        amount came from is only named here, so a start that is refused above
        never reads the portfolio at all -- which is the retired handler's exact
        ordering, preserved.
        """

        config: ShadowSimulationConfig = build_targeted_shadow_config(
            request.parameters,
            initial_cash=request.initial_cash,
            capital_source=queries.capital_source_text(
                self._account_alias_provider()
            ),
            daily_loss_limit=request.daily_loss_limit,
            symbol_risk_multipliers=request.risk_multipliers(),
        )
        return ShadowPaperEngine(
            store=self._store,
            allowed_symbols=(request.target_symbol,),
            config=config,
            strategy_version_id=request.strategy_version_id,
            parameter_hash=request.parameter_hash,
            target_symbol=request.target_symbol,
        )


__all__ = ["ShadowOrchestrator"]
