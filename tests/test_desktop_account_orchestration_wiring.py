"""Real ``MainWindow`` wiring for the v2O-B account orchestration extraction.

The orchestrator's own tests prove it decides correctly; these prove the
*window* is still listening.  The extraction moved the account truth out of
``MainWindow``, and the failure mode it could hide is a signal nobody
connected -- a dashboard card that never repaints, a preflight that never
refreshes, a shell badge that never promotes -- which is invisible to a test
that only drives the orchestrator.

Nothing here reaches IBKR.  The portfolio is handed to the application the way
a successful refresh would, and the window's own bridges are driven directly.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.runtime.artifacts import AutoQuantSnapshot


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _portfolio() -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias="DU***67",
            net_liquidation=Decimal("12345.67"),
            cash=Decimal("5000"),
            available_funds=Decimal("4000"),
            buying_power=Decimal("8000"),
            gross_position_value=Decimal("2000"),
            excess_liquidity=Decimal("3000"),
            maintenance_margin=Decimal("1000"),
            cushion=Decimal("0.75"),
            daily_pnl=Decimal("-12.50"),
            unrealized_pnl=Decimal("30.25"),
            realized_pnl=Decimal("-4"),
            observed_at=NOW,
            pnl_source="IBKR reqPnL",
        ),
        positions=(),
    )


@pytest.fixture()
def window(monkeypatch, tmp_path):
    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


# -- the route table -----------------------------------------------------


def test_the_account_route_is_the_native_page(window) -> None:
    assert window.shell.page("account") is window.account_page


def test_the_window_composes_the_orchestrator_with_its_own_services(
    window,
) -> None:
    """Composition, not ownership: the window builds it and then stays out."""

    assert window.account_orchestrator is not None
    # The orchestrator is handed the window's own application and ledger.
    assert window.account_orchestrator._application is window.broker_account
    assert window.account_orchestrator._ledger is window.account_ledger
    assert window.account_orchestrator._page is window.account_page


def test_the_page_intents_land_on_the_orchestrator(window) -> None:
    """The refresh button must reach ``request_refresh``, not a window handler.

    The connection is left exactly as the window made it -- the test does not
    disconnect and re-connect, because re-connecting would *create* the wiring
    under test and pass even if the window had wired the button to something
    else entirely.  What is replaced is the orchestrator's own method, so the
    click travels the real signal the window built.
    """

    seen: list[int] = []
    original = window.account_orchestrator.request_refresh
    window.account_orchestrator.request_refresh = lambda: seen.append(1)
    try:
        window.account_page.refresh_button.click()
    finally:
        window.account_orchestrator.request_refresh = original

    assert seen == [1]


def test_the_refresh_signal_is_connected_to_the_orchestrator(window) -> None:
    """The wiring itself, asserted on the window's own connect call.

    ``AccountPage.refresh_requested`` has exactly one receiver after the
    window is built, and it is the orchestrator's entry point -- not a window
    handler, and not a second slot that would double-fire the read.
    """

    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    source = (root / "src" / "us_quant" / "desktop.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    connects: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "connect"):
            continue
        target = ast.unparse(func.value)
        if target == "self.account_page.refresh_requested":
            connects.append(ast.unparse(node.args[0]))

    assert connects == ["self.account_orchestrator.request_refresh"], connects


def test_the_window_has_no_account_runtime_attribute(window) -> None:
    """The ownership the extraction removed, asserted on the live object."""

    for name in (
        "account_portfolio",
        "_refresh_account_snapshot",
        "_account_snapshot_finished",
        "_refresh_account_surfaces",
        "_paper_simulation_capital",
    ):
        assert not hasattr(window, name), name


# -- single truth --------------------------------------------------------


def test_the_orchestrator_portfolio_is_the_application_portfolio(window) -> None:
    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio

    assert window.account_orchestrator.portfolio is portfolio
    assert window.account_orchestrator.portfolio is window.broker_account.portfolio


# -- refresh success fan-out --------------------------------------------


def test_a_successful_refresh_promotes_the_shell_badges(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    assert window.handshake_badge.text() == "协议 · 已握手"
    assert window.handshake_badge.toolTip() == "最近一次账户刷新握手成功"
    assert window.handshake_badge.property("state") == "ok"
    assert window.account_badge.text() == "Paper · DU***67"
    assert window.account_badge.property("state") == "ok"


def test_a_successful_refresh_does_not_touch_the_market_badge(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    before_text = window.market_badge.text()
    before_state = window.market_badge.property("state")

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    assert window.market_badge.text() == before_text
    assert window.market_badge.property("state") == before_state


def test_a_successful_refresh_repaints_the_dashboard_card(
    window, monkeypatch
) -> None:
    """Read the rendered fact, not "was the callback called"."""

    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    card = window.dashboard_page._net_liquidation_card
    assert card.value_label.text() == "$12,345.67"


def test_a_successful_refresh_records_a_runtime_event(
    window, monkeypatch, tmp_path
) -> None:
    """``Account -> System`` is a request, and the window must honour it."""

    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)
    _APP.processEvents()

    table = window.runtime_events_page.table
    codes = {
        table.item(row, 4).text() for row in range(table.rowCount())
    }
    assert "SNAPSHOT_OK" in codes


def test_a_successful_refresh_refreshes_both_preflights(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)

    calls: list[str] = []
    monkeypatch.setattr(
        window, "_refresh_auto_quant_preflight", lambda: calls.append("auto")
    )
    monkeypatch.setattr(
        window, "_refresh_target_preflight", lambda: calls.append("target")
    )

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    assert set(calls) == {"auto", "target"}


# -- the fresh Paper capital bridge -------------------------------------


def test_the_fresh_paper_capital_reads_the_canonical_portfolio(
    window,
) -> None:
    """The window's Paper/Shadow sizing now reads the orchestrator."""

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio

    assert (
        window.account_orchestrator.fresh_paper_net_liquidation(now=NOW)
        == Decimal("12345.67")
    )


