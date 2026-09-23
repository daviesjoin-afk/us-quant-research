"""Unit tests for ``ShadowOrchestrator``, driven entirely by fakes.

No Qt application, no real engine, no broker, no long-running simulation: the
store, the lease, the engine constructor and every provider are replaced, so each
test asserts the *sequencing* this class owns -- which dependency it calls, in
which order, and what it publishes -- rather than the shadow algorithm, which has
its own tests in ``test_shadow_paper.py``.

The behaviours pinned here are the ones the extraction could plausibly break:

* a refusal builds nothing and touches the engine not at all;
* a failing start rolls back this layer's own bookkeeping and does not fabricate
  a stopped session;
* ``shutdown`` is quieter than ``stop`` -- no event, no repaint;
* ``on_market_snapshot`` is a no-op when nothing runs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from us_quant.desktop_v2.orchestration.shadow import orchestrator as module
from us_quant.desktop_v2.orchestration.shadow.models import (
    SHADOW_COMPONENT,
    SHADOW_START_CODE,
    SHADOW_STOP_CODE,
    ShadowCapitalFact,
    ShadowRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.shadow.orchestrator import (
    ShadowOrchestrator,
)
from us_quant.trading.runtime.workflow_state import WorkflowStateError

from tests.test_desktop_shadow_queries import (  # noqa: F401 - fixtures reused
    _strategy,
    _stream,
    _universe,
)


class _Store:
    """A store that records what was asked of it and touches no disk."""

    def __init__(self) -> None:
        self.fill_limits: list[int] = []

    def recent_fills(self, limit: int = 200):
        self.fill_limits.append(limit)
        return ()


class _Lease:
    """The shared execution lease, modelling ``ShadowWorkflowController`` exactly.

    Both refusals the real controller raises are modelled, because both are
    behaviour this round must preserve: an already-active Shadow run refuses a
    second start, and a lease held by Paper refuses any start at all.

    ``refuse=True`` models the *second* case faithfully: the lease really is held
    (by Paper), so :attr:`active` is ``True`` while Shadow's ``start()`` raises.
    That distinction is the whole point of the guard it supports -- a failing
    caller must not release a lease that is active but was not its to take.
    """

    def __init__(self, *, refuse: bool = False) -> None:
        self.active = refuse
        self.starts = 0
        self.stops = 0
        self._refuse = refuse

    def start(self) -> None:
        if self._refuse:
            raise WorkflowStateError(
                "Cannot acquire SHADOW lease while PAPER is active."
            )
        if self.active:
            raise WorkflowStateError("Shadow workflow is already active.")
        self.starts += 1
        self.active = True

    def stop(self) -> None:
        if not self.active:
            raise WorkflowStateError("Shadow workflow is not active.")
        self.stops += 1
        self.active = False


class _Engine:
    """A stand-in engine that records lifecycle calls and owns a fake snapshot."""

    created: list["_Engine"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.active = False
        self.streams: list[object] = []
        _Engine.created.append(self)

    def start(self):
        self.active = True
        return {"active": True, "session_id": "session-1"}

    def stop(self):
        self.active = False
        return {"active": False, "session_id": "session-1"}

    def on_stream(self, stream):
        self.streams.append(stream)
        return {"active": True, "session_id": "session-1"}


class _Events:
    """Captures the three published channels."""

    def __init__(self) -> None:
        self.refusals: list[tuple[str, str]] = []
        self.events: list[ShadowRuntimeEvent] = []
        self.logs: list[str] = []


_USE_DEFAULT = object()


def _build(
    *,
    lease: _Lease | None = None,
    store: _Store | None = None,
    events: _Events | None = None,
    strategy=_USE_DEFAULT,
    capital=_USE_DEFAULT,
    market_live: bool = True,
    market_stream=_USE_DEFAULT,
    universe=_USE_DEFAULT,
    target: str = "AAPL",
    alias: str = "DU1234567",
    runtime_active: bool = False,
    multipliers=_USE_DEFAULT,
    rendered: list[str] | None = None,
) -> tuple[ShadowOrchestrator, _Lease, _Store, _Events, list[str]]:
    """An orchestrator wired to fakes, every gate satisfied unless overridden.

    ``_USE_DEFAULT`` rather than ``None`` for the fact arguments, because ``None``
    is itself a meaningful value here -- it is how a test says "no strategy" and
    drives the refusal path.
    """

    lease = lease if lease is not None else _Lease()
    store = store if store is not None else _Store()
    events = events if events is not None else _Events()
    paints = rendered if rendered is not None else []

    orchestrator = ShadowOrchestrator(
        store=store,  # type: ignore[arg-type]
        lease=lease,
        strategy_provider=lambda: (
            _strategy() if strategy is _USE_DEFAULT else strategy
        ),
        target_provider=lambda: target,
        capital_provider=lambda: (
            ShadowCapitalFact(net_liquidation=Decimal("10000"))
            if capital is _USE_DEFAULT
            else capital
        ),
        account_alias_provider=lambda: alias,
        market_stream_provider=lambda: (
            _stream() if market_stream is _USE_DEFAULT else market_stream
        ),
        market_is_live=lambda: market_live,
        universe_provider=lambda: (
            _universe("AAPL") if universe is _USE_DEFAULT else universe
        ),
        runtime_is_active=lambda: runtime_active,
        exposure_multipliers_provider=lambda: (
            {"AAPL": Decimal("1.5")} if multipliers is _USE_DEFAULT else multipliers
        ),
        render_session=lambda: paints.append("session"),
    )
    orchestrator.refused.connect(
        lambda title, message: events.refusals.append((title, message))
    )
    orchestrator.log_requested.connect(events.logs.append)
    orchestrator.runtime_event_requested.connect(events.events.append)
    return orchestrator, lease, store, events, paints


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch):
    """Patch the engine and its config builder where the module imports them."""

    _Engine.created.clear()
    monkeypatch.setattr(module, "ShadowPaperEngine", _Engine)
    monkeypatch.setattr(
        module, "build_targeted_shadow_config", lambda *a, **k: object()
    )


# -- intent → dependency calls -------------------------------------------


def test_a_valid_start_builds_one_engine_and_starts_it_once() -> None:
    orchestrator, lease, _store, _events, paints = _build()

    orchestrator.start()

    assert len(_Engine.created) == 1
    assert _Engine.created[0].active is True
    assert lease.starts == 1
    assert lease.active is True
    assert orchestrator.is_active is True
    assert paints == ["session"]


def test_the_engine_is_built_with_the_gated_target_and_strategy() -> None:
    """The run's inputs are the frozen request's, not re-read at build time."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()

    kwargs = _Engine.created[0].kwargs
    assert kwargs["allowed_symbols"] == ("AAPL",)
    assert kwargs["target_symbol"] == "AAPL"
    assert kwargs["strategy_version_id"] == "targeted-v1"
    assert kwargs["parameter_hash"] == "hash-v1"


