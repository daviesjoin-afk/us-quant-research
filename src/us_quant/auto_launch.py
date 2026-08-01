"""Immutable inputs for an asynchronous Paper AutoQuant launch."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class AutoLaunchPlan:
    """The user-approved inputs that must survive broker connection."""

    attempt_id: int
    strategy_version_id: str
    parameter_hash: str
    candidate_symbols: tuple[str, ...]
    requested_capital_limit: Decimal


def build_auto_launch_plan(
    *,
    attempt_id: int,
    strategy_version_id: str,
    parameter_hash: str,
    candidate_symbols: Iterable[str],
    requested_capital_limit: Decimal,
) -> AutoLaunchPlan:
    """Build a normalized, immutable launch-plan fingerprint."""

    return AutoLaunchPlan(
        attempt_id=attempt_id,
        strategy_version_id=strategy_version_id,
        parameter_hash=parameter_hash,
        candidate_symbols=tuple(str(symbol).upper() for symbol in candidate_symbols),
        requested_capital_limit=Decimal(requested_capital_limit),
    )


def auto_launch_plan_matches(
    plan: AutoLaunchPlan,
    *,
    strategy_version_id: str,
    parameter_hash: str,
    candidate_symbols: Iterable[str],
    requested_capital_limit: Decimal,
) -> bool:
    """Return whether mutable UI inputs still match a confirmed plan."""

    return (
        plan.strategy_version_id == strategy_version_id
        and plan.parameter_hash == parameter_hash
        and plan.candidate_symbols
        == tuple(str(symbol).upper() for symbol in candidate_symbols)
        and plan.requested_capital_limit == Decimal(requested_capital_limit)
    )
