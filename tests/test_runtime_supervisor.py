"""Contract tests for the framework-free runtime supervisor.

These tests intentionally import no GUI toolkit, no broker client and touch
no network or database: the supervisor's whole value is that teardown
bookkeeping is testable on its own.
"""

from __future__ import annotations

import pytest

from us_quant.runtime_supervisor import (
    STATE_FAILED,
    STATE_REGISTERED,
    STATE_RUNNING,
    STATE_STOPPED,
    RuntimeSnapshot,
    RuntimeSupervisor,
)


class RecordingResource:
    """Minimal stand-in for a timer/thread/service with observable effects."""

    def __init__(self, name: str, *, log: list[str] | None = None) -> None:
        self.name = name
        self.log = log if log is not None else []
        self.running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0
        self.stop_error: BaseException | None = None
        self.join_result: object = True

    def start(self) -> None:
        self.start_calls += 1
        self.running = True
        self.log.append(f"{self.name}:start")

    def stop(self) -> None:
        self.stop_calls += 1
        self.log.append(f"{self.name}:stop")
        if self.stop_error is not None:
            raise self.stop_error
        self.running = False

    def join(self) -> object:
        self.join_calls += 1
        self.log.append(f"{self.name}:join")
        return self.join_result

    def is_running(self) -> bool:
        return self.running


def _register(supervisor: RuntimeSupervisor, resource: RecordingResource) -> None:
    supervisor.register(
        resource.name,
        start=resource.start,
        stop=resource.stop,
        join=resource.join,
        is_running=resource.is_running,
    )


# -- normal lifecycle ----------------------------------------------------


def test_register_start_shutdown_round_trip() -> None:
    log: list[str] = []
    resource = RecordingResource("worker", log=log)
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)

    assert supervisor.names == ("worker",)
    assert supervisor.is_registered("worker")
    assert not supervisor.shutting_down

    supervisor.start("worker")
    assert resource.start_calls == 1
    assert resource.running

    result = supervisor.shutdown()
    assert resource.stop_calls == 1
    assert resource.join_calls == 1
    assert not resource.running
    assert log == ["worker:start", "worker:stop", "worker:join"]

    assert isinstance(result, RuntimeSnapshot)
    assert result.shutting_down
    (component,) = result.components
    assert component.name == "worker"
    assert component.state == STATE_STOPPED
    assert component.exit_ok is True
    assert component.last_error is None


def test_start_all_skips_components_without_a_start_callable() -> None:
    observed = RecordingResource("observed")
    supervisor = RuntimeSupervisor()
    supervisor.register("observed", is_running=observed.is_running)

    supervisor.start_all()

    assert observed.start_calls == 0
    assert supervisor.snapshot().components[0].state == STATE_STOPPED


def test_snapshot_reports_live_state_from_the_probe() -> None:
    """With a probe, liveness wins: the probe is the only thing that knows
    whether the underlying timer/thread is actually live."""

    resource = RecordingResource("stream")
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)

    assert supervisor.snapshot().components[0].state == STATE_STOPPED

    resource.running = True
    assert supervisor.snapshot().components[0].state == STATE_RUNNING

    resource.running = False
    assert supervisor.snapshot().components[0].state == STATE_STOPPED


def test_state_without_a_probe_reflects_what_the_supervisor_drove() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("blind")

    assert supervisor.snapshot().components[0].state == STATE_REGISTERED


# -- shutdown ordering ---------------------------------------------------


def test_shutdown_releases_components_in_registration_order() -> None:
    log: list[str] = []
    supervisor = RuntimeSupervisor()
    for name in ("first", "second", "third"):
        resource = RecordingResource(name, log=log)
        _register(supervisor, resource)
        supervisor.start(name)

    supervisor.shutdown()

    stops = [entry for entry in log if entry.endswith(":stop")]
    assert stops == ["first:stop", "second:stop", "third:stop"]


def test_explicit_order_decouples_shutdown_sequence_from_registration() -> None:
    log: list[str] = []
    supervisor = RuntimeSupervisor()
    late = RecordingResource("late", log=log)
    early = RecordingResource("early", log=log)
    supervisor.register("late", stop=late.stop, join=late.join, order=20)
    supervisor.register("early", stop=early.stop, join=early.join, order=10)

    assert supervisor.names == ("early", "late")
    supervisor.shutdown()

    assert log == ["early:stop", "early:join", "late:stop", "late:join"]


# -- failure isolation ---------------------------------------------------


