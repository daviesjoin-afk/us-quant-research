from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from us_quant.desktop_v2.orchestration.autonomy.host import PaperAutonomyHost


_APP = QCoreApplication.instance() or QCoreApplication([])


class _Supervisor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def tick(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return "tick result"


def test_host_starts_and_stops_its_timer():
    host = PaperAutonomyHost(_Supervisor(), tick_interval_seconds=17)

    host.start()
    assert host._timer.isActive()
    assert host._timer.interval() == 17000
    host.stop()
    assert not host._timer.isActive()


def test_tick_calls_supervisor_once_and_emits_completion():
    supervisor = _Supervisor()
    host = PaperAutonomyHost(supervisor, tick_interval_seconds=30)
    completed = []
    host.tick_completed.connect(completed.append)

    host._tick()

    assert supervisor.calls == 1
    assert completed == ["tick result"]


def test_tick_failure_emits_generic_error_without_retry(caplog):
    supervisor = _Supervisor(error=RuntimeError("sensitive owner detail"))
    host = PaperAutonomyHost(supervisor, tick_interval_seconds=30)
    failed = []
    host.tick_failed.connect(failed.append)

    host._tick()

    assert supervisor.calls == 1
    assert failed == [
        "Paper autonomy tick failed before a safe decision could be completed"
    ]
    assert "sensitive owner detail" not in failed[0]
