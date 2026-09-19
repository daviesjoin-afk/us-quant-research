"""Strategy selection: which version each runtime purpose is allowed to run.

Until now the answer to "which strategy version does the backtest use?" lived
in a Qt combo box.  ``QComboBox.currentData()`` was the truth, the filter rules
were inline in the window, and the same question asked from a non-UI caller had
no answer at all.  This service owns that answer.

Three things are deliberate:

* **Eligibility is a policy, not a widget state.**  ``BACKTEST``,
  ``TARGETED_SHADOW`` and ``AUTO_ROTATION`` each declare which strategy ids and
  which governance statuses they will accept.  A policy is data, so it can be
  asserted in a test instead of being inferred from a widget's item list.
* **Governance selection is not runtime selection.**  Browsing a version in the
  strategy page must not silently repoint the auto-rotation runtime at it.  The
  page has its own selected row; this service is only reachable through an
  explicit ``select`` call.
* **A version that stops being eligible stops being selected.**  If the chosen
  version is later stopped or invalidated, ``selected`` declines to return it
  and ``restore_or_default`` picks the newest eligible replacement (or
  nothing).  A stopped strategy that kept being returned by the selector would
  be a stopped strategy that still runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from us_quant.trading.application.strategies import (
    StrategyApplication,
    StrategyNotFoundError,
)
from us_quant.trading.domain.strategy import (
    StrategyStatus,
    StrategyVersion,
)

#: The strategy families the backtest workspace can actually execute.
#:
#: Mirrors ``backtest_workspace.STRATEGY_SPECS``.  It is spelled out here
#: rather than imported so the application layer keeps no dependency on the
#: research workspace, and
#: ``test_trading_strategy_selection`` asserts the two sets are equal -- the
#: mirror cannot drift silently.
#: ``sector-momentum`` is absent on purpose: it is governed but has no
#: executable backtest factory.
BACKTEST_STRATEGY_IDS = frozenset(
    {
        "buy-hold",
        "dual-ma-trend",
        "donchian-breakout",
        "rsi-mean-reversion",
    }
)

TARGETED_SHADOW_STRATEGY_IDS = frozenset({"intraday-targeted-t"})
AUTO_ROTATION_STRATEGY_IDS = frozenset({"intraday-auto-rotation"})

#: Statuses a version may be *run* from.  ``PAUSED`` mirrors what the retired
#: combo filter accepted (``research`` / ``paper_shadow``) -- a paused version
#: is not runnable and must be resumed first.
RUNNABLE_STATUSES = frozenset(
    {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
)


class StrategySelectionPurpose(StrEnum):
    BACKTEST = "backtest"
    TARGETED_SHADOW = "targeted_shadow"
    AUTO_ROTATION = "auto_rotation"


@dataclass(frozen=True, slots=True)
class StrategySelectionPolicy:
    """Which versions a purpose will accept."""

    strategy_ids: frozenset[str]
    statuses: frozenset[StrategyStatus]

    def accepts(self, version: StrategyVersion) -> bool:
        return (
            version.strategy_id in self.strategy_ids
            and version.status in self.statuses
        )


SELECTION_POLICIES: dict[
    StrategySelectionPurpose, StrategySelectionPolicy
] = {
    StrategySelectionPurpose.BACKTEST: StrategySelectionPolicy(
        strategy_ids=BACKTEST_STRATEGY_IDS,
        statuses=frozenset({StrategyStatus.RESEARCH}),
    ),
    StrategySelectionPurpose.TARGETED_SHADOW: StrategySelectionPolicy(
        strategy_ids=TARGETED_SHADOW_STRATEGY_IDS,
        statuses=RUNNABLE_STATUSES,
    ),
    StrategySelectionPurpose.AUTO_ROTATION: StrategySelectionPolicy(
        strategy_ids=AUTO_ROTATION_STRATEGY_IDS,
        statuses=RUNNABLE_STATUSES,
    ),
}


class StrategySelectionError(RuntimeError):
    """The requested version may not be selected for this purpose."""


class StrategyNotFoundForSelection(StrategySelectionError):
    """The requested version does not exist."""


class StrategySelectionService:
    """Owns the runtime selection state for each purpose."""

    def __init__(self, strategies: StrategyApplication) -> None:
        self._strategies = strategies
        self._selected: dict[
            StrategySelectionPurpose, str
        ] = {}

    # -- policy ---------------------------------------------------------

    @staticmethod
    def policy(purpose: StrategySelectionPurpose) -> StrategySelectionPolicy:
        try:
            return SELECTION_POLICIES[purpose]
        except KeyError as error:
            raise StrategySelectionError(
                f"unknown selection purpose: {purpose!r}"
            ) from error

    # -- queries --------------------------------------------------------

    def options(
        self, purpose: StrategySelectionPurpose
    ) -> tuple[StrategyVersion, ...]:
        """Eligible versions, newest first.

        Ordered by ``created_at`` descending with ``version_id`` as a stable
        tiebreak, so the list does not depend on the order the repository
        happened to return rows in.
        """

        policy = self.policy(purpose)
        eligible = [
            version
            for version in self._strategies.list_versions()
            if policy.accepts(version)
        ]
        eligible.sort(
            key=lambda version: (version.created_at, version.version_id),
            reverse=True,
        )
        return tuple(eligible)

    def selected(
        self, purpose: StrategySelectionPurpose
    ) -> StrategyVersion | None:
        """The chosen version, or ``None``.

        Returns ``None`` -- and drops the stale choice -- when the version has
        since become ineligible.  Call ``restore_or_default`` to move on to a
        replacement.
        """

        version_id = self._selected.get(purpose)
        if version_id is None:
            return None
        try:
            version = self._strategies.get_version(version_id)
        except StrategyNotFoundError:
            self._selected.pop(purpose, None)
            return None
        if not self.policy(purpose).accepts(version):
            self._selected.pop(purpose, None)
            return None
        return version

    def restore_or_default(
        self, purpose: StrategySelectionPurpose
    ) -> StrategyVersion | None:
        """Return the current selection, else adopt the newest eligible one.

        ``None`` means the purpose genuinely has nothing runnable, which is a
        state the caller must handle rather than paper over.
        """

        current = self.selected(purpose)
        if current is not None:
            return current
        newest = self.options(purpose)
        if not newest:
            return None
        return self.select(purpose, newest[0].version_id)

    # -- commands -------------------------------------------------------

    def select(
        self,
        purpose: StrategySelectionPurpose,
        version_id: str,
    ) -> StrategyVersion:
        """Choose the version this purpose will run.

        Rejects a version that does not exist, belongs to a different strategy
        family, or is not in an eligible status.  The refusal is the point: an
        ``AUTO_ROTATION`` slot that accepted ``buy-hold`` would run a
        benchmark as a live rotation strategy.
        """

        try:
            version = self._strategies.get_version(version_id)
        except StrategyNotFoundError as error:
            raise StrategyNotFoundForSelection(version_id) from error
        policy = self.policy(purpose)
        if version.strategy_id not in policy.strategy_ids:
            raise StrategySelectionError(
                f"{purpose} does not accept strategy "
                f"{version.strategy_id!r}; expected one of "
                f"{sorted(policy.strategy_ids)}"
            )
        if version.status not in policy.statuses:
            raise StrategySelectionError(
                f"{purpose} does not accept status "
                f"{version.status!r}; the version must be one of "
                f"{sorted(status.value for status in policy.statuses)}"
            )
        self._selected[purpose] = version.version_id
        return version


__all__ = [
    "AUTO_ROTATION_STRATEGY_IDS",
    "BACKTEST_STRATEGY_IDS",
    "RUNNABLE_STATUSES",
    "SELECTION_POLICIES",
    "StrategyNotFoundForSelection",
    "StrategySelectionError",
    "StrategySelectionPolicy",
    "StrategySelectionPurpose",
    "StrategySelectionService",
    "TARGETED_SHADOW_STRATEGY_IDS",
]