def test_one_failing_stop_does_not_prevent_the_remaining_components() -> None:
    log: list[str] = []
    supervisor = RuntimeSupervisor()
    first = RecordingResource("a", log=log)
    broken = RecordingResource("b", log=log)
    broken.stop_error = RuntimeError("stream refused to close")
    last = RecordingResource("c", log=log)
    for resource in (first, broken, last):
        _register(supervisor, resource)
        supervisor.start(resource.name)

    result = supervisor.shutdown()

    # A failing ``stop`` must not skip that component's own ``join``: the
    # thread may still be alive and is exactly what needs joining.
    assert log == [
        "a:start",
        "b:start",
        "c:start",
        "a:stop",
        "a:join",
        "b:stop",
        "b:join",
        "c:stop",
        "c:join",
    ]
    states = {component.name: component for component in result.components}
    assert states["a"].state == STATE_STOPPED
    assert states["b"].state == STATE_FAILED
    assert states["c"].state == STATE_STOPPED
    assert states["b"].last_error is not None
    assert "stream refused to close" in states["b"].last_error
    assert states["b"].exit_ok is False
    assert states["c"].exit_ok is True


def test_failing_join_continues_to_later_components_and_records_the_error() -> None:
    log: list[str] = []
    supervisor = RuntimeSupervisor()
    stuck = RecordingResource("stuck", log=log)
    stuck.join_result = False
    later = RecordingResource("later", log=log)
    for resource in (stuck, later):
        _register(supervisor, resource)
        supervisor.start(resource.name)

    result = supervisor.shutdown()

    assert "later:stop" in log
    states = {component.name: component for component in result.components}
    assert states["stuck"].state == STATE_FAILED
    assert states["stuck"].exit_ok is False
    assert "did not exit" in (states["stuck"].last_error or "")
    assert states["later"].state == STATE_STOPPED


def test_join_failure_is_not_hidden_when_only_the_join_is_available() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("join-only", join=lambda: False, is_running=lambda: True)

    result = supervisor.shutdown()

    (component,) = result.components
    assert component.state == STATE_FAILED
    assert component.exit_ok is False


def test_stop_returns_false_is_not_treated_as_failure() -> None:
    """Only ``join`` uses a return value as a verdict; ``stop`` may return anything."""

    supervisor = RuntimeSupervisor()
    supervisor.register(
        "timer", stop=lambda: False, is_running=lambda: True
    )

    result = supervisor.shutdown()

    (component,) = result.components
    assert component.state == STATE_STOPPED
    assert component.exit_ok is True


# -- errors are recorded, never swallowed --------------------------------


def test_shutdown_records_every_error_and_exposes_them_in_order() -> None:
    supervisor = RuntimeSupervisor()
    for name in ("a", "b"):
        supervisor.register(
            name,
            stop=_raiser(f"{name} boom"),
            is_running=lambda: True,
        )

    supervisor.shutdown()

    assert supervisor.errors() == ("a: stop failed: RuntimeError: a boom",
                                   "b: stop failed: RuntimeError: b boom")


def test_snapshot_keeps_the_last_error_after_a_failed_shutdown() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("x", stop=_raiser("nope"), is_running=lambda: True)

    snapshot = supervisor.shutdown()

    (component,) = snapshot.components
    assert component.state == STATE_FAILED
    assert component.last_error == "stop failed: RuntimeError: nope"


def test_probe_failure_is_reported_as_failed_not_as_running() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("bad-probe", is_running=_raiser("probe exploded"))

    (component,) = supervisor.snapshot().components

    assert component.state == STATE_FAILED
    assert "probe exploded" in (component.last_error or "")


def test_a_failing_probe_fails_closed_and_still_attempts_release() -> None:
    calls: list[str] = []
    supervisor = RuntimeSupervisor()
    supervisor.register(
        "flaky",
        stop=lambda: calls.append("stop"),
        is_running=_raiser("cannot tell"),
    )

    supervisor.shutdown()

    assert calls == ["stop"]


def test_single_stop_raises_to_the_caller_and_marks_the_component_failed() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("solo", stop=_raiser("boom"), is_running=lambda: True)

    with pytest.raises(RuntimeError, match="boom"):
        supervisor.stop("solo")

    (component,) = supervisor.snapshot().components
    assert component.state == STATE_FAILED
    assert component.exit_ok is False


def test_start_failure_is_recorded_and_re_raised() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("worker", start=_raiser("cannot start"))

    with pytest.raises(RuntimeError, match="cannot start"):
        supervisor.start("worker")

    (component,) = supervisor.snapshot().components
    assert component.state == STATE_FAILED
    assert component.last_error == "start failed: RuntimeError: cannot start"


def test_start_all_stops_at_the_first_failure() -> None:
    started: list[str] = []
    supervisor = RuntimeSupervisor()
    supervisor.register("first", start=lambda: started.append("first"))
    supervisor.register("second", start=_raiser("second refused"))
    supervisor.register("third", start=lambda: started.append("third"))

    with pytest.raises(RuntimeError, match="second refused"):
        supervisor.start_all()

    assert started == ["first"]


