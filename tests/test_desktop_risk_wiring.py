"""Desktop risk wiring tests: one risk truth, from config to the running engine.

These exist because of a specific defect.  The window placed the account limits
into ``ShadowConfig.layered_risk_limits`` and then constructed
``AutoQuantEngine`` *without* passing ``layered_risk_limits`` to it, so the
configured ``risk_limits`` reached a field nobody read and the running engine
enforced none of them.  The unit tests passed, because they injected the
argument directly -- proving the engine works, never that the product uses it.

The guards below close that gap from both ends:

* behaviourally, by building the risk application the way the window does and
  asserting the engine that receives it reports the window's configured
  limits;
* structurally, by asserting the engine call site passes ``risk=`` and none of
  the retired risk arguments, so the two-path arrangement cannot come back by
  editing one line.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
import pathlib

import pytest

from PySide6.QtWidgets import QApplication

from us_quant.auto_intraday import build_auto_rotation_config
from us_quant.auto_quant import AutoQuantCandidate, AutoQuantEngine
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.risk import RiskPage
from us_quant.shadow_paper import ShadowConfig
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.risk import LayeredRiskLimits, RiskLimits
from us_quant.trading.domain.strategy import StrategyIdentity

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DESKTOP = _REPO_ROOT / "src" / "us_quant" / "desktop.py"


def _unused_execution() -> ExecutionApplication:
    """An execution service the assertions here never drive.

    These tests are about which risk authority the engine holds.  The engine
    still requires an execution service, so one is supplied with ports that are
    never called -- a lambda would be rejected by the constructor's type check,
    which is itself the point.
    """

    return ExecutionApplication(repository=object(), broker=object())


#: A limit set no configuration file would produce, so "it received the
#: configured limits" cannot pass by coincidence.
_DISTINCTIVE = RiskLimits(
    max_gross_exposure_pct=Decimal("0.37"),
    max_position_exposure_pct=Decimal("0.061"),
    daily_loss_halt_pct=Decimal("0.013"),
    drawdown_halt_pct=Decimal("0.047"),
    allow_margin_borrowing=False,
)

_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def window():
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _engine_call_keywords() -> list[dict[str, object]]:
    """Every ``AutoQuantEngine(...)`` call in ``desktop.py``, by keyword."""

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "AutoQuantEngine"
        ):
            calls.append(
                {keyword.arg: keyword.value for keyword in node.keywords}
            )
    return calls


# -- behaviour: the configured limits reach the engine --------------------


def test_the_window_builds_one_risk_application_from_its_current_config(
    window,
) -> None:
    window.config = replace(window.config, risk_limits=_DISTINCTIVE)
    risk = window._build_auto_quant_risk()
    assert isinstance(risk, RiskApplication)
    assert risk.limits.account == _DISTINCTIVE
    assert risk.session_overrides is not None


def test_the_engine_receives_the_application_the_window_built(window) -> None:
    """The regression itself: ``Engine.risk`` is what the window configured."""

    window.config = replace(window.config, risk_limits=_DISTINCTIVE)
    risk = window._build_auto_quant_risk()
    engine = AutoQuantEngine(
        candidates=(
            AutoQuantCandidate(
                "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
            ),
        ),
        config=ShadowConfig(
            initial_cash=Decimal("10000"), capital_source="test"
        ),
        strategy=StrategyIdentity(
            strategy_id="intraday-auto-rotation",
            version_id="v",
            parameter_hash="p",
        ),
        risk=risk,
        execution=_unused_execution(),
    )
    assert engine.risk is risk
    assert engine.risk.limits.account == window.config.risk_limits
    assert (
        engine.risk.limits.account.max_position_exposure_pct
        == Decimal("0.061")
    )


def test_the_window_passes_its_configured_exposure_multipliers(window) -> None:
    risk = window._build_auto_quant_risk()
    configured = window._configured_exposure_multipliers()
    for rule in window.config.substitutions.values():
        assert risk.exposure_multiplier(
            rule.execution_symbol
        ) == rule.exposure_multiplier
    assert set(configured) == {
        rule.execution_symbol for rule in window.config.substitutions.values()
    }


def test_the_engine_refuses_to_run_without_a_risk_application() -> None:
    with pytest.raises(TypeError):
        AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"), capital_source="test"
            ),
            strategy=StrategyIdentity(
                strategy_id="s", version_id="v", parameter_hash="p"
            ),
            risk=None,
            # Supplied, so the TypeError is about the missing risk authority
            # rather than about a missing argument.
            execution=_unused_execution(),
        )


def test_the_engine_refuses_to_run_without_an_execution_service() -> None:
    """Execution v2: the engine cannot submit, so it must be handed the one
    service that can -- a stand-in with an execution-shaped API is not enough.
    """

    with pytest.raises(TypeError):
        AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"), capital_source="test"
            ),
            strategy=StrategyIdentity(
                strategy_id="s", version_id="v", parameter_hash="p"
            ),
            risk=RiskApplication(
                LayeredRiskLimits(
                    account=RiskLimits(
                        max_gross_exposure_pct=Decimal("1"),
                        max_position_exposure_pct=Decimal("1"),
                        daily_loss_halt_pct=Decimal("1"),
                        drawdown_halt_pct=Decimal("1"),
                    )
                )
            ),
            execution=lambda intent: 1,
        )


# -- behaviour: the rotation config carries no risk -----------------------


def test_the_rotation_config_builder_owns_no_risk_policy() -> None:
    """Spec 83: it is a strategy/session builder and nothing else."""

    import inspect

    parameters = inspect.signature(build_auto_rotation_config).parameters
    assert "layered_risk_limits" not in parameters
    assert "symbol_risk_multipliers" not in parameters
    config = build_auto_rotation_config(
        _rotation_parameters(),
        initial_cash=Decimal("10000"),
        capital_source="test",
        daily_loss_limit=Decimal("100"),
    )
    # ``ShadowConfig`` keeps the field because ``ShadowPaperEngine`` still
    # uses it; the auto-rotation path must simply stop populating it.
    assert config.layered_risk_limits is None
    assert dict(config.symbol_risk_multipliers) == {}


def _rotation_parameters() -> dict[str, object]:
    """The seeded parameter set for the auto-rotation strategy."""

    from us_quant.trading.application.strategy_defaults import (
        DEFAULT_STRATEGY_SEEDS,
    )

    seed = next(
        item
        for item in DEFAULT_STRATEGY_SEEDS
        if item.strategy_id == "intraday-auto-rotation"
    )
    return dict(seed.parameters)


# -- structure: the retired two-path wiring cannot return -----------------


def test_the_engine_call_site_passes_the_risk_application() -> None:
    calls = _engine_call_keywords()
    assert len(calls) == 1, calls
    keywords = calls[0]
    assert "risk" in keywords
    assert "strategy" in keywords
    for retired in (
        "layered_risk_limits",
        "symbol_risk_multipliers",
        "strategy_version_id",
        "parameter_hash",
    ):
        assert retired not in keywords, retired


def test_the_window_never_hands_risk_limits_to_the_shadow_config() -> None:
    """The mixed-up path: limits stored where nothing reads them."""

    source = _DESKTOP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_auto_rotation_config"
        ):
            offenders.extend(
                keyword.arg for keyword in node.keywords
            )
    assert "layered_risk_limits" not in offenders, offenders
    assert "symbol_risk_multipliers" not in offenders, offenders


def test_the_risk_route_is_the_native_v2_page(window) -> None:
    assert isinstance(window.risk_page, RiskPage)
    index = window.shell.routes.index("risk")
    assert window.shell.page_stack.widget(index) is window.risk_page
    assert window.risk_page.limits == window.config.risk_limits


def test_the_legacy_safety_page_is_deleted() -> None:
    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_safety_tab" not in methods
    assert "RiskPage" in _DESKTOP.read_text(encoding="utf-8")


def test_no_source_module_imports_the_retired_risk_module() -> None:
    offenders = []
    for path in sorted((_REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                module = next(iter(node.names), None)
                module = getattr(module, "name", None)
            if module == "us_quant.risk":
                offenders.append(path.name)
    assert not offenders, offenders
    assert not (_REPO_ROOT / "src" / "us_quant" / "risk.py").exists()