def test_the_fresh_paper_capital_is_none_without_a_portfolio(window) -> None:
    window.broker_account._portfolio = None
    assert window.account_orchestrator.fresh_paper_net_liquidation(now=NOW) is None


# -- the targeted preflight reads the canonical account ---------------


def test_the_targeted_preflight_reads_the_canonical_account(
    window, monkeypatch
) -> None:
    """Spec 39/20: the targeted preflight must see the newly published account.

    It reads ``account_orchestrator.portfolio.account`` and passes it to
    ``evaluate_target_preflight``.  Asserting on the *result* object rather
    than on "the callback ran" is what makes this meaningful: a window still
    holding its own ``account_portfolio`` -- or one that passed ``None`` --
    would leave ``account_net_liquidation`` unset while the call-count test
    passed.
    """

    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window._refresh_target_preflight()

    result = window.target_preflight_result
    assert result is not None
    assert result.account_net_liquidation == portfolio.account.net_liquidation


# -- the execution route reads the same truth ---------------------------


def _session_snapshot() -> AutoQuantSnapshot:
    """A minimal live session snapshot, so the runtime route renders."""

    return AutoQuantSnapshot(
        session_id="session-1",
        active=True,
        strategy_version_id="version-1",
        parameter_hash="hash-1",
        candidate_count=0,
        initial_equity=Decimal("10000"),
        estimated_cash=Decimal("10000"),
        estimated_equity=Decimal("10000"),
        estimated_realized_pnl=Decimal("0"),
        estimated_unrealized_pnl=Decimal("0"),
        positions=(),
        fills=(),
        intents=(),
        pending_orders=(),
        trades_today=0,
        trading_day="2026-09-21",
        status="running",
        observed_at="2026-09-21T12:00:00+00:00",
    )


def test_a_successful_refresh_moves_the_execution_route_equity_card(
    window, monkeypatch
) -> None:
    """Spec 39: the execution route's rendered account fact follows the read.

    ``_render_auto_quant_snapshot`` projects ``account_orchestrator.portfolio``
    into the execution page's equity card.  Reading that card is the end-to-end
    check: a window that still kept its own ``account_portfolio`` -- or that
    handed the presenter a stale copy -- would leave the card on the old value
    while every callback-level assertion still passed.
    """

    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    window.auto_quant_snapshot = _session_snapshot()
    window.broker_account._portfolio = None
    window._render_auto_quant_snapshot()
    before = window.execution_page.equity_card.value_label.text()

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    after = window.execution_page.equity_card.value_label.text()
    assert after != before
    assert after == "$12,345.67"


def test_a_successful_refresh_puts_a_row_in_the_real_ledger_table(
    window, monkeypatch
) -> None:
    """Read the rendered table, not "append was called".

    ``AccountOrchestrator`` appends and then reads the points back for the
    account it appended, so the page's ledger table is the end-to-end fact: an
    append that happened but was never rendered, or a read that used the wrong
    environment/alias, is invisible to a call-count assertion and visible here.
    """

    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    table = window.account_page.ledger_table
    assert table.rowCount() == 1
    assert table.item(0, 2).text() == "DU***67"
    assert table.item(0, 3).text() == "$12,345.67"


