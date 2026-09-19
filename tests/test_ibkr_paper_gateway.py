"""The IBKR Paper callback bridge.

The bridge has one job: forward IBKR callbacks to the order service. These
tests pin that it forwards exactly once with the arguments intact, that a stale
epoch is dropped before any business handler runs, and that the module itself
stays free of trading, persistence and UI dependencies.

Structural checks read the AST rather than the raw text: the docstring names
the forbidden modules on purpose, to record that the bridge does not import
them.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from us_quant.trading.adapters.ibkr import execution_gateway
from us_quant.trading.adapters.ibkr.execution_gateway import (
    IBKRPaperGatewayError,
    PaperGatewayHandshake,
    create_paper_gateway_app,
)

SOURCE = Path(execution_gateway.__file__ or "").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


class RecordingSink:
    """A sink that records every forwarded callback verbatim."""

    def __init__(self, *, current: bool = True) -> None:
        self.current = current
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def gateway_is_current(self, app: object, epoch: int) -> bool:
        self.calls.append(("gateway_is_current", (app, epoch)))
        return self.current

    def __getattr__(self, name: str):
        if not name.startswith("gateway_"):
            raise AttributeError(name)

        def record(*args: object) -> None:
            self.calls.append((name, args))

        return record


def _ibapi_stub() -> dict[str, ModuleType]:
    """A minimal fake ibapi so the bridge can be built without the real API."""

    class FakeEClient:
        def __init__(self, wrapper=None) -> None:
            self.wrapper = wrapper

        def run(self) -> None:
            raise AssertionError("the bridge must never be started by a test")

    client_module = ModuleType("ibapi.client")
    client_module.EClient = FakeEClient
    wrapper_module = ModuleType("ibapi.wrapper")
    wrapper_module.EWrapper = type("FakeEWrapper", (), {})
    ibapi_module = ModuleType("ibapi")
    return {
        "ibapi": ibapi_module,
        "ibapi.client": client_module,
        "ibapi.wrapper": wrapper_module,
    }


def _build(sink: RecordingSink, epoch: int = 7):
    with patch.dict(sys.modules, _ibapi_stub()):
        return create_paper_gateway_app(sink=sink, epoch=epoch)


def _forwarded(sink: RecordingSink) -> list[tuple[str, tuple[object, ...]]]:
    return [call for call in sink.calls if call[0] != "gateway_is_current"]


# --------------------------------------------------------------- isolation


def test_gateway_module_imports_without_the_ibkr_api() -> None:
    """Importing the bridge must not require the official IBKR package."""

    assert "ibapi" not in sys.modules
    assert execution_gateway.create_paper_gateway_app is not None


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_gateway_imports_no_trading_persistence_or_ui_module() -> None:
    imported = _imported_modules(TREE)

    for forbidden in (
        "us_quant.paper_order_journal",
        "us_quant.paper_order_models",
        "us_quant.ibkr_paper_orders",
        "us_quant.desktop",
        "us_quant.paper_workflow",
        "us_quant.paper_session",
        "us_quant.paper_trading_service",
        "us_quant.workflow_state",
        "us_quant.risk",
        "us_quant.auto_quant",
        "sqlite3",
        "PySide6",
    ):
        assert forbidden not in imported, forbidden


def test_gateway_never_imports_another_us_quant_module() -> None:
    """The bridge sits below the adapter and depends on nothing of ours."""

    for name in _imported_modules(TREE):
        assert not name.startswith("us_quant"), name


def test_gateway_has_no_top_level_ibapi_import() -> None:
    """``ibapi`` must be imported inside the factory only."""

    for node in TREE.body:
        if isinstance(node, ast.Import):
            assert all(
                not alias.name.startswith("ibapi") for alias in node.names
            )
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("ibapi")


def test_gateway_calls_no_trading_entry_point() -> None:
    """The bridge is a transport: it places nothing and cancels nothing."""

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else None
        for node in ast.walk(TREE)
        if isinstance(node, ast.Call)
    }
    for forbidden in ("placeOrder", "cancelOrder", "reqGlobalCancel"):
        assert forbidden not in called, forbidden


def test_gateway_constructs_no_order_or_contract() -> None:
    """Only the adapter builds ``Contract`` / ``Order`` objects."""

    constructed = {
        node.func.id
        for node in ast.walk(TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "Contract" not in constructed
    assert "Order" not in constructed


def test_gateway_never_reaches_into_service_internals() -> None:
    """The sink contract is explicit callbacks, never private attributes."""

    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                continue
            assert not node.attr.startswith("_") or node.attr in {
                "_current",
                "_sink",
                "_epoch",
            }, node.attr


# ------------------------------------------------------------ lazy ibapi


def test_factory_reports_a_missing_ibkr_api() -> None:
    sink = RecordingSink()

    with patch.dict(sys.modules, {"ibapi.client": None}):
        with pytest.raises(IBKRPaperGatewayError):
            create_paper_gateway_app(sink=sink, epoch=1)


def test_factory_builds_an_app_when_the_api_is_present() -> None:
    sink = RecordingSink()
    app = _build(sink)

    assert app is not None
    assert sink.calls == []


def test_factory_returns_a_fresh_app_per_call() -> None:
    sink = RecordingSink()

    assert _build(sink, epoch=1) is not _build(sink, epoch=2)


def test_handshake_events_start_clear_and_are_shared() -> None:
    handshake = PaperGatewayHandshake()

    assert not handshake.ready.is_set()
    assert not handshake.accounts_ready.is_set()
    assert not handshake.executions_ready.is_set()
    assert handshake.errors == []
    assert handshake.errors is handshake.errors


# ------------------------------------------------------------- forwarding


# (callback, args passed to the callback, args the sink must receive)
FORWARDED_CALLS = (
    ("nextValidId", (42,), (42,)),
    ("managedAccounts", ("DU1234567",), ("DU1234567",)),
    ("error", (91_003, 201, "Order rejected", "x"), (91_003, (201, "Order rejected", "x"))),
    (
        "orderStatus",
        (7, "Filled", 2, 1, 3, 101, 202, 4, 303, "held-marker", 5),
        (7, "Filled", 2, 1, 3, 101, 202, 4, 303, "held-marker", 5),
    ),
    ("openOrder", (7, "contract", "order", "state"), (7, "contract", "order", "state")),
    ("openOrderEnd", (), ()),
    (
        "accountSummary",
        (91_001, "DU1234567", "NetLiquidation", "100", "USD-marker"),
        (91_001, "DU1234567", "NetLiquidation", "100", "USD-marker"),
    ),
    ("accountSummaryEnd", (91_001,), (91_001,)),
    ("position", ("DU1234567", "contract", 3.0, 100.0), ("DU1234567", "contract", 3.0, 100.0)),
    ("positionEnd", (), ()),
    ("pnl", (91_002, 1.0, 2.0, 3.0), (91_002, 1.0, 2.0, 3.0)),
    ("execDetails", (91_003, "contract", "execution"), (91_003, "contract", "execution")),
    ("execDetailsEnd", (91_003,), (91_003,)),
    ("completedOrder", ("contract", "order", "state"), ("contract", "order", "state")),
    ("completedOrdersEnd", (), ()),
    ("connectionClosed", (), ()),
)


def _snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


@pytest.mark.parametrize("name,args,expected", FORWARDED_CALLS)
def test_callback_is_forwarded_exactly_once(name, args, expected) -> None:
    sink = RecordingSink()
    app = _build(sink, epoch=11)

    getattr(app, name)(*args)

    forwarded = _forwarded(sink)
    assert len(forwarded) == 1, sink.calls
    handler, forwarded_args = forwarded[0]
    assert handler == f"gateway_{_snake(name)}", handler
    assert forwarded_args[0] is app
    assert forwarded_args[1] == 11
    assert forwarded_args[2:] == expected, forwarded_args


def test_forwarding_does_not_copy_the_arguments() -> None:
    sink = RecordingSink()
    app = _build(sink)
    contract = SimpleNamespace(symbol="AAPL")

    app.openOrder(7, contract, "order", "state")

    assert _forwarded(sink)[0][1][3] is contract


def test_error_callback_forwards_the_variadic_arguments() -> None:
    """IBKR calls ``error`` with different arities; both must survive."""

    sink = RecordingSink()
    app = _build(sink)

    app.error(1, 200, "No security definition")
    app.error(1, 200, "No security definition", "extra")

    forwarded = _forwarded(sink)
    assert forwarded[0][1][2:] == (1, (200, "No security definition"))
    assert forwarded[1][1][2:] == (
        1,
        (200, "No security definition", "extra"),
    )


def test_order_status_arguments_keep_their_positions() -> None:
    """All eleven IBKR arguments must reach the sink, in order.

    Every value is a distinct sentinel so a permutation, a truncation or a
    dropped argument is visible.  The four arguments the service does not act
    on (``permId``, ``parentId``, ``clientId``, ``mktCapPrice``) are the ones
    most likely to be silently swallowed by the transport, so they get the
    most distinctive values.
    """

    sink = RecordingSink()
    app = _build(sink, epoch=2)

    app.orderStatus(7, "Filled", 2, 1, 3, 101, 202, 4, 303, "held-marker", 5)

    name, args = _forwarded(sink)[0]
    assert name == "gateway_order_status"
    assert args[2:] == (
        7,
        "Filled",
        2,
        1,
        3,
        101,
        202,
        4,
        303,
        "held-marker",
        5,
    )


def test_account_summary_currency_reaches_the_sink() -> None:
    """``currency`` is transport data even though the service discards it."""

    sink = RecordingSink()
    app = _build(sink, epoch=2)

    app.accountSummary(91_001, "DU1234567", "NetLiquidation", "100", "USD-marker")

    name, args = _forwarded(sink)[0]
    assert name == "gateway_account_summary"
    assert args[2:] == (
        91_001,
        "DU1234567",
        "NetLiquidation",
        "100",
        "USD-marker",
    )


def test_transport_holds_no_state_of_its_own() -> None:
    """After forwarding, the bridge remembers nothing but sink and epoch."""

    sink = RecordingSink()
    app = _build(sink, epoch=5)

    app.orderStatus(7, "Filled", 2.0, 0.0, 10.0, 1, 0, 10.0, 9, "", 0.0)
    app.nextValidId(42)

    assert app._epoch == 5
    assert app._sink is sink
    assert set(vars(app)) <= {"_sink", "_epoch", "wrapper"}


# ----------------------------------------------------------- stale epochs


STALE_CALLS = (
    ("orderStatus", (7, "Filled", 2.0, 0.0, 10.0, 1, 0, 10.0, 9, "", 0.0)),
    ("execDetails", (91_003, "contract", "execution")),
    ("openOrder", (7, "contract", "order", "state")),
    ("connectionClosed", ()),
)


@pytest.mark.parametrize("name,args", STALE_CALLS)
def test_stale_callback_is_never_forwarded(name, args) -> None:
    sink = RecordingSink(current=False)
    app = _build(sink, epoch=3)

    getattr(app, name)(*args)

    assert _forwarded(sink) == []


@pytest.mark.parametrize("name,args", STALE_CALLS)
def test_stale_check_uses_this_app_and_this_epoch(name, args) -> None:
    sink = RecordingSink(current=False)
    app = _build(sink, epoch=3)

    getattr(app, name)(*args)

    checks = [call for call in sink.calls if call[0] == "gateway_is_current"]
    assert checks, sink.calls
    assert checks[0][1] == (app, 3)


def test_every_callback_guards_on_the_current_connection() -> None:
    """No callback may forward without asking the sink first."""

    app_class = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ClassDef) and node.name == "PaperApp"
    )
    callbacks = [
        node
        for node in app_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name not in {"__init__", "_current"}
    ]
    assert len(callbacks) == len(FORWARDED_CALLS), [
        node.name for node in callbacks
    ]

    for callback in callbacks:
        guard = callback.body[0]
        assert isinstance(guard, ast.If), callback.name
        assert isinstance(guard.test, ast.UnaryOp), callback.name
        assert guard.test.operand.func.attr == "_current", callback.name
        assert isinstance(guard.body[0], ast.Return), callback.name
        assert guard.orelse == [], callback.name


def test_no_callback_drops_an_ibkr_argument() -> None:
    """Every parameter IBKR hands the transport must reach the sink.

    The transport's only job is to move bytes.  A callback that accepts an
    argument and never forwards it is silently reinterpreted at the transport
    layer, which is exactly the boundary this split exists to draw.  ``self``
    and ``*args`` are excluded; ``args`` is forwarded whole.
    """

    app_class = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ClassDef) and node.name == "PaperApp"
    )
    callbacks = [
        node
        for node in app_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name not in {"__init__", "_current"}
    ]

    for callback in callbacks:
        params = {
            arg.arg
            for arg in callback.args.args[1:]  # drop self
            if arg.arg not in {"self"}
        }
        if callback.args.vararg is not None:
            params.add(callback.args.vararg.arg)
        forwarded: set[str] = set()
        for node in ast.walk(callback):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("gateway_")
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        forwarded.add(arg.id)
                    elif isinstance(arg, ast.Starred) and isinstance(
                        arg.value, ast.Name
                    ):
                        forwarded.add(arg.value.id)
        dropped = params - forwarded
        assert not dropped, f"{callback.name} drops {sorted(dropped)}"


def test_gateway_performs_no_interpretation_of_callback_data() -> None:
    """The transport may not convert, filter or interpret what it carries.

    The boundary contract is "Gateway moves bytes, Service decides meaning".
    This walks every callback and fails on any of the interpretation moves the
    step-6 addendum forbids: type conversion, account filtering, status
    interpretation, and mutation of anything other than its own two fields.
    """

    app_class = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ClassDef) and node.name == "PaperApp"
    )
    callbacks = [
        node
        for node in app_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name not in {"__init__", "_current"}
    ]
    assert callbacks

    converters = {"Decimal", "str", "int", "float", "bool", "round"}
    own_attrs = {"_sink", "_epoch"}

    for callback in callbacks:
        params = {arg.arg for arg in callback.args.args[1:]}
        for node in ast.walk(callback):
            # No type conversion: that is the service's job.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in converters, (
                    f"{callback.name} converts with {node.func.id}"
                )
                assert node.func.id in {"len"}, (
                    f"{callback.name} calls {node.func.id}"
                )
            # No account filtering at the transport layer.
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                names = {
                    operand.id
                    for operand in operands
                    if isinstance(operand, ast.Name)
                }
                assert "account" not in names, (
                    f"{callback.name} filters on account"
                )
            # No state of its own beyond sink and epoch.
            if isinstance(node, ast.Attribute) and isinstance(
                node.value, ast.Name
            ):
                if node.value.id == "self" and isinstance(node.ctx, ast.Store):
                    assert node.attr in own_attrs, (
                        f"{callback.name} stores self.{node.attr}"
                    )
            # Only the sink may be called (plus its own guard).
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    assert func.attr == "_current" or func.attr.startswith(
                        "gateway_"
                    ), f"{callback.name} calls {func.attr}"
                    if func.attr.startswith("gateway_"):
                        assert isinstance(func.value, ast.Attribute)
                        assert func.value.attr == "_sink"


def test_current_check_delegates_to_the_sink() -> None:
    """``_current`` must ask the sink; only the service owns the judgement."""

    app_class = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ClassDef) and node.name == "PaperApp"
    )
    current = next(
        node
        for node in app_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_current"
    )
    returned = current.body[0]
    assert isinstance(returned, ast.Return)
    call = returned.value
    assert isinstance(call, ast.Call)
    assert call.func.attr == "gateway_is_current"
    assert [ast.unparse(arg) for arg in call.args] == ["self", "self._epoch"]