def test_the_capital_provenance_names_the_account_at_build_time() -> None:
    """The provenance line is composed from the alias while the engine is built.

    The builder is spied on rather than the engine, because ``capital_source``
    travels in the *config*: asserting it on the engine's kwargs would prove
    nothing about what the run is recorded as.
    """

    captured: list[dict] = []

    def _capture(*args, **kwargs):
        captured.append(kwargs)
        return object()

    module.build_targeted_shadow_config = _capture  # type: ignore[assignment]
    orchestrator, _lease, _store, _events, _paints = _build(alias="DU9999999")
    orchestrator.start()

    assert captured[0]["capital_source"] == (
        "IBKR Paper DU9999999 NetLiquidation"
    )
    assert captured[0]["initial_cash"] == Decimal("10000")
    assert captured[0]["daily_loss_limit"] == Decimal("100")
    assert captured[0]["symbol_risk_multipliers"] == {"AAPL": Decimal("1.5")}


def test_a_start_publishes_one_event_and_one_log_line() -> None:
    orchestrator, _lease, _store, events, _paints = _build()

    orchestrator.start()

    assert [event.code for event in events.events] == [SHADOW_START_CODE]
    assert events.events[0].component == SHADOW_COMPONENT
    assert events.events[0].severity == "info"
    assert "AAPL" in events.events[0].message
    assert "$10,000.00" in events.events[0].message
    assert len(events.logs) == 1
    assert "AAPL" in events.logs[0]


# -- invalid request → dependency not called -----------------------------


def test_a_refused_start_builds_nothing_and_publishes_the_refusal() -> None:
    """The gate refuses before the lease is touched or an engine exists."""

    orchestrator, lease, _store, events, paints = _build(strategy=None)

    orchestrator.start()

    assert [title for title, _message in events.refusals] == ["请选择策略版本"]
    assert all(message for _title, message in events.refusals)
    assert _Engine.created == []
    assert lease.starts == 0
    assert paints == []
    assert orchestrator.is_active is False
    assert orchestrator.snapshot is None


