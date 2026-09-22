"""The canonical owner of the research *scenario* capital scalar.

Nothing else lives here:

**Research Scenario Capital** is the initial-equity figure that historical
research, replay, scan affordability and cross-sectional portfolio research use
as a scenario.  It is deliberately *not*:

* an IBKR Paper ``NetLiquidation``;
* a live account balance, buying power or available funds;
* risk capital, position funding or a capital-allocation authority.

**This is research-only.**  When a real ``CapitalAllocator`` arrives it must be
computed from fresh broker/account truth, strategy allocations and portfolio
risk constraints -- never by promoting this scalar.  The name is chosen so that
promotion is not a rename: research scenario dollars and orderable live dollars
must stay distinguishable by a grep.

Why this object exists at all, given the rule that a shared abstraction needs a
real repeated consumer: the window's private ``self._research_capital_value``
had already become a genuine cross-workflow fact.  Seven workflows read it --
Cross Section research, the Scanner's manual run inputs, AutoQuant candidate
preparation, the market watchlist selection fallback, Targeted replay, Targeted
robustness, and the Account page's presentation of a scenario number.  A scalar
with seven consumers and no single owner is exactly what belongs here.

What it is **not**:

* not an event bus.  :meth:`set` reports whether the value moved; publishing
  that fact is the caller's business, and there is no listener registry;
* not a settings store, a config view or a widget.  It holds no ``AppConfig``
  and no ``QSpinBox``, so it cannot become a second way to edit configuration;
* not a ``ResearchState`` bag.  One ``int`` lives here.  Its only projection is
  :attr:`decimal_value`, because research runs on ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal


class ResearchScenarioCapitalState:
    """One integer scalar: the research scenario capital, owned once."""

    def __init__(self, initial_value: int) -> None:
        self._value = int(initial_value)

    @property
    def value(self) -> int:
        """The scenario capital as the page control and the card present it."""

        return self._value

    @property
    def decimal_value(self) -> Decimal:
        """The same value as ``Decimal``, which is what research runs on.

        An exact conversion from the ``int``: no float round-trip is involved,
        so the value a caller freezes at request time is the value the executor
        receives.
        """

        return Decimal(self._value)

    def set(self, value: int) -> bool:
        """Adopt ``value`` and report whether it actually changed.

        Returning the change rather than emitting an event keeps this class a
        scalar.  A caller that wants to publish the change -- the Cross Section
        capability does, so the Account presentation follows it -- owns that
        decision and that fan-out.
        """

        value = int(value)
        changed = value != self._value
        self._value = value
        return changed


__all__ = ["ResearchScenarioCapitalState"]