# -- idempotency ---------------------------------------------------------


def test_repeated_shutdown_is_safe_and_does_not_rerun_teardown() -> None:
    resource = RecordingResource("worker")
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)
    supervisor.start("worker")

    first = supervisor.shutdown()
    second = supervisor.shutdown()
    third = supervisor.shutdown()

    assert resource.stop_calls == 1
    assert resource.join_calls == 1
    assert first == second == third
    assert second.shutting_down


def test_shutdown_without_registered_components_is_safe() -> None:
    supervisor = RuntimeSupervisor()

    snapshot = supervisor.shutdown()

    assert snapshot.components == ()
    assert snapshot.shutting_down


def test_a_failed_release_is_retried_while_the_resource_is_still_live() -> None:
    """The close path ignores the close event when a thread is still alive and
    relies on the next attempt making progress, so a failure must not be
    cached as "already shut down"."""

    attempts: list[int] = []

    def stop() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt failed")

    supervisor = RuntimeSupervisor()
    supervisor.register("stream", stop=stop, is_running=lambda: True)

    first = supervisor.shutdown()
    assert attempts == [1]
    assert first.components[0].state == STATE_FAILED

    second = supervisor.shutdown()

    assert attempts == [1, 2]
    assert second.components[0].exit_ok is True


def test_a_failed_release_is_not_retried_once_the_resource_is_gone() -> None:
    """Once the probe reports the resource is no longer live there is nothing
    left to release, so the failed attempt is not repeated."""

    attempts: list[int] = []
    state = {"live": True}

    def stop() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt failed")

    supervisor = RuntimeSupervisor()
    supervisor.register("stream", stop=stop, is_running=lambda: state["live"])

    supervisor.shutdown()
    assert attempts == [1]

    state["live"] = False
    supervisor.shutdown()

    assert attempts == [1]


def test_a_cleanly_released_component_is_not_released_again() -> None:
    resource = RecordingResource("worker")
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)
    supervisor.start("worker")

    supervisor.shutdown()
    supervisor.shutdown()

    assert resource.stop_calls == 1
    assert resource.join_calls == 1


def test_shutdown_releases_a_resource_started_outside_the_supervisor() -> None:
    """The desktop starts its heartbeat timers in ``_build_ui``; the
    supervisor must still be able to release them."""

    resource = RecordingResource("heartbeat")
    resource.running = True
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)

    result = supervisor.shutdown()

    assert resource.stop_calls == 1
    assert result.components[0].state == STATE_STOPPED


def test_shutdown_skips_a_component_that_was_never_live() -> None:
    resource = RecordingResource("never-started")
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)

    supervisor.shutdown()

    assert resource.stop_calls == 0
    assert resource.join_calls == 0


# -- registration guards -------------------------------------------------


def test_registration_after_shutdown_is_refused() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.shutdown()

    with pytest.raises(RuntimeError, match="shutting down"):
        supervisor.register("late")


def test_start_after_shutdown_is_refused() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("worker", start=lambda: None)
    supervisor.shutdown()

    with pytest.raises(RuntimeError, match="shutting down"):
        supervisor.start("worker")


def test_duplicate_and_empty_names_are_refused() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("worker", stop=lambda: None)

    with pytest.raises(ValueError, match="already registered"):
        supervisor.register("worker", stop=lambda: None)
    with pytest.raises(ValueError, match="must not be empty"):
        supervisor.register("")


def test_operations_on_an_unknown_component_raise_key_error() -> None:
    supervisor = RuntimeSupervisor()

    with pytest.raises(KeyError, match="missing"):
        supervisor.start("missing")
    with pytest.raises(KeyError, match="missing"):
        supervisor.stop("missing")


def test_starting_a_component_without_a_start_callable_is_refused() -> None:
    supervisor = RuntimeSupervisor()
    supervisor.register("observe-only", is_running=lambda: False)

    with pytest.raises(RuntimeError, match="no start callable"):
        supervisor.start("observe-only")


# -- immutability of the reported view -----------------------------------


def test_snapshots_are_frozen_and_do_not_alias_internal_state() -> None:
    resource = RecordingResource("worker")
    supervisor = RuntimeSupervisor()
    _register(supervisor, resource)
    supervisor.start("worker")

    before = supervisor.snapshot()
    supervisor.shutdown()
    after = supervisor.snapshot()

    assert before.components[0].state == STATE_RUNNING
    assert after.components[0].state == STATE_STOPPED
    with pytest.raises(Exception):
        before.components[0].state = STATE_FAILED  # type: ignore[misc]


def _raiser(message: str):
    def _raise() -> None:
        raise RuntimeError(message)

    return _raise