def test_a_refused_start_never_reads_the_account_alias() -> None:
    """A refusing path must not touch the portfolio, as the retired one did not."""

    reads: list[str] = []
    orchestrator = ShadowOrchestrator(
        store=_Store(),  # type: ignore[arg-type]
        lease=_Lease(),
        strategy_provider=lambda: None,
        target_provider=lambda: "AAPL",
        capital_provider=lambda: None,
        account_alias_provider=lambda: reads.append("alias") or "DU1",
        market_stream_provider=lambda: _stream(),
        market_is_live=lambda: True,
        universe_provider=lambda: _universe("AAPL"),
        runtime_is_active=lambda: False,
        exposure_multipliers_provider=lambda: {},
        render_session=lambda: None,
    )

    orchestrator.start()

    assert reads == []


# -- success path --------------------------------------------------------


def test_the_snapshot_is_the_engines_own_value() -> None:
    """A reference to the engine's reading, never a recomputation."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()

    published = orchestrator.snapshot
    assert published is not None
    assert published["session_id"] == "session-1"
    # It is the value the engine returned from its own start, not a rebuilt one.
    assert published == {"active": True, "session_id": "session-1"}


def test_recent_fills_is_delegated_to_the_store() -> None:
    """The export reads through the capability; the store has one desktop reader."""

    store = _Store()
    orchestrator, _lease, _s, _events, _paints = _build(store=store)

    assert orchestrator.recent_fills(500) == ()
    assert store.fill_limits == [500]


# -- failure path --------------------------------------------------------


def test_a_lease_refusal_rolls_back_bookkeeping_and_propagates() -> None:
    """A start that cannot acquire execution is reported, not silently dropped.

    The lease is shared with Paper, so acquiring it while Paper holds it raises.
    What must happen: nothing is published as running, no truth is fabricated, and
    the operator is told.
    """

    lease = _Lease(refuse=True)
    orchestrator, _lease, _store, events, paints = _build(lease=lease)

    orchestrator.start()

    assert lease.starts == 0
    # The refusal is Paper's, so this call must not release Paper's lease either.
    assert lease.stops == 0
    assert orchestrator.is_active is False
    assert orchestrator.snapshot is None
    assert paints == []
    assert len(events.refusals) == 1
    assert events.refusals[0][0] == "内部影子仿真未启动"
    assert "PAPER" in events.refusals[0][1]
    # A later retry is possible once Paper gives the lease back: nothing was left
    # half-initialized by the refused attempt.
    lease._refuse = False
    lease.stop()  # Paper releases the execution lease
    orchestrator.start()
    assert orchestrator.is_active is True


def test_a_failed_start_never_releases_a_lease_it_did_not_acquire() -> None:
    """The root cause of the duplicate-start hole, pinned independently.

    The old failure path released ``self._lease`` whenever it was active, without
    asking whether *this call* had acquired it.  That is what let a second start
    hand back the first run's mutex.  Here the lease refuses because Paper holds
    it, so this call acquired nothing -- and must therefore release nothing, even
    though the lease is active.
    """

    lease = _Lease(refuse=True)
    orchestrator, _lease, _store, _events, _paints = _build(lease=lease)

    orchestrator.start()

    assert lease.active is True, "the lease belongs to Paper and stays held"
    assert lease.stops == 0, "a caller that acquired nothing must release nothing"


def test_a_stop_never_releases_a_lease_paper_took_afterwards() -> None:
    """The same bug class in ``stop()``, found by the guard that pins it.

    Shadow starts, stops (releasing its own lease), and leaves ``_engine`` on
    record because a stop publishes the engine's final snapshot rather than
    clearing the reference.  If Paper then takes the shared lease, a second
    ``stop()`` must not hand it back: Shadow holds nothing at that point, and
    releasing here would un-enforce Shadow XOR Paper for a *Paper* session.
    """

    orchestrator, lease, _store, _events, _paints = _build()
    orchestrator.start()
    orchestrator.stop()
    assert lease.active is False
    stops_after_shadow = lease.stops

    # Paper acquires the shared execution lease.
    lease.starts += 1
    lease.active = True

    orchestrator.stop()

    assert lease.active is True, "Paper's lease must survive Shadow's stop"
    assert lease.stops == stops_after_shadow, "Shadow released a lease it did not hold"


def test_shutdown_never_releases_a_lease_paper_took_afterwards() -> None:
    """The close-time path has the same obligation as ``stop()``."""

    orchestrator, lease, _store, _events, _paints = _build()
    orchestrator.start()
    engine = _Engine.created[0]
    orchestrator.stop()
    # A stop leaves the engine on record but inactive; simulate the window closing
    # after Paper has taken the lease.
    engine.active = True
    lease.starts += 1
    lease.active = True
    stops_before = lease.stops

    orchestrator.shutdown()

    assert lease.active is True
    assert lease.stops == stops_before


def test_a_failed_start_leaves_a_previously_running_session_untouched() -> None:
    """A refusal must not disturb the session that is already tracking.

    The engine's own start raising is the other way this path is reached.  The
    published truth and the tracked reference must survive it, and the lease must
    be handed back only because this call really did acquire it.
    """

    orchestrator, lease, _store, events, _paints = _build()
    orchestrator.start()
    first_engine = _Engine.created[0]
    orchestrator.stop()
    # Baseline *after* the clean start/stop pair, so the assertions below are about
    # the failing call only -- and the snapshot under test is the one a stop
    # published, which is the truth a failed start must leave alone.
    first_snapshot = orchestrator.snapshot
    starts_before = lease.starts
    stops_before = lease.stops

    class _ExplodingEngine(_Engine):
        def start(self):
            raise RuntimeError("engine refused")

    module.ShadowPaperEngine = _ExplodingEngine  # type: ignore[assignment]
    try:
        orchestrator.start()
    finally:
        module.ShadowPaperEngine = _Engine  # type: ignore[assignment]

    assert lease.starts == starts_before + 1
    assert lease.stops == stops_before + 1, (
        "this call acquired the lease, so it hands it back"
    )
    assert lease.active is False
    # The previous truth is intact: no fabricated stopped session, no lost engine.
    assert orchestrator.snapshot is first_snapshot
    assert first_engine.active is False
    assert events.refusals[0][0] == "内部影子仿真未启动"


def test_a_failed_start_does_not_report_a_stopped_session() -> None:
    """A failure must never be turned into a business verdict."""

    orchestrator, _lease, _store, events, _paints = _build(
        lease=_Lease(refuse=True)
    )

    orchestrator.start()

    assert [event.code for event in events.events] == []
    assert orchestrator.snapshot is None


def test_a_lease_acquired_then_failing_is_released() -> None:
    """If the engine's own start raises, the lease must not stay held."""

    lease = _Lease()
    orchestrator, _lease, _store, events, _paints = _build(lease=lease)

    class _ExplodingEngine(_Engine):
        def start(self):
            raise RuntimeError("engine refused")

    module.ShadowPaperEngine = _ExplodingEngine  # type: ignore[assignment]
    try:
        orchestrator.start()
    finally:
        module.ShadowPaperEngine = _Engine  # type: ignore[assignment]

    assert lease.starts == 1
    assert lease.stops == 1
    assert lease.active is False
    assert events.refusals[0][0] == "内部影子仿真未启动"
    assert "engine refused" in events.refusals[0][1]


