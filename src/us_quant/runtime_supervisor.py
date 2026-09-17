"""Framework-free runtime ownership for long-lived application resources.

The supervisor exists so the UI layer can hand over the *lifecycle* of
background resources -- timers, worker threads, stream services, broker
connections -- without the UI also owning the teardown bookkeeping.  It
imports no GUI toolkit, no broker client and performs no I/O, so it is
directly unit testable.

Design constraints (deliberate, kept small on purpose):

* a component is described by plain callables, not by an interface the
  caller must implement -- the desktop already holds ``QTimer``,
  ``QThread`` and service objects, and wrapping them in adapters would add
  code without adding safety;
* a resource the supervisor did not start is still released if its
  liveness probe says it is live, so "started before the supervisor
  existed" is not a leak;
* shutdown has two explicit phases -- :meth:`RuntimeSupervisor.begin_shutdown`
  raises the admission gate and signals the cancellable work, and
  :meth:`RuntimeSupervisor.shutdown` releases everything -- so a caller can
  refuse new work immediately while a long-running task is still finishing
  its writes;
* phase one is reversible and phase two is not.  :meth:`RuntimeSupervisor.cancel_shutdown`
  undoes a drain that released nothing, because a *refused* close (the
  operator is told to reconcile first) has to hand the client back in a
  usable state; once any resource has actually been released the drain can
  no longer be cancelled and the call raises rather than pretending a
  released runtime came back;
* shutdown never lets one failure skip the remaining components;
* only ``Exception`` is caught around component callables: a
  ``KeyboardInterrupt``/``SystemExit`` means the operator is aborting the
  process and must keep propagating;
* every failure is recorded on the component and surfaced through
  :meth:`RuntimeSupervisor.snapshot` instead of being swallowed;
* a resource with no ``stop``/``join`` can still register, so that "this
  resource has no owner" is *observable* rather than silently absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

# Tracked lifecycle states.  ``running``/``stopped`` mirror the last
# transition the supervisor drove unless a live ``is_running`` probe is
# supplied, in which case the probe wins.
STATE_REGISTERED = "registered"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeComponentSnapshot:
    """Immutable view of one managed resource."""

    name: str
    state: str
    last_error: str | None = None
    exit_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Immutable view of every managed resource."""

    components: tuple[RuntimeComponentSnapshot, ...]
    shutting_down: bool


@dataclass
class _Component:
    name: str
    order: int
    start: Callable[[], None] | None = None
    stop: Callable[[], None] | None = None
    join: Callable[[], object] | None = None
    is_running: Callable[[], bool] | None = None
    drain: bool = False
    state: str = STATE_REGISTERED
    started: bool = False
    released: bool = False
    last_error: str | None = None
    exit_ok: bool | None = None
    errors: list[str] = field(default_factory=list)


def _describe(error: BaseException) -> str:
    message = " ".join(str(error).split())
    name = type(error).__name__
    return f"{name}: {message}" if message else name


