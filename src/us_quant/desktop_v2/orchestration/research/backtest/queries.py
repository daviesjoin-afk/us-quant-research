"""The pure rules behind one backtest request, with no Qt and no window.

Three decisions used to live inline in ``MainWindow``: which strategy versions
the page offers and in what order, which versions a click means to run, and how
a form draft becomes a ``BacktestRequest``.  All three are rules, not UI and not
orchestration, so they belong in a module that can be read -- and tested --
without starting a Qt event loop.

The rules are moved, not rewritten.  Every one of them is a verbatim lift of the
code that was in the window, including the parts that look incidental:

* the family order is ``STRATEGY_SPECS`` order, with unknown families sorted
  last (the ``999`` sentinel) and ``semver`` as the tiebreak within a family;
* ``compare_all`` takes the **first** version per ``strategy_id`` from the list
  it is given.  That is "newest" only because the selection service already
  returns newest-first -- this module reads the ordering it is handed rather
  than re-deriving it, so a change to the service's ordering does not need a
  second rule here;
* a single run matches ``selected_version_id`` exactly, and a miss is an empty
  result rather than a fallback to the newest version.  Running a version the
  operator did not pick would be a silent substitution;
* the draft-to-request conversion keeps every ``Decimal`` call and the
  ``target_weight_percent / 100`` division at the same precision.  Backtest
  numbers are research results; nothing here rounds, coerces to ``float`` or
  reformats.

The final ordering of a ``compare_all`` batch follows ``STRATEGY_SPECS`` rather
than the input order, so the comparison table reads the same way every run.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from us_quant.backtest_workspace import (
    STRATEGY_SPECS,
    BacktestRequest,
)
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestFormDraft,
    BacktestStrategyOption,
)
from us_quant.trading.domain.strategy import StrategyVersion

#: The sort position given to a strategy family that is not in
#: ``STRATEGY_SPECS``.  Kept from the window's inline sort: a governed family
#: with no executable backtest factory sorts after the ones that can run,
#: rather than being dropped.
UNKNOWN_FAMILY_ORDER = 999


def _family_order() -> dict[str, int]:
    return {
        spec.strategy_id: index
        for index, spec in enumerate(STRATEGY_SPECS)
    }


def strategy_options(
    versions: Sequence[StrategyVersion],
) -> tuple[BacktestStrategyOption, ...]:
    """Project versions into the combo options the page displays.

    Ordered by ``STRATEGY_SPECS`` family order and then by ``semver``, so the
    combo groups a family together instead of interleaving whatever order the
    repository returned.
    """

    order = _family_order()
    ordered = sorted(
        versions,
        key=lambda item: (
            order.get(item.strategy_id, UNKNOWN_FAMILY_ORDER),
            item.semver,
        ),
    )
    return tuple(
        BacktestStrategyOption(
            version.version_id,
            f"{version.name} · {version.semver}",
        )
        for version in ordered
    )


def select_backtest_versions(
    versions: Sequence[StrategyVersion],
    *,
    compare_all: bool,
    selected_version_id: str,
) -> tuple[StrategyVersion, ...]:
    """The versions one click means to run.

    ``compare_all`` runs one version per strategy family -- the first one seen
    for each ``strategy_id``, which is the newest because the caller hands over
    the selection service's newest-first list -- and orders the batch by
    ``STRATEGY_SPECS`` so the comparison table is stable.

    Otherwise it runs exactly the requested version.  An id that matches
    nothing yields an empty tuple: the caller refuses the request, which is
    better than quietly running a different version.
    """

    if not compare_all:
        return tuple(
            version
            for version in versions
            if version.version_id == selected_version_id
        )

    newest: dict[str, StrategyVersion] = {}
    for version in versions:
        newest.setdefault(version.strategy_id, version)
    return tuple(
        newest[spec.strategy_id]
        for spec in STRATEGY_SPECS
        if spec.strategy_id in newest
    )


def build_backtest_requests(
    versions: Sequence[StrategyVersion],
    draft: BacktestFormDraft,
) -> tuple[BacktestRequest, ...]:
    """Turn the frozen form draft plus the chosen versions into requests.

    Every field is taken from the version's governance identity (the parameter
    and code hashes are what make a run reproducible) and every numeric field
    from the draft's text through ``Decimal``.  The precision is the contract:
    the ``Decimal`` conversions and the ``/ 100`` on the target weight are
    reproduced exactly as the window performed them.
    """

    return tuple(
        BacktestRequest(
            strategy_id=version.strategy_id,
            strategy_version_id=version.version_id,
            parameter_hash=version.parameter_hash,
            code_hash=version.code_hash,
            parameters=version.parameters,
            symbol=draft.symbol,
            start_date=draft.start_date,
            end_date=draft.end_date,
            initial_equity=Decimal(draft.initial_equity),
            target_weight=(
                Decimal(draft.target_weight_percent) / Decimal("100")
            ),
            per_share_commission=Decimal(draft.per_share_commission),
            minimum_commission=Decimal(draft.minimum_commission),
            slippage_bps=Decimal(draft.slippage_bps),
        )
        for version in versions
    )


__all__ = [
    "UNKNOWN_FAMILY_ORDER",
    "build_backtest_requests",
    "select_backtest_versions",
    "strategy_options",
]
