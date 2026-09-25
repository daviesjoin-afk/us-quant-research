"""Behaviour tests for the G2-A strategy governance orchestration.

The subject is ``StrategyGovernanceOrchestrator`` -- the one owner of the
desktop strategy governance sequencing.  The tests pin, against a fake
application and the real page:

* the refresh sequence (read once, paint once, announce once) and the failure
  path that fakes nothing;
* the clone adapter's refusals and its exact success semantics;
* the transition's promotion-gate refusal and its exact success semantics
  (one ``STATUS_CHANGE`` event, routed -- never written -- by the window);
* the governance *view* selection: it publishes the account notice and never
  touches the runtime selection.

The window-level fan-out test lives at the bottom: which routes refill after
a catalogue change is composition, so that wiring is pinned on the real
window.
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.desktop_v2.orchestration.strategy import (  # noqa: E402
    StrategyGovernanceOrchestrator,
)
from us_quant.desktop_v2.pages.strategy import StrategyPage  # noqa: E402
from us_quant.trading.application.strategies import (  # noqa: E402
    StrategyApplicationError,
    StrategyNotFoundError,
)
from us_quant.trading.application.strategy_selection import (  # noqa: E402
    StrategySelectionPurpose,
)
from us_quant.trading.domain.strategy import (  # noqa: E402
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)

_APP = None

_NOW = datetime(2025, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

#: The G2-A governance capability.  Announcing a catalogue change is its job;
#: deciding which routes refill from it is composition's, so the package must
#: never name the execution route at all.
_GOVERNANCE_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "orchestration"
    / "strategy"
)


def _governance_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(_GOVERNANCE_DIR.glob("*.py"))
    )


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _version(
    strategy_id: str = "buy-hold",
    semver: str = "1.0.0-research",
    status: StrategyStatus = StrategyStatus.RESEARCH,
    gate_passed: bool = False,
    gate_reason: str = "尚未通过研究晋级门",
    parameters: dict | None = None,
) -> StrategyVersion:
    """One immutable version, matching the domain's construction rules."""

    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name="Buy & Hold",
            description="test seed",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=f"{strategy_id}-{semver}",
            parameter_hash="p" * 16,
        ),
        semver=semver,
        status=status,
        mode=StrategyMode.RESEARCH,
        parameters=parameters if parameters is not None else {"whole_shares": True},
        universe_hash="u" * 18,
        code_hash="c" * 12,
        risk_budget_pct=Decimal("0.10"),
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        created_at=_NOW,
        updated_at=_NOW + timedelta(minutes=1),
    )


class CountingPage(StrategyPage):
    """The real page, counting ``render`` calls for the sequence tests."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.render_calls: list[int] = []

    def render(self, versions) -> None:  # noqa: D102 - see StrategyPage
        self.render_calls.append(len(versions))
        super().render(versions)


class FakeApplication:
    """Duck-typed ``StrategyApplication`` that records every call.

    ``select`` exists as a trap: the governance orchestrator must never call
    the runtime selection service, and a fake that raises makes any slip a
    test failure instead of a silent coupling.
    """

    def __init__(self, versions=()) -> None:
        self._versions = tuple(versions)
        self.list_calls = 0
        self.get_calls: list[str] = []
        self.clone_calls: list[tuple] = []
        self.transition_calls: list[tuple] = []
        self.clone_error: Exception | None = None
        self.transition_error: Exception | None = None

    def list_versions(self):
        self.list_calls += 1
        return self._versions

    def get_version(self, version_id):
        self.get_calls.append(version_id)
        for version in self._versions:
            if version.version_id == version_id:
                return version
        raise StrategyNotFoundError(version_id)

    def clone_version(self, version_id, *, semver, parameters):
        self.clone_calls.append((version_id, semver, dict(parameters)))
        if self.clone_error is not None:
            raise self.clone_error
        created = _version(
            semver=semver,
            parameters=dict(parameters),
        )
        self._versions = self._versions + (created,)
        return created

    def transition(self, version_id, target_status, *, reason):
        self.transition_calls.append((version_id, target_status, reason))
        if self.transition_error is not None:
            raise self.transition_error
        source = self.get_version(version_id)
        moved = _version(
            strategy_id=source.strategy_id,
            semver=source.semver,
            status=StrategyStatus(target_status),
            gate_passed=source.gate_passed,
            gate_reason=source.gate_reason,
            parameters=dict(source.parameters),
        )
        self._versions = tuple(
            moved if v.version_id == moved.version_id else v
            for v in self._versions
        )
        return moved

    def select(self, *args, **kwargs):  # pragma: no cover - the trap
        raise AssertionError("governance touched the runtime selection")


class Recorder:
    """Signal spy: appends every emission under a label."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.warnings: list[tuple[str, str]] = []
        self.notices: list[str] = []
        self.events: list[object] = []
        self.catalog: list[None] = []

    def connect(self, orchestrator) -> None:
        orchestrator.log_requested.connect(self.log.append)
        orchestrator.warning_requested.connect(
            lambda title, message: self.warnings.append((title, message))
        )
        orchestrator.account_notice_requested.connect(
            self.notices.append
        )
        orchestrator.runtime_event_requested.connect(self.events.append)
        orchestrator.catalog_changed.connect(lambda: self.catalog.append(None))


