"""Step 5: the pure Paper order model layer.

The models were relocated, not redesigned, so these tests pin the contract the
rest of the codebase depends on: field names, frozen/slots, defaults, and the
fact that the old import path and the new one are the same object.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from decimal import Decimal
from pathlib import Path
import sys

from us_quant import ibkr_paper_orders as old_path
from us_quant import paper_order_models as models

MIGRATED_MODELS = (
    "PaperOrderIntent",
    "PaperOrderUpdate",
    "PaperExecution",
    "PaperOrderConnection",
    "PaperBrokerPosition",
    "PaperBrokerState",
    "PaperOrderReconciliation",
    "ReconciliationSummary",
    "PaperBrokerOrder",
    "PaperReconciliationSnapshot",
)


def _field_map(cls: type) -> dict[str, tuple[str, object]]:
    return {
        field.name: (str(field.type), field.default)
        for field in fields(cls)
    }


def test_models_module_has_no_runtime_dependencies() -> None:
    """A DTO layer that imports a broker, a DB or a GUI is not a DTO layer."""

    source = Path(models.__file__ or "").read_text(encoding="utf-8")

    for forbidden in (
        "ibapi",
        "sqlite3",
        "PySide6",
        "desktop",
        "paper_trading_service",
        "IBKRPaperOrderService",
        "connect_sqlite",
        "IBKR",
    ):
        assert forbidden not in source, forbidden


def test_models_module_imports_nothing_from_the_package() -> None:
    """Nothing in this module may depend on another us_quant module."""

    source = Path(models.__file__ or "").read_text(encoding="utf-8")

    assert "from us_quant" not in source
    assert "import us_quant" not in source


def test_every_migrated_model_is_a_frozen_slots_dataclass() -> None:
    for name in MIGRATED_MODELS:
        cls = getattr(models, name)
        params = cls.__dataclass_params__

        assert is_dataclass(cls), name
        assert params.frozen is True, name
        assert "__slots__" in cls.__dict__, name


def test_migrated_models_keep_their_exact_field_contract() -> None:
    """Field names, order, types and defaults are part of the on-disk format.

    The journal writes these values by position and reads them back by name,
    so a rename or a reordered default silently corrupts stored orders.
    """

    expected = {
        "PaperOrderIntent": [
            ("intent_id", "str", None),
            ("session_id", "str", None),
            ("strategy_version_id", "str", None),
            ("symbol", "str", None),
            ("side", "str", None),
            ("quantity", "int", None),
            ("limit_price", "Decimal", None),
            ("reason", "str", None),
            ("generated_at", "str", None),
            ("idempotency_key", "str | None", None),
        ],
        "PaperOrderUpdate": [
            ("intent_id", "str", None),
            ("broker_order_id", "int", None),
            ("status", "str", None),
            ("filled", "Decimal", None),
            ("remaining", "Decimal", None),
            ("average_fill_price", "Decimal | None", None),
            ("last_fill_price", "Decimal | None", None),
            ("message", "str", None),
            ("observed_at", "str", None),
        ],
        "PaperExecution": [
            ("intent_id", "str", None),
            ("broker_order_id", "int", None),
            ("execution_id", "str", None),
            ("symbol", "str", None),
            ("side", "str", None),
            ("quantity", "Decimal", None),
            ("price", "Decimal", None),
            ("occurred_at", "str", None),
        ],
        "PaperBrokerPosition": [
            ("symbol", "str", None),
            ("quantity", "Decimal", None),
            ("average_cost", "Decimal", None),
        ],
        "PaperBrokerOrder": [
            ("broker_order_id", "int", None),
            ("symbol", "str", None),
            ("side", "str", None),
            ("quantity", "Decimal", None),
            ("status", "str", None),
        ],
        "ReconciliationSummary": [
            ("session_id", "str", None),
            ("total", "int", None),
            ("reconciled", "int", None),
            ("unreconciled", "int", None),
            ("terminal", "int", None),
            ("terminal_unreconciled", "int", None),
            ("nonterminal", "int", None),
            ("observed_at", "str", None),
        ],
    }

    for name, spec in expected.items():
        actual = [
            (field.name, str(field.type))
            for field in fields(getattr(models, name))
        ]
        assert actual == [(n, t) for n, t, _ in spec], name


def test_defaults_survive_the_move() -> None:
    """The only defaulted fields in the layer, checked by value."""

    intent = models.PaperOrderIntent(
        intent_id="i",
        session_id="s",
        strategy_version_id="v",
        symbol="AAPL",
        side="BUY",
        quantity=1,
        limit_price=Decimal("1"),
        reason="r",
        generated_at="g",
    )
    assert intent.idempotency_key is None

    connection = models.PaperOrderConnection(
        connected=True,
        account_alias="DU***17",
        server_version=176,
        connection_time="t",
        next_order_id=1,
    )
    assert connection.open_broker_orders == 0
    assert connection.unreconciled_local_orders == 0
    assert connection.connection_generation == 0
    assert connection.snapshot_complete is False
    assert connection.observed_at == ""


def test_models_keep_their_declared_defaults() -> None:
    """Defaults are part of the constructor contract, not decoration.

    The layer has exactly four defaulted fields; they are read here as values
    so that flipping one is caught even though the field list still matches.
    """

    expected = {
        "PaperOrderIntent": {"idempotency_key": None},
        "PaperOrderConnection": {
            "open_broker_orders": 0,
            "unreconciled_local_orders": 0,
            "connection_generation": 0,
            "snapshot_complete": False,
            "observed_at": "",
        },
        "PaperReconciliationSnapshot": {"snapshot_complete": True},
    }

    seen: set[tuple[str, str]] = set()
    for name, defaults in expected.items():
        cls = getattr(models, name)
        for field in fields(cls):
            if field.default is not MISSING:
                assert field.name in defaults, f"{name}.{field.name} has a default"
                assert field.default == defaults[field.name], (
                    f"{name}.{field.name}"
                )
                seen.add((name, field.name))
        for field_name, value in defaults.items():
            assert (name, field_name) in seen, f"{name}.{field_name}"

    # Every other field in the layer is required.
    for name in MIGRATED_MODELS:
        for field in fields(getattr(models, name)):
            if field.default is MISSING:
                continue
            assert (name, field.name) in seen, f"{name}.{field.name}"


def test_old_and_new_import_paths_are_the_same_objects() -> None:
    """Re-exports, not subclasses or wrappers; identity is what matters."""

    for name in MIGRATED_MODELS:
        assert getattr(old_path, name) is getattr(models, name), name

    assert old_path.TERMINAL_ORDER_STATUSES is models.TERMINAL_ORDER_STATUSES


def test_terminal_statuses_are_defined_exactly_once() -> None:
    """Both the journal and the adapter read this vocabulary."""

    import us_quant.paper_order_journal as journal

    assert journal.TERMINAL_ORDER_STATUSES is models.TERMINAL_ORDER_STATUSES
    assert models.TERMINAL_ORDER_STATUSES == frozenset(
        {"filled", "cancelled", "apicancelled", "inactive", "error"}
    )


def test_models_are_not_defined_anywhere_else() -> None:
    """A second definition would let the journal and adapter drift apart."""

    package = Path(models.__file__ or "").parent
    needles = tuple(f"class {name}:" for name in MIGRATED_MODELS) + tuple(
        f"class {name}(" for name in MIGRATED_MODELS
    )

    for path in sorted(package.glob("*.py")):
        if path.name == "paper_order_models.py":
            continue
        source = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in source, f"{needle} in {path.name}"


def test_models_module_is_importable_without_the_adapter() -> None:
    """Import isolation: the DTO layer must not drag the adapter in."""

    import subprocess

    script = (
        "import sys; import us_quant.paper_order_models; "
        "print('ibkr_paper_orders' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(models.__file__ or "").parents[2]),
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", result.stdout
