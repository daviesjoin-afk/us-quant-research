"""Strategy parameter validation tests.

This replaces ``tests/test_strategy_schema.py``.  The module moved from
``us_quant.strategy_schema`` to ``us_quant.trading.domain.strategy_parameters``
verbatim, so the point of these tests is that the behaviour did not move with
it: every range, comparison and normalisation below is the old behaviour, and
the new ones cover the rules the original file left unasserted.

Two conventions are pinned explicitly because they look like bugs and are not:

* a validated number is stored back as ``str``, so the canonical JSON -- and
  therefore every already-governed ``parameter_hash`` -- is stable;
* ``whole_shares`` cannot be switched off for any family.
"""

from __future__ import annotations

import pytest

from us_quant.trading.domain.strategy_parameters import (
    StrategyParameterError,
    strategy_schema_summary,
    validate_strategy_parameters,
)


def targeted_parameters() -> dict[str, object]:
    return {
        "momentum_lookback_minutes": 5,
        "warmup_minutes": 10,
        "maximum_hold_minutes": 45,
        "maximum_trades_per_day": 4,
        "max_position_fraction": "0.10",
        "min_order_notional": "50",
        "commission_per_order": "0.35",
        "slippage_bps": "2",
        "maximum_spread_fraction": "0.002",
        "minimum_momentum": "0.0035",
        "maximum_momentum": "0.025",
        "profit_target": "0.012",
        "stop_loss": "0.007",
        "trailing_stop": "0.006",
        "whole_shares": True,
    }


# -- dual moving average --------------------------------------------------


def test_dual_ma_requires_short_below_long() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend",
            {
                "short_window": 100,
                "long_window": 20,
                "whole_shares": True,
            },
        )


def test_dual_ma_accepts_a_valid_pair_and_normalises() -> None:
    validated = validate_strategy_parameters(
        "dual-ma-trend",
        {"short_window": 20, "long_window": 100, "whole_shares": True},
    )
    assert validated == {
        "short_window": 20,
        "long_window": 100,
        "whole_shares": True,
    }


def test_dual_ma_window_ranges_are_enforced() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend",
            {"short_window": 1, "long_window": 100, "whole_shares": True},
        )
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend",
            {"short_window": 20, "long_window": 501, "whole_shares": True},
        )


# -- donchian and RSI -----------------------------------------------------


def test_donchian_and_rsi_ranges_are_validated() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "donchian-breakout",
            {
                "entry_window": 20,
                "exit_window": 20,
                "whole_shares": True,
            },
        )
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "rsi-mean-reversion",
            {
                "window": 5,
                "entry_threshold": 60,
                "exit_threshold": 40,
                "whole_shares": True,
            },
        )


def test_rsi_thresholds_are_stored_as_text() -> None:
    """A validated number becomes a string, so the hash cannot drift."""

    validated = validate_strategy_parameters(
        "rsi-mean-reversion",
        {
            "window": 5,
            "entry_threshold": 25,
            "exit_threshold": 55,
            "whole_shares": True,
        },
    )
    assert validated["entry_threshold"] == "25.0"
    assert validated["exit_threshold"] == "55.0"


# -- whole shares ---------------------------------------------------------


@pytest.mark.parametrize(
    "strategy_id,parameters",
    [
        ("buy-hold", {"whole_shares": False}),
        ("dual-ma-trend", {"short_window": 20, "long_window": 100, "whole_shares": False}),
        (
            "donchian-breakout",
            {"entry_window": 55, "exit_window": 20, "whole_shares": False},
        ),
        (
            "rsi-mean-reversion",
            {
                "window": 5,
                "entry_threshold": "25",
                "exit_threshold": "55",
                "whole_shares": False,
            },
        ),
    ],
)
def test_whole_share_constraint_cannot_be_disabled(
    strategy_id: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(strategy_id, parameters)


def test_whole_shares_defaults_to_true() -> None:
    assert validate_strategy_parameters("buy-hold", {}) == {
        "whole_shares": True
    }


# -- sector momentum ------------------------------------------------------


def test_sector_momentum_requires_non_empty_lists() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "sector-momentum",
            {
                "lookbacks": [],
                "rebalance_days": [5, 21],
                "max_holdings": [3, 5],
                "whole_shares": True,
                "max_gross_risk_pct": 0.50,
            },
        )


def test_sector_momentum_gross_risk_floor_is_single_position_budget() -> None:
    """The portfolio budget may not be set below the 10% per-position cap."""

    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "sector-momentum",
            {
                "lookbacks": [63],
                "rebalance_days": [5],
                "max_holdings": [3],
                "whole_shares": True,
                "max_gross_risk_pct": 0.05,
            },
        )
    validated = validate_strategy_parameters(
        "sector-momentum",
        {
            "lookbacks": [63],
            "rebalance_days": [5],
            "max_holdings": [3],
            "whole_shares": True,
            "max_gross_risk_pct": 0.50,
        },
    )
    assert validated["max_gross_risk_pct"] == "0.5"


def test_sector_momentum_leaves_unknown_keys_untouched() -> None:
    """Only declared keys are touched; the rest is carried through as given."""

    validated = validate_strategy_parameters(
        "sector-momentum",
        {
            "lookbacks": [63],
            "rebalance_days": [5],
            "max_holdings": [3],
            "whole_shares": True,
            "max_gross_risk_pct": 0.5,
            "max_position_risk_pct": 0.1,
        },
    )
    assert validated["max_position_risk_pct"] == 0.1