@pytest.fixture()
def page():
    _qapp()
    widget = CountingPage()
    yield widget
    widget.deleteLater()


def _orchestrator(application, page) -> tuple:
    orchestrator = StrategyGovernanceOrchestrator(
        application=application, page=page
    )
    recorder = Recorder()
    recorder.connect(orchestrator)
    return orchestrator, recorder


# -- A / B: the refresh sequence -----------------------------------------


def test_refresh_reads_paints_and_announces_exactly_once(page) -> None:
    versions = (_version(), _version(strategy_id="dual-ma-trend"))
    application = FakeApplication(versions)
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.refresh()

    assert application.list_calls == 1
    assert page.render_calls == [2]
    assert len(recorder.catalog) == 1


def test_a_catalogue_read_failure_logs_and_fakes_nothing(page) -> None:
    application = FakeApplication()
    application.list_versions = (
        lambda: (_ for _ in ()).throw(
            StrategyApplicationError("磁盘打不开")
        )
    )
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.refresh()

    assert recorder.log == ["策略目录读取失败：磁盘打不开"]
    assert page.render_calls == []
    assert recorder.catalog == []


# -- C / D / E / F: the clone adapter ------------------------------------


def test_an_invalid_json_clone_is_refused_and_calls_nothing(page) -> None:
    application = FakeApplication((_version(),))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.clone("buy-hold-1.0.0-research", "1.0.1-research", "{not json")

    assert application.clone_calls == []
    assert recorder.warnings and recorder.warnings[-1][0] == "创建失败"
    assert recorder.warnings[-1][1].startswith("参数不是合法 JSON")
    assert application.list_calls == 0
    assert recorder.catalog == []


def test_a_non_object_json_clone_is_refused(page) -> None:
    application = FakeApplication((_version(),))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.clone("buy-hold-1.0.0-research", "1.0.1-research", "[1, 2]")

    assert application.clone_calls == []
    assert recorder.warnings == [("创建失败", "参数必须是 JSON 对象")]
    assert application.list_calls == 0


def test_a_refused_clone_publishes_no_success(page) -> None:
    application = FakeApplication((_version(),))
    application.clone_error = StrategyApplicationError("版本号已存在")
    orchestrator, recorder = _orchestrator(application, page)
    application.list_calls = 99  # sentinel: no refresh may happen

    orchestrator.clone("buy-hold-1.0.0-research", "1.0.1-research", "{}")

    assert recorder.warnings == [("创建失败", "版本号已存在")]
    assert application.list_calls == 99
    assert recorder.catalog == []
    assert not any("已创建" in line for line in recorder.log)


def test_a_successful_clone_refreshes_and_logs_exactly(page) -> None:
    application = FakeApplication((_version(),))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.clone(
        "buy-hold-1.0.0-research",
        "1.0.1-research",
        '{"whole_shares": false}',
    )

    assert application.clone_calls == [
        ("buy-hold-1.0.0-research", "1.0.1-research", {"whole_shares": False})
    ]
    # Exactly one refresh: the success path repaints once.  There was no
    # read before the clone (this test never called ``refresh``), so a count
    # of two would mean the orchestrator re-reads somewhere it must not.
    assert application.list_calls == 1
    assert len(recorder.catalog) == 1
    assert recorder.log == [
        "已创建 buy-hold 1.0.1-research；状态回到研究，需重新验证"
    ]
    assert recorder.events == []


# -- G / H: the transition ----------------------------------------------


def test_a_blocked_transition_publishes_no_runtime_event(page) -> None:
    application = FakeApplication((_version(),))
    application.transition_error = StrategyApplicationError(
        "research gate blocked: 尚未通过研究晋级门"
    )
    orchestrator, recorder = _orchestrator(application, page)
    application.list_calls = 99  # sentinel: no refresh may happen

    orchestrator.transition("buy-hold-1.0.0-research", "paper_shadow")

    assert recorder.warnings == [
        (
            "晋级门阻断",
            "research gate blocked: 尚未通过研究晋级门\n\n自动下单仍保持关闭。",
        )
    ]
    assert recorder.log == [
        "策略状态变更被阻断：research gate blocked: 尚未通过研究晋级门"
    ]
    assert recorder.events == []
    assert application.list_calls == 99