# -- duplicate / stop / ingress behaviour --------------------------------


def test_a_second_start_preserves_the_running_session_and_lease() -> None:
    """A second start while running is a no-op, and must not break the mutex.

    This test replaces one that pinned the *opposite* -- inherited duplicate-start
    behaviour in which a second start built a second engine, was refused by the
    shared lease, and then released the **first** run's lease in its failure path.
    That left a session still trading with the Shadow/Paper mutex released, so
    Paper could acquire it: the exact invariant the shared ``ExecutionLeaseManager``
    exists to enforce, silently void.

    The round's spec says the old behaviour may be preserved where it is product
    semantics.  This is not product semantics -- it is a safety hole, and freezing
    it into the new canonical Shadow owner (with a test protecting it) would have
    made v2O-E harder and more dangerous.  So the contract is now:

        already active -> refuse/no-op -> no second engine, no lease traffic,
        no lost reference, no changed snapshot.
    """

    orchestrator, lease, _store, events, _paints = _build()

    orchestrator.start()
    first_engine = _Engine.created[0]
    first_snapshot = orchestrator.snapshot
    assert orchestrator.is_active is True
    assert lease.active is True

    orchestrator.start()

    # No second engine was built, and the lease saw no traffic at all.
    assert len(_Engine.created) == 1
    assert lease.starts == 1
    assert lease.stops == 0
    # The running session is still the tracked, published one.
    assert orchestrator.is_active is True
    assert orchestrator.snapshot is first_snapshot
    assert first_engine.active is True
    # And nothing was reported to the operator, because nothing went wrong.
    assert events.refusals == []


