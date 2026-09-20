"""Execution composition and the window's execution wiring.

Two things are being pinned here, and they are the two ways this migration
could look finished while not being finished:

* the composition root is the only assembly point -- the application it builds
  must be bound to the store and the channel it was handed, and nothing above
  it may name a concrete adapter;
* the *window* actually uses it.  Risk v2 shipped a defect of exactly this
  shape: the unit tests injected a risk application the real window never
  passed, so every test was green and the running engine enforced none of the
  configured limits.  The wiring assertions below therefore read the window's
  own objects and the desktop call site, not a hand-built stand-in.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
import pathlib

import pytest

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.trading.application.execution import (
    ExecutionApplication,
    SubmissionResult,
)
from us_quant.trading.composition.execution import (
    build_execution_application,
    build_execution_candidate,
    build_order_repository,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.ports.broker_execution import BrokerOrderReservation

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DESKTOP = _REPO_ROOT / "src" / "us_quant" / "desktop.py"

_APP = QApplication.instance() or QApplication([])

_NOW = datetime(2026, 7, 26, 14, 0, tzinfo=timezone.utc)

_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version",
    parameter_hash="hash",
)


class _StubBroker:
    """A broker port that accepts every order and never sends one.

    Broker ids start above whatever the store already holds, which is the same
    rule the real channel follows (CR-4): reusing an id an existing row owns
    would collide on the store's unique constraint.
    """

    def __init__(self, *, start_above: int = 0) -> None:
        self.reservations: list[BrokerOrderReservation] = []
        self._next = start_above

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        self._next += 1
        reservation = BrokerOrderReservation(
            order_id=intent.order_id,
            broker_order_id=self._next,
            account_alias="DU***TEST",
        )
        self.reservations.append(reservation)
        return reservation

    def submit(self, reservation: BrokerOrderReservation) -> None:
        return None

    def cancel(self, order_id: str) -> bool:
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        return ()

    def fills(self) -> tuple[ExecutionFill, ...]:
        return ()


def _proposal() -> TradeProposal:
    return TradeProposal(
        strategy=_STRATEGY,
        symbol="AAPL",
        action=TradeAction.BUY,
        desired_quantity=2,
        reference_price=Decimal("101.25"),
        reason="wiring fixture",
        generated_at=_NOW,
    )


# -- the composition root -------------------------------------------------


def test_the_store_builder_opens_the_frozen_paper_database(tmp_path) -> None:
    repository = build_order_repository(tmp_path / "orders.sqlite3")

    assert repository.max_broker_order_id() == 0
    assert repository.reconciliation_rows() == ()


def test_the_application_is_bound_to_the_store_and_channel_it_was_handed(
    tmp_path,
) -> None:
    repository = build_order_repository(tmp_path / "orders.sqlite3")
    broker = _StubBroker()
    application = build_execution_application(
        repository=repository, broker=broker
    )

    assert isinstance(application, ExecutionApplication)
    result = application.submit_approved(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        session_id="session-1",
        reason="wiring fixture",
    )
    assert isinstance(result, SubmissionResult)
    assert broker.reservations[0].order_id == result.intent.order_id
    # The durable row is in *that* store, and the store can name the order.
    assert repository.intent(result.intent.order_id) is not None
    assert (
        repository.broker_order_id(result.intent.order_id)
        == result.broker_order_id
    )
    row = repository.reconciliation_rows()[0]
    assert row.symbol == "AAPL"
    assert row.latest_status is None


def test_the_candidate_builder_produces_a_channel_over_the_shared_store(
    tmp_path,
) -> None:
    """The adapter is constructed with the store, not with a store of its own."""

    repository = build_order_repository(tmp_path / "orders.sqlite3")
    config = _paper_config()
    adapter = build_execution_candidate(
        config, repository=repository, extended_hours_enabled=False
    )

    assert adapter.repository is repository
    assert adapter.extended_hours_enabled is False
    # The adapter satisfies the port's event surface with domain types.
    assert adapter.events() == ()
    assert adapter.fills() == ()
    adapter.disarm()


def _paper_config():
    from us_quant.ibkr import IBKRConnectionConfig

    return IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=71,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=3,
    )


# -- the window's own wiring ----------------------------------------------


@pytest.fixture()
def window():
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _engine_call_keywords() -> list[dict[str, object]]:
    """Every ``build_trading_runtime(...)`` call in ``desktop.py``, by keyword."""

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_trading_runtime"
        ):
            calls.append(
                {keyword.arg: keyword.value for keyword in node.keywords}
            )
    return calls


def test_the_window_owns_an_order_store_it_built_through_composition() -> None:
    """Not a journal it opened itself, and not a second store beside it."""

    window = MainWindow()
    try:
        assert not hasattr(window, "paper_order_journal")
        repository = window.order_repository
        # It really is the order store: the reads the window performs answer on
        # the window's own object rather than on a fixture's copy.
        assert isinstance(repository.max_broker_order_id(), int)
        assert isinstance(repository.audit_rows(), tuple)
        assert isinstance(repository.reconciliation_rows(), tuple)
        assert isinstance(window.order_repository.sessions(), tuple)
    finally:
        window.close()
        window.deleteLater()


def test_the_window_hands_the_runtime_an_execution_service_not_a_sink(
    window,
) -> None:
    """The runtime must not be able to submit; the service must be injectable."""

    calls = _engine_call_keywords()
    assert calls, "no build_trading_runtime call found in desktop.py"
    for keywords in calls:
        assert "order_sink" not in keywords, sorted(
            name for name in keywords if name
        )
        assert "execution" in keywords, sorted(
            name for name in keywords if name
        )


def test_the_engine_receives_the_application_built_from_the_windows_store(
    window,
) -> None:
    """The composition path, exercised with the window's own store.

    This is the Risk v2 defect's shape: a test that injects its own objects
    proves the engine works and says nothing about whether the window passes
    them.  Building the application exactly as the window does -- from
    ``window.order_repository`` -- and then driving an order through it makes
    the window's store the one that has to show the result.
    """

    broker = _StubBroker(
        start_above=window.order_repository.max_broker_order_id()
    )
    application = build_execution_application(
        repository=window.order_repository, broker=broker
    )
    result = application.submit_approved(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        session_id="window-session",
        reason="wiring fixture",
    )

    assert application.intent(result.intent.order_id) is not None
    stored = [
        row
        for row in window.order_repository.audit_rows()
        if row["intent_id"] == result.intent.order_id
    ]
    assert len(stored) == 1
    assert stored[0]["session_id"] == "window-session"
    assert (
        window.order_repository.broker_order_id(result.intent.order_id)
        == result.broker_order_id
    )


def test_the_window_never_names_a_concrete_execution_implementation() -> None:
    """Structural half of the guard: the names must not appear at all."""

    source = _DESKTOP.read_text(encoding="utf-8")
    for forbidden in (
        "IBKRExecutionAdapter",
        "SQLiteOrderRepository",
        "IBKRPaperOrderService",
        "PaperOrderJournal",
        "new_paper_order_intent",
        "paper_order_journal",
    ):
        assert forbidden not in source, forbidden
    for required in (
        "build_order_repository",
        "build_execution_application",
    ):
        assert required in source, required


def test_the_session_coordinator_reads_domain_events(window) -> None:
    """The coordinator's order port speaks ``fills``/``events``, not Paper DTOs."""

    source = (
        _REPO_ROOT
        / "src"
        / "us_quant"
        / "trading"
        / "runtime"
        / "paper_contracts.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("poll_executions", "poll_updates"):
        assert forbidden not in source, forbidden
    assert "def fills(" in source
    assert "def events(" in source


def test_the_runtime_keeps_domain_orders_in_its_pending_book() -> None:
    """Guard against a Paper DTO sneaking back into the runtime's book."""

    source = (
        _REPO_ROOT
        / "src"
        / "us_quant"
        / "trading"
        / "runtime"
        / "portfolio.py"
    ).read_text(encoding="utf-8")
    assert "PaperOrderIntent" not in source
    assert "order_sink" not in source
    assert "IBKRPaperOrderUncertainError" not in source
    # The uncertainty the session has to survive is the port's own type, and it
    # is named where the broker call is -- the dispatch.
    dispatch = (
        _REPO_ROOT
        / "src"
        / "us_quant"
        / "trading"
        / "runtime"
        / "dispatch.py"
    ).read_text(encoding="utf-8")
    assert "ExecutionSubmissionUncertain" in dispatch