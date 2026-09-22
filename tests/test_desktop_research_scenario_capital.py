"""The research scenario capital scalar, without a window or Qt.

``ResearchScenarioCapitalState`` is deliberately tiny, so this file is short on
purpose: it pins the four facts that make it usable as a canonical owner --
the initial value, the exact Decimal projection, the change report, and the
absence of any trading dependency that would let research dollars be mistaken
for orderable capital.  A dozen tests over one int would be noise.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from decimal import Decimal

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "orchestration"
    / "research"
    / "scenario_capital.py"
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from us_quant.desktop_v2.orchestration.research import (  # noqa: E402
    scenario_capital,
)

ResearchScenarioCapitalState = scenario_capital.ResearchScenarioCapitalState


def test_the_initial_value_is_what_the_caller_gave() -> None:
    assert ResearchScenarioCapitalState(1500).value == 1500


def test_a_non_int_is_coerced_to_int() -> None:
    """The constructor keeps the scalar an ``int`` whatever it is handed."""

    state = ResearchScenarioCapitalState(True)

    assert state.value == 1
    assert isinstance(state.value, int)


def test_decimal_value_is_exact_not_rounded() -> None:
    """No float round-trip: the research executor must see the same digits."""

    state = ResearchScenarioCapitalState(2500)

    assert state.decimal_value == Decimal(2500)
    assert state.decimal_value == Decimal("2500")


def test_decimal_value_preserves_a_value_float_cannot_represent() -> None:
    """The precision claim has to be tested with a value float cannot hold.

    ``Decimal(float(n))`` for this number is not equal to ``Decimal(n)``, so
    the two conversions are distinguishable -- which is what makes the
    assertion above meaningful rather than vacuous.
    """

    number = 2**53 + 1

    state = ResearchScenarioCapitalState(number)

    assert state.decimal_value == Decimal(number)
    assert Decimal(float(number)) != Decimal(number)


def test_set_reports_true_when_the_value_changes() -> None:
    state = ResearchScenarioCapitalState(1500)

    changed = state.set(2500)

    assert changed is True
    assert state.value == 2500


def test_set_reports_false_when_the_value_is_already_that() -> None:
    """A no-op edit must be distinguishable, so callers can avoid a repaint."""

    state = ResearchScenarioCapitalState(2500)

    changed = state.set(2500)

    assert changed is False
    assert state.value == 2500


def test_set_coerces_and_keeps_the_projection_in_step() -> None:
    state = ResearchScenarioCapitalState(1500)

    state.set(Decimal("2500"))

    assert state.value == 2500
    assert isinstance(state.value, int)
    assert state.decimal_value == Decimal(2500)


# -- the boundaries the module must not cross ----------------------------


def _imports() -> set[str]:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_module_imports_nothing_but_the_standard_library() -> None:
    """A canonical scalar needs no framework, and no neighbour to lean on."""

    assert _imports() <= {"__future__", "decimal"}


def _code_without_docstrings() -> str:
    """The module source with every string literal removed.

    The docstring deliberately *names* the concepts this module refuses to
    touch -- saying "it holds no ``AppConfig``" is the point -- so a textual
    ban would flag the very prose that documents the boundary.  Stripping the
    literals leaves only what the module can actually reach.
    """

    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree)


@pytest.mark.parametrize(
    "forbidden",
    (
        "PySide6",
        "QObject",
        "Signal",
        "MainWindow",
        "Account",
        "Broker",
        "RiskApplication",
        "Execution",
        "Paper",
        "Shadow",
        "CrossSection",
        "AppConfig",
    ),
)
def test_the_module_never_names_a_trading_or_ui_concept(
    forbidden: str,
) -> None:
    """Research scenario dollars must not be reachable from trading vocabulary.

    The name is the safety boundary: when a real ``CapitalAllocator`` arrives
    it is built from broker truth and portfolio risk, so nothing in the
    research scalar's *code* may be spelled in those terms -- otherwise
    promoting it would look like a rename.
    """

    assert forbidden not in _code_without_docstrings(), forbidden


def test_the_module_holds_one_int_and_no_collection() -> None:
    """It is not a ``ResearchState`` bag: a second attribute fails here."""

    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ResearchScenarioCapitalState"
    )
    assigns = [
        node
        for node in cls.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    # ``self._value = int(initial_value)`` is the only instance assignment.
    assert len(assigns) == 0 or all(
        isinstance(node, ast.AnnAssign) for node in assigns
    )

    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(
            node.value, ast.Name
        ):
            if node.value.id == "self":
                assert node.attr == "_value", node.attr
