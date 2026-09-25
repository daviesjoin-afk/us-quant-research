"""Immutable facts the strategy governance orchestrator publishes.

Deliberately Qt-free, and deliberately small: the governance route moves text
and one runtime event across the window boundary, and everything else it
passes is a live :class:`StrategyVersion` owned by the application service.

``StrategyRuntimeEvent`` exists for the same reason its Account/Market/Paper
cousins do: the capability decides *what* happened, the window forwards it
through the one runtime-event adapter, and the Runtime Events store decides
how it is persisted.  The orchestrator never holds the store, so
``Strategy -> System`` never becomes a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyRuntimeEvent:
    """One runtime event the orchestrator wants recorded.

    The four fields are the exact fields the generic window router reads; a
    fifth field here would silently stop being forwarded, which is why this
    type carries nothing else.
    """

    severity: str
    component: str
    code: str
    message: str


__all__ = ["StrategyRuntimeEvent"]