def test_a_failed_refresh_preserves_the_last_good_truth(
    window, monkeypatch
) -> None:
    """Spec 36: a failed read must not clear, replace or re-date the truth.

    This drives a *real* failing refresh through
    ``BrokerAccountApplication.refresh`` -- the adapter factory raises -- so
    the assertion is on the account safety semantic itself rather than on a
    description of it.  What must not happen: the page cleared, the portfolio
    replaced with ``None``, the good snapshot re-dated, or a fake ledger point
    appended.  The orchestrator is never told about the failure, so its only
    job here is to have changed nothing.
    """

    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    good = _portfolio()
    window.broker_account._portfolio = good
    window.account_orchestrator._refresh_succeeded(good)
    rows_before = window.account_page.ledger_table.rowCount()
    observed_before = good.account.observed_at

    def exploding_adapter():
        raise RuntimeError("socket refused")

    monkeypatch.setattr(
        window.broker_account, "_adapter_factory", exploding_adapter
    )
    with pytest.raises(RuntimeError):
        window.broker_account.refresh(timeout_seconds=20)

    # The application kept the last good truth, unchanged and not re-dated.
    assert window.broker_account.portfolio is good
    assert window.broker_account.portfolio.account.observed_at == observed_before
    assert "socket refused" in (window.broker_account.last_error or "")

    # And the account route was not touched: no fake ledger point, no cleared
    # page, and the orchestrator still delegates to the same object.
    assert window.account_page.ledger_table.rowCount() == rows_before
    assert window.account_page.portfolio is good
    assert window.account_orchestrator.portfolio is good
    assert window.account_orchestrator.fresh_paper_net_liquidation(
        now=NOW
    ) == Decimal("12345.67")


# -- the export reads the canonical truth -------------------------------


def test_the_terminal_export_reads_the_canonical_portfolio(
    window, monkeypatch, tmp_path
) -> None:
    """Spec 40: the export must not rebuild a portfolio from page or ledger."""

    seen: dict = {}

    def recorder(*args, **kwargs):
        seen["kwargs"] = kwargs
        return tmp_path / "terminal-1.zip"

    monkeypatch.setattr("us_quant.desktop.export_terminal_bundle", recorder)
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information", lambda *args, **kwargs: None
    )

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window._export_terminal_state()

    assert seen["kwargs"]["portfolio"] is portfolio


# -- the line-count delta ----------------------------------------------


#: The commit this round is measured against.  Recorded as the **full** SHA
#: on purpose: ``git fetch <sha>`` refuses a short one ("couldn't find remote
#: ref"), so a shallow CI checkout could never resolve it and the guard would
#: fail there while passing locally -- which is exactly what happened the first
#: time this was written.
ACCOUNT_ORCHESTRATION_BASE_COMMIT = (
    "99049f09318f1eb8246f1160b933dcaf4d06229f"
)


def _base_desktop_source(root) -> str:
    """Read ``desktop.py`` at the v2O-B base commit, fetching if shallow.

    Mirrors the repo's existing base-commit helper: a shallow CI checkout does
    not contain the commit, so it is fetched rather than skipped -- a skipped
    line-count guard would silently stop guarding.
    """

    import subprocess

    for attempt in (0, 1):
        result = subprocess.run(
            [
                "git",
                "show",
                f"{ACCOUNT_ORCHESTRATION_BASE_COMMIT}:src/us_quant/desktop.py",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        if attempt == 0:
            subprocess.run(
                [
                    "git",
                    "fetch",
                    "--depth",
                    "1",
                    "-q",
                    "origin",
                    ACCOUNT_ORCHESTRATION_BASE_COMMIT,
                ],
                cwd=root,
                capture_output=True,
                check=False,
            )
    pytest.fail(
        f"base commit {ACCOUNT_ORCHESTRATION_BASE_COMMIT} is unreachable"
    )


def test_the_account_extraction_net_reduced_the_window() -> None:
    """Spec 43: ownership moved, so ``desktop.py`` must keep shrinking.

    Measured against the commit the round started from.  The threshold is a
    *net decrease*, not the spec's "~100+ lines" estimate: this capability's
    cross-workflow bridges (``_on_account_portfolio_changed``,
    ``_render_account_shell_health``, ``_record_account_runtime_event``,
    ``_publish_account_presentation_inputs``) are required to stay on the
    window, so the real delta is small.  The guard exists to catch the failure
    mode where the extraction *copies* the account code to the new module and
    leaves it behind -- that would show up as a net increase.
    """

    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    before = len(_base_desktop_source(root).splitlines())
    after = len(
        (root / "src" / "us_quant" / "desktop.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert after < before, f"desktop.py went {before} -> {after}"

    # And the capability really has its own home: the orchestrator is a
    # substantial module rather than an empty shim beside a copy.
    account = root / "src" / "us_quant" / "desktop_v2" / "orchestration" / "account"
    account_lines = sum(
        len((account / name).read_text(encoding="utf-8").splitlines())
        for name in ("__init__.py", "models.py", "queries.py", "orchestrator.py")
    )
    assert account_lines >= 400, account_lines