def test_a_successful_transition_changes_status_exactly_once(page) -> None:
    application = FakeApplication((_version(),))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.transition("buy-hold-1.0.0-research", "stopped")

    assert application.transition_calls == [
        ("buy-hold-1.0.0-research", "stopped", "desktop governance action")
    ]
    assert application.list_calls == 1
    assert len(recorder.catalog) == 1
    assert recorder.log == ["buy-hold 已变更为 stopped"]
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.severity == "info"
    assert event.component == "strategy"
    assert event.code == "STATUS_CHANGE"
    assert event.message == "buy-hold 1.0.0-research -> stopped"


# -- I / J: the governance view selection --------------------------------


def test_viewing_a_version_publishes_a_notice_and_never_selects(page) -> None:
    shadow = _version(
        strategy_id="intraday-targeted-t",
        semver="1.3.0-research",
    )
    application = FakeApplication((shadow,))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.select_version("intraday-targeted-t-1.3.0-research")

    assert application.get_calls == ["intraday-targeted-t-1.3.0-research"]
    assert recorder.notices == [
        (
            "探索性影子模式：已绑定 intraday-targeted-t "
            "1.3.0-research。可收集实时模拟证据；"
            "不代表晋级，不会发送券商订单。"
        )
    ]
    # The trap: ``FakeApplication.select`` raises if the governance path ever
    # reaches the runtime selection service.  Reaching this line means the
    # governance view selection never did.


def test_a_missing_selected_version_is_a_silent_no_op(page) -> None:
    application = FakeApplication((_version(),))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.select_version("vanished-version")

    assert recorder.notices == []
    assert recorder.warnings == []


def test_a_gate_passed_paper_shadow_version_publishes_the_second_notice(
    page,
) -> None:
    paper = _version(
        semver="2.0.0-paper",
        status=StrategyStatus.PAPER_SHADOW,
        gate_passed=True,
        gate_reason="",
    )
    application = FakeApplication((paper,))
    orchestrator, recorder = _orchestrator(application, page)

    orchestrator.select_version("buy-hold-2.0.0-paper")
    assert "策略证据门：通过" in recorder.notices[0]

    blocked = _version(semver="3.0.0-research", gate_passed=False)
    application._versions = (blocked,)
    orchestrator.select_version("buy-hold-3.0.0-research")
    assert "策略证据门：硬阻断" in recorder.notices[1]
    assert "尚未通过研究晋级门" in recorder.notices[1]


# -- K: the window-level catalogue fan-out -------------------------------


def test_a_catalogue_change_fans_out_to_every_refilling_route(
    monkeypatch, tmp_path
) -> None:
    """The window's remaining role: route the announcement, decide nothing.

    G2-B moved the execution half of this fan-out: the deleted window seam
    ``_sync_execution_strategy_options`` is
    ``ExecutionOrchestrator.refresh_strategy_options`` now, which refills the
    combo from the selection service and repaints the preflight itself.  The
    pin moved to the new owner; the claim -- all three routes refill, exactly
    once each, and governance never learns that Execution exists -- did not.
    """

    from us_quant.desktop import MainWindow  # noqa: PLC0415
    from us_quant.paths import STATE_ROOT_ENV  # noqa: PLC0415

    app = _qapp()
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    window = MainWindow()
    app.processEvents()
    try:
        calls: list[str] = []
        monkeypatch.setattr(
            window.backtest_orchestrator,
            "refresh_strategy_options",
            lambda: calls.append("backtest"),
        )
        monkeypatch.setattr(
            window.targeted_session_orchestrator,
            "refresh_strategy_options",
            lambda: calls.append("targeted"),
        )
        monkeypatch.setattr(
            window.execution_orchestrator,
            "refresh_strategy_options",
            lambda: calls.append("execution"),
        )

        window.strategy_governance_orchestrator.refresh()
        app.processEvents()

        assert calls.count("backtest") == 1
        assert calls.count("targeted") == 1
        assert calls.count("execution") == 1

        # Governance publishes the fact and names no consumer: naming Execution
        # is how the capability would acquire a route it may not know about.
        governance = _governance_source()
        for token in (
            "ExecutionOrchestrator",
            "execution_orchestrator",
            "orchestration.execution",
        ):
            assert token not in governance, token
    finally:
        window.close()
        window.deleteLater()