class RuntimeSupervisor:
    """Own the start/stop lifecycle of a fixed set of named resources.

    Registration order is shutdown order by default: register resources in
    the sequence they must be released.  Pass ``order`` to decouple the two
    when that reads more clearly; shutdown always walks ascending ``order``.
    """

    def __init__(self) -> None:
        self._components: dict[str, _Component] = {}
        self._shutting_down = False
        # Set as soon as the *release* pass is entered or any single component
        # is actually released.  From that point on phase one can no longer be
        # undone: a released timer/thread/connection cannot be conjured back,
        # so ``cancel_shutdown`` must refuse rather than pretend it can.
        self._release_entered = False

    # -- registration ---------------------------------------------------

    def register(
        self,
        name: str,
        *,
        start: Callable[[], None] | None = None,
        stop: Callable[[], None] | None = None,
        join: Callable[[], object] | None = None,
        is_running: Callable[[], bool] | None = None,
        order: int | None = None,
        drain: bool = False,
    ) -> None:
        """Register a resource under ``name``.

        ``start``/``stop``/``join`` are optional so an already-running or
        externally owned resource can be *observed* (via ``is_running``)
        without the supervisor pretending it can release it.

        ``drain`` marks the ``stop`` callable as a *request* to stop rather
        than a release: :meth:`begin_shutdown` calls it first, without
        joining, so a long-running task is asked to wind down while the
        caller is still waiting.  ``stop`` and ``drain`` are deliberately
        separate: for a Qt timer ``stop`` really is the release, while for a
        worker thread it is only a cancel signal.
        """

        if self._shutting_down:
            raise RuntimeError(
                "runtime supervisor is shutting down; no new component may "
                f"be registered: {name}"
            )
        if not name:
            raise ValueError("runtime component name must not be empty")
        if name in self._components:
            raise ValueError(f"runtime component is already registered: {name}")
        self._components[name] = _Component(
            name=name,
            order=len(self._components) if order is None else int(order),
            start=start,
            stop=stop,
            join=join,
            is_running=is_running,
            drain=drain,
        )

    @property
    def names(self) -> tuple[str, ...]:
        """Registered names, in shutdown order."""

        return tuple(component.name for component in self._in_order())

    def is_registered(self, name: str) -> bool:
        return name in self._components

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    # -- single component operations ------------------------------------

    def start(self, name: str) -> None:
        """Start one resource.  A failure is recorded *and* re-raised."""

        component = self._require(name)
        if self._shutting_down:
            raise RuntimeError(
                f"runtime supervisor is shutting down; refusing to start {name}"
            )
        if component.start is None:
            raise RuntimeError(f"runtime component has no start callable: {name}")
        try:
            component.start()
        except Exception as error:
            self._record_error(component, error, phase="start")
            component.state = STATE_FAILED
            component.exit_ok = False
            raise
        component.started = True
        component.state = STATE_RUNNING
        component.exit_ok = None

    def stop(self, name: str) -> None:
        """Stop one resource.  A failure is recorded *and* re-raised."""

        component = self._require(name)
        self._release_entered = True
        self._release(component, tolerate=False)

    # -- bulk operations -------------------------------------------------

    def start_all(self) -> None:
        """Start every registered resource in ascending ``order``.

        Stops at the first failure: a half-started stack must not be
        silently presented as running.
        """

        for component in self._in_order():
            if component.start is None or component.started:
                continue
            self.start(component.name)

    def begin_shutdown(self) -> RuntimeSnapshot:
        """Phase one: refuse new work and signal cancellable work to stop.

        Called the moment the operator asks to close, while a long-running
        task may still be finishing its writes.  It raises the admission
        gate, drains every component registered with ``drain=True`` (a
        *request* to stop, never a join), and deliberately releases nothing
        else: the caller still has to keep the close event ignored until the
        workers exit on their own.

        Safe to call repeatedly -- it only ever signals, so calling it again
        after the admission gate is already down is a no-op for the
        components that have nothing left to signal.
        """

        self._shutting_down = True
        for component in self._in_order():
            if not component.drain:
                continue
            action = component.stop
            if action is None:
                continue
            try:
                action()
            except Exception as error:  # noqa: BLE001 - recorded, never fatal
                self._record_error(component, error, phase="drain")
        return self.snapshot()

    def cancel_shutdown(self) -> None:
        """Undo phase one after the close request was *refused*.

        A refused close is not an aborted shutdown: the operator is told to
        reconcile first and then keeps using the client, so the admission
        gate has to come back up.  The Paper recovery path runs through the
        same gated task entry point as ordinary work, and leaving the gate
        down would lock it out permanently -- halted, unable to reconcile,
        unable to finalize, unable to exit.

        This restores *admission only*.  It starts nothing, restarts nothing,
        creates no thread, touches no broker and performs no I/O; a drain
        component's ``stop`` is a cancel request, so undoing it is purely
        bookkeeping.  It is therefore only legal while nothing has actually
        been released.

        Raises:
            RuntimeError: if the release pass has already run.  A released
                timer, thread or connection cannot be restored, and silently
                reporting success would hand the caller a runtime that looks
                open but is half torn down.
        """

        if self._release_entered:
            raise RuntimeError(
                "cannot cancel a shutdown that already released resources"
            )
        self._shutting_down = False

    def shutdown(self) -> RuntimeSnapshot:
        """Release every live resource, isolating each failure.

        Safe to call repeatedly.  A component that was released cleanly is
        skipped on later calls, while one whose ``stop``/``join`` failed is
        *retried* -- the close path ignores the close event when a thread is
        still alive and depends on the next attempt making progress, so a
        cached "already shut down" verdict would deadlock the application.
        A component whose ``stop`` or ``join`` raises never prevents the
        remaining components from being released, and its error stays
        visible in the returned snapshot.

        Entering this method ends phase one for good: it is the point of no
        return, so a later :meth:`cancel_shutdown` refuses.
        """

        self._shutting_down = True
        self._release_entered = True
        for component in self._in_order():
            self._release(component, tolerate=True)
        return self.snapshot()

    def snapshot(self) -> RuntimeSnapshot:
        """Current state of every managed resource, in shutdown order."""

        return RuntimeSnapshot(
            components=tuple(
                RuntimeComponentSnapshot(
                    name=component.name,
                    state=self._state_of(component),
                    last_error=component.last_error,
                    exit_ok=component.exit_ok,
                )
                for component in self._in_order()
            ),
            shutting_down=self._shutting_down,
        )

    def errors(self) -> tuple[str, ...]:
        """Every error seen, as ``name: message``, oldest first."""

        return tuple(
            f"{component.name}: {message}"
            for component in self._in_order()
            for message in component.errors
        )

    # -- internals -------------------------------------------------------

    def _in_order(self) -> Iterable[_Component]:
        return sorted(self._components.values(), key=lambda item: item.order)

    def _require(self, name: str) -> _Component:
        component = self._components.get(name)
        if component is None:
            raise KeyError(f"runtime component is not registered: {name}")
        return component

    def _state_of(self, component: _Component) -> str:
        if component.state == STATE_FAILED:
            return STATE_FAILED
        # Once released, the supervisor's own verdict is final; a probe that
        # still reports "live" afterwards is exactly the leak worth showing.
        if component.released:
            return component.state
        if component.is_running is not None:
            try:
                running = bool(component.is_running())
            except Exception as error:
                self._record_error(component, error, phase="probe")
                return STATE_FAILED
            return STATE_RUNNING if running else STATE_STOPPED
        # No probe: report what the supervisor itself drove.  ``registered``
        # therefore means "known, never started, no way to ask".
        if component.started:
            return STATE_RUNNING
        return STATE_REGISTERED

    def _is_live(self, component: _Component) -> bool:
        """Whether a component still holds a resource that must be released.

        A cleanly released component is done for good.  One whose release
        *failed* is live again so the next ``shutdown`` retries it.  A
        component the supervisor started is always released.  One with no
        probe is also released: liveness is unknown, so fail closed and let
        the attempt report the truth.  Only an explicit "not running" probe
        lets a component be skipped.
        """

        if component.released and component.exit_ok is True:
            return False
        if component.started:
            return True
        if component.is_running is None:
            return True
        try:
            return bool(component.is_running())
        except Exception as error:
            self._record_error(component, error, phase="probe")
            return True

    def _release(self, component: _Component, *, tolerate: bool) -> None:
        """Run ``stop`` then ``join`` for one live component.

        ``tolerate`` is the whole point of the bulk path: during shutdown an
        exception must be recorded and swallowed *for that component only*,
        because the remaining resources still have to be released.
        """

        if not self._is_live(component):
            return
        failed = False
        for label, action in (("stop", component.stop), ("join", component.join)):
            if action is None:
                continue
            try:
                result = action()
            except Exception as error:
                self._record_error(component, error, phase=label)
                failed = True
                if not tolerate:
                    component.started = False
                    component.released = True
                    component.state = STATE_FAILED
                    component.exit_ok = False
                    raise
                continue
            # A ``join``-style callable reports failure by returning False
            # (``QThread.wait`` semantics) rather than by raising.
            if label == "join" and result is False:
                error = RuntimeError("join reported the resource did not exit")
                self._record_error(component, error, phase=label)
                failed = True
                if not tolerate:
                    component.started = False
                    component.released = True
                    component.state = STATE_FAILED
                    component.exit_ok = False
                    raise error
        component.started = False
        component.released = True
        component.exit_ok = not failed
        component.state = STATE_FAILED if failed else STATE_STOPPED

    def _record_error(
        self, component: _Component, error: BaseException, *, phase: str | None = None
    ) -> None:
        message = _describe(error)
        if phase is not None:
            message = f"{phase} failed: {message}"
        component.last_error = message
        component.errors.append(message)
