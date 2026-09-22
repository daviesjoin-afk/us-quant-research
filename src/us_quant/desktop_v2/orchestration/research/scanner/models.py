"""Immutable inputs the scanner capability consumes.

This module is deliberately Qt-free.  ``ScannerRunInputs`` is an *input* the
composition root builds and pushes in: the scanner's own runtime reads finished
facts, never ``AppConfig``, ``RiskApplication`` or a research-capital widget.

The type exists because the manual scan has two different time semantics that
must not be collapsed:

* the capital, the risk percentage and the substitution rules are a **UI-thread
  snapshot taken when the operator clicks 扫描**.  Changing the research capital
  slider while a scan is queued must not change the scan that is about to run;
* the universe is read **when the task actually executes**, because a refresh
  that landed while the task queued must be the universe that gets scanned.

So this type freezes exactly the first group.  ``substitutions`` is a tuple of
pairs rather than a dict so a caller cannot hand over a mapping and keep
mutating it behind the capability's back -- which would silently change what the
scan runs with after the freeze point.  ``of(...)`` is the conversion from the
window's own config-derived mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from us_quant.portfolio import SubstitutionRule


@dataclass(frozen=True, slots=True)
class ScannerRunInputs:
    """The frozen facts one scan request runs with.

    ``SubstitutionRule`` is itself frozen and slotted, so storing them in a
    frozen tuple keeps the whole value immutable: nothing here can be edited
    after the freeze point, which is what makes "the capital was captured at
    request time" a property of the data rather than a convention.
    """

    capital: Decimal
    max_position_risk_pct: Decimal
    substitutions: tuple[tuple[str, SubstitutionRule], ...] = ()

    @classmethod
    def of(
        cls,
        *,
        capital: Decimal,
        max_position_risk_pct: Decimal,
        substitutions: dict[str, SubstitutionRule] | None = None,
    ) -> "ScannerRunInputs":
        """Freeze the window's config-derived values into the carried form.

        The two ``Decimal`` fields are passed through rather than coerced:
        they are already immutable and already ``Decimal`` at the call site,
        so a conversion would only add a failure mode for values the window
        legitimately produces.
        """

        return cls(
            capital=capital,
            max_position_risk_pct=max_position_risk_pct,
            substitutions=tuple(
                (str(symbol), rule)
                for symbol, rule in (substitutions or {}).items()
            ),
        )

    def substitutions_mapping(self) -> dict[str, SubstitutionRule]:
        """The scanner-facing projection: ``scan_market`` takes a mapping.

        A fresh dict on every call, so the caller cannot retain it and mutate
        the frozen inputs through it.
        """

        return dict(self.substitutions)


__all__ = ["ScannerRunInputs"]