def test_repeated_starts_neither_leak_a_lease_nor_a_session() -> None:
    """Hammering the button must leave exactly one session holding one lease."""

    orchestrator, lease, _store, _events, _paints = _build()

    for _ in range(5):
        orchestrator.start()

    assert len(_Engine.created) == 1
    assert lease.starts == 1
    assert lease.stops == 0
    assert orchestrator.is_active is True
    assert lease.active is True


def test_a_second_start_after_stop_starts_a_fresh_session() -> None:
    """The gate is "already active", not "ever started": stop then start works."""

    orchestrator, lease, _store, _events, _paints = _build()

    orchestrator.start()
    orchestrator.stop()
    orchestrator.start()

    assert len(_Engine.created) == 2
    assert lease.starts == 2
    assert lease.active is True
    assert orchestrator.is_active is True


def test_stop_publishes_the_stop_event_and_repaints() -> None:
    orchestrator, lease, _store, events, paints = _build()
    orchestrator.start()
    events.events.clear()
    paints.clear()

    orchestrator.stop()

    assert orchestrator.is_active is False
    assert lease.active is False
    assert [event.code for event in events.events] == [SHADOW_STOP_CODE]
    assert paints == ["session"]


def test_stop_is_a_noop_when_nothing_runs() -> None:
    orchestrator, lease, _store, events, paints = _build()

    orchestrator.stop()

    assert lease.stops == 0
    assert events.events == []
    assert paints == []


def test_shutdown_stops_and_releases_without_publishing() -> None:
    """Close-time teardown is quieter: no event, no repaint, no log."""

    orchestrator, lease, _store, events, paints = _build()
    orchestrator.start()
    events.events.clear()
    events.logs.clear()
    paints.clear()

    orchestrator.shutdown()

    assert lease.active is False
    assert events.events == []
    assert events.logs == []
    assert paints == []


def test_shutdown_leaves_the_last_snapshot_alone() -> None:
    """The retired ``closeEvent`` discarded the stop result; so does this."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()
    published = orchestrator.snapshot

    orchestrator.shutdown()

    assert orchestrator.snapshot is published


def test_shutdown_is_a_noop_when_nothing_runs() -> None:
    orchestrator, lease, _store, _events, _paints = _build()

    orchestrator.shutdown()

    assert lease.stops == 0


def test_a_market_snapshot_reaches_the_running_engine() -> None:
    orchestrator, _lease, _store, _events, paints = _build()
    orchestrator.start()
    paints.clear()
    stream = _stream()

    orchestrator.on_market_snapshot(stream)

    assert _Engine.created[0].streams == [stream]
    assert paints == ["session"]


def test_a_market_snapshot_is_a_noop_without_a_run() -> None:
    """The bridge fans out unconditionally, so this must stay cheap."""

    orchestrator, _lease, _store, events, paints = _build()

    orchestrator.on_market_snapshot(_stream())

    assert _Engine.created == []
    assert paints == []
    assert events.events == []


def test_a_market_snapshot_is_a_noop_after_stop() -> None:
    orchestrator, _lease, _store, _events, paints = _build()
    orchestrator.start()
    orchestrator.stop()
    paints.clear()
    stream = _stream()

    orchestrator.on_market_snapshot(stream)

    assert _Engine.created[0].streams == []
    assert paints == []


def test_a_stopped_engine_is_not_fed_while_the_reference_remains() -> None:
    """A stale engine that stopped itself must not be fed either."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()
    engine = _Engine.created[0]
    engine.active = False

    orchestrator.on_market_snapshot(_stream())

    assert engine.streams == []


# -- no second truth ----------------------------------------------------


def test_is_active_asks_the_engine_rather_than_a_local_flag() -> None:
    """The engine is the state owner; a mirrored flag would be a second truth."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()

    _Engine.created[0].active = False
    assert orchestrator.is_active is False
    _Engine.created[0].active = True
    assert orchestrator.is_active is True


def test_the_orchestrator_stores_no_capital_or_strategy_truth() -> None:
    """It holds references and a snapshot, not a copy of anyone's state."""

    orchestrator, _lease, _store, _events, _paints = _build()
    orchestrator.start()

    for banned in ("_capital", "_strategy", "_target_symbol", "_universe"):
        assert not hasattr(orchestrator, banned), banned