# -- intraday families ----------------------------------------------------


def test_targeted_intraday_parameters_are_symbol_agnostic() -> None:
    validated = validate_strategy_parameters(
        "intraday-targeted-t",
        targeted_parameters(),
    )
    assert "symbol" not in validated
    with pytest.raises(StrategyParameterError):
        invalid = targeted_parameters()
        invalid["momentum_lookback_minutes"] = 1
        validate_strategy_parameters("intraday-targeted-t", invalid)


def test_targeted_intraday_gets_no_reference_symbols() -> None:
    """Only auto-rotation owns a market reference list."""

    validated = validate_strategy_parameters(
        "intraday-targeted-t", targeted_parameters()
    )
    assert "market_reference_symbols" not in validated


def test_auto_rotation_continuity_filter_matches_lookback() -> None:
    invalid = targeted_parameters()
    invalid["minimum_positive_steps"] = 6
    invalid["maximum_one_minute_move"] = "0.01"
    invalid["entry_order_timeout_seconds"] = 45
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters("intraday-auto-rotation", invalid)


def test_auto_rotation_normalizes_reference_symbols() -> None:
    parameters = targeted_parameters()
    parameters.update(
        {
            "minimum_positive_steps": 3,
            "maximum_one_minute_move": "0.01",
            "entry_order_timeout_seconds": 45,
            "market_reference_symbols": [" spy ", "qqq"],
        }
    )
    validated = validate_strategy_parameters(
        "intraday-auto-rotation", parameters
    )
    assert validated["market_reference_symbols"] == ["SPY", "QQQ"]


def test_auto_rotation_rejects_duplicate_reference_symbols() -> None:
    parameters = targeted_parameters()
    parameters.update(
        {
            "minimum_positive_steps": 3,
            "maximum_one_minute_move": "0.01",
            "entry_order_timeout_seconds": 45,
            "market_reference_symbols": ["SPY", " spy "],
        }
    )
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters("intraday-auto-rotation", parameters)


def test_auto_rotation_rejects_a_non_list_reference_field() -> None:
    parameters = targeted_parameters()
    parameters.update(
        {
            "minimum_positive_steps": 3,
            "maximum_one_minute_move": "0.01",
            "entry_order_timeout_seconds": 45,
            "market_reference_symbols": "SPY",
        }
    )
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters("intraday-auto-rotation", parameters)


def test_auto_rotation_defaults_are_filled_in() -> None:
    """The three optional auto-rotation keys are defaulted, not required."""

    validated = validate_strategy_parameters(
        "intraday-auto-rotation", targeted_parameters()
    )
    assert validated["market_reference_symbols"] == []
    assert validated["minimum_positive_steps"] == 0
    assert validated["maximum_one_minute_move"] == "1.0"
    assert validated["entry_order_timeout_seconds"] == 90


def test_intraday_momentum_band_must_be_ordered() -> None:
    invalid = targeted_parameters()
    invalid["minimum_momentum"] = "0.03"
    invalid["maximum_momentum"] = "0.01"
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters("intraday-targeted-t", invalid)


# -- integer handling -----------------------------------------------------


def test_a_bool_is_not_an_integer() -> None:
    """``isinstance(True, int)`` is true, so it must be refused explicitly."""

    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend",
            {"short_window": True, "long_window": 100, "whole_shares": True},
        )


def test_a_non_integral_value_is_refused() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend",
            {
                "short_window": "twenty",
                "long_window": 100,
                "whole_shares": True,
            },
        )


def test_a_missing_integer_is_refused() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "dual-ma-trend", {"long_window": 100, "whole_shares": True}
        )


def test_integer_list_rejects_a_scalar() -> None:
    with pytest.raises(StrategyParameterError):
        validate_strategy_parameters(
            "sector-momentum",
            {
                "lookbacks": 63,
                "rebalance_days": [5],
                "max_holdings": [3],
                "whole_shares": True,
                "max_gross_risk_pct": 0.5,
            },
        )


# -- unknown families -----------------------------------------------------


def test_an_unknown_family_only_requires_a_mapping() -> None:
    """A custom strategy is carried through unchanged, as it always was."""

    assert validate_strategy_parameters(
        "legacy-sector-momentum", {"legacy_artifact": True}
    ) == {"legacy_artifact": True}


def test_validation_does_not_mutate_the_caller_mapping() -> None:
    parameters = {"short_window": 20, "long_window": 100}
    validated = validate_strategy_parameters("dual-ma-trend", parameters)
    assert parameters == {"short_window": 20, "long_window": 100}
    assert validated is not parameters


# -- summaries ------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy_id",
    [
        "buy-hold",
        "dual-ma-trend",
        "donchian-breakout",
        "rsi-mean-reversion",
        "sector-momentum",
        "intraday-targeted-t",
        "intraday-auto-rotation",
    ],
)
def test_every_known_family_has_a_summary(strategy_id: str) -> None:
    summary = strategy_schema_summary(strategy_id)
    assert summary
    assert "自定义策略" not in summary


def test_an_unknown_family_says_it_has_no_factory() -> None:
    assert "自定义策略" in strategy_schema_summary("not-a-family")
