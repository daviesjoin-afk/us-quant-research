from decimal import Decimal

from us_quant.auto_launch import (
    auto_launch_plan_matches,
    build_auto_launch_plan,
)


def _plan():
    return build_auto_launch_plan(
        attempt_id=7,
        strategy_version_id="intraday-auto-rotation@1.1.0",
        parameter_hash="abc123",
        candidate_symbols=("spy", "XLE", "BAC"),
        requested_capital_limit=Decimal("1500"),
    )


def test_auto_launch_plan_matches_same_confirmed_inputs() -> None:
    assert auto_launch_plan_matches(
        _plan(),
        strategy_version_id="intraday-auto-rotation@1.1.0",
        parameter_hash="abc123",
        candidate_symbols=("SPY", "xle", "bac"),
        requested_capital_limit=Decimal("1500"),
    )


def test_auto_launch_plan_rejects_changed_strategy() -> None:
    assert not auto_launch_plan_matches(
        _plan(),
        strategy_version_id="intraday-auto-rotation@1.2.0",
        parameter_hash="abc123",
        candidate_symbols=("SPY", "XLE", "BAC"),
        requested_capital_limit=Decimal("1500"),
    )


def test_auto_launch_plan_rejects_changed_parameters() -> None:
    assert not auto_launch_plan_matches(
        _plan(),
        strategy_version_id="intraday-auto-rotation@1.1.0",
        parameter_hash="changed",
        candidate_symbols=("SPY", "XLE", "BAC"),
        requested_capital_limit=Decimal("1500"),
    )


def test_auto_launch_plan_rejects_changed_candidates() -> None:
    assert not auto_launch_plan_matches(
        _plan(),
        strategy_version_id="intraday-auto-rotation@1.1.0",
        parameter_hash="abc123",
        candidate_symbols=("SPY", "BAC", "XLE"),
        requested_capital_limit=Decimal("1500"),
    )


def test_auto_launch_plan_rejects_changed_capital_limit() -> None:
    assert not auto_launch_plan_matches(
        _plan(),
        strategy_version_id="intraday-auto-rotation@1.1.0",
        parameter_hash="abc123",
        candidate_symbols=("SPY", "XLE", "BAC"),
        requested_capital_limit=Decimal("2000"),
    )
