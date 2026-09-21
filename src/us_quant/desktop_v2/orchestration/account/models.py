"""Immutable facts the account orchestrator consumes and publishes.

This module is deliberately Qt-free.  Everything here is either an *input* the
composition root pushes in (the exposure multipliers it owns the source of) or
an *output* the window renders (the shell header facts, and the runtime events
the account layer wants recorded).

Two boundaries are load-bearing:

* ``AccountPresentationInputs`` exists so the account route never has to import
  the risk or strategy layer to find out how wide a position is presented.  The
  window knows how the multiplier is configured; it hands over a finished,
  immutable mapping.
* ``AccountShellHealthView`` exists so the orchestrator never holds a badge.
  The global header belongs to the shell, so the orchestrator publishes text
  and state and the window does the painting.

Neither type holds a ``BrokerAccountApplication``, an ``AccountPage`` or any
widget: a fact that carried its owner would make the boundary unverifiable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AccountPresentationInputs:
    """The cross-domain presentation facts the account page draws with.

    ``exposure_multipliers`` is stored as a tuple of ``(symbol, multiplier)``
    pairs rather than a mapping, so a caller cannot hand over a dict and then
    keep mutating it behind the orchestrator's back -- which would silently
    change what the page renders without any render having been requested.
    ``AccountPresentationInputs.of(...)`` is the conversion from the window's
    own config-derived mapping.
    """

    exposure_multipliers: tuple[tuple[str, Decimal], ...] = ()

    @classmethod
    def of(
        cls, multipliers: dict[str, Decimal] | None = None
    ) -> "AccountPresentationInputs":
        """Freeze a mapping into the immutable form the boundary carries."""

        return cls(
            exposure_multipliers=tuple(
                (str(symbol), Decimal(value))
                for symbol, value in (multipliers or {}).items()
            )
        )

    def multiplier_for(self, symbol: str) -> Decimal:
        """The presentation multiplier for one symbol, defaulting to ``1``."""

        for key, value in self.exposure_multipliers:
            if key == symbol:
                return value
        return Decimal("1")

    def as_mapping(self) -> dict[str, Decimal]:
        """The page-facing projection: the page renders a mapping."""

        return dict(self.exposure_multipliers)


@dataclass(frozen=True, slots=True)
class AccountRuntimeEvent:
    """One runtime event the orchestrator wants recorded.

    The orchestrator does not hold the event store: it asks, and the window
    writes.  That keeps ``Account -> System`` from becoming a dependency.
    """

    severity: str
    component: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AccountShellHealthView:
    """The global header facts one successful account read produces.

    ``handshake_*`` and ``account_*`` are ``None`` when that badge must be left
    alone.  An account refresh only ever *promotes* the handshake badge, and a
    failure must not demote it, so a view that always carried a value would
    quietly change that rule.
    """

    handshake_text: str | None = None
    handshake_state: str | None = None
    handshake_tooltip: str | None = None
    account_text: str | None = None
    account_state: str | None = None


__all__ = [
    "AccountPresentationInputs",
    "AccountRuntimeEvent",
    "AccountShellHealthView",
]
