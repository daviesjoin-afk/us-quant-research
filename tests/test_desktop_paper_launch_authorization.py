from __future__ import annotations

from types import SimpleNamespace

from us_quant.desktop import MainWindow
from us_quant.trading.domain.paper_autonomy import PaperAutonomyError
from us_quant.trading.runtime.preflight import PaperLaunchAuthorization


class _Intent:
    def __init__(self, allowed: bool) -> None:
        self.allows_autonomous_work = allowed


class _Application:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls = 0

    def snapshot(self) -> object:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _Execution:
    def __init__(self) -> None:
        self.confirmations: list[bool] = []

    def preflight_with_confirmation(self, *, paper_confirmed: bool) -> bool:
        self.confirmations.append(paper_confirmed)
        return paper_confirmed

    @property
    def current_preflight(self) -> str:
        return "manual-preflight"


def _window(application: _Application) -> SimpleNamespace:
    return SimpleNamespace(
        paper_autonomy_application=application,
        execution_orchestrator=_Execution(),
    )


def test_manual_preflight_does_not_read_autonomy_intent() -> None:
    application = _Application(_Intent(False))
    window = _window(application)

    result = MainWindow._paper_launch_preflight(
        window, PaperLaunchAuthorization.MANUAL
    )

    assert result == "manual-preflight"
    assert application.calls == 0
    assert window.execution_orchestrator.confirmations == []


def test_autonomous_preflight_reads_a1_fresh_for_every_pass() -> None:
    application = _Application(_Intent(False), _Intent(True))
    window = _window(application)

    first = MainWindow._paper_launch_preflight(
        window, PaperLaunchAuthorization.AUTONOMOUS
    )
    second = MainWindow._paper_launch_preflight(
        window, PaperLaunchAuthorization.AUTONOMOUS
    )

    assert (first, second) == (False, True)
    assert application.calls == 2
    assert window.execution_orchestrator.confirmations == [False, True]


def test_unreadable_a1_refuses_autonomous_confirmation() -> None:
    application = _Application(PaperAutonomyError("unreadable"))
    window = _window(application)

    result = MainWindow._paper_launch_preflight(
        window, PaperLaunchAuthorization.AUTONOMOUS
    )

    assert result is False
    assert application.calls == 1
    assert window.execution_orchestrator.confirmations == [False]
