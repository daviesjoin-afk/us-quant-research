"""Numeric primitives, the execution-environment enum, and frozen containers.

These are the lowest-level shared concepts.  The numeric half was moved verbatim
from the retired ``us_quant.domain`` module, so the arithmetic semantics that the
research, backtest and Paper paths already rely on are unchanged.

The frozen containers exist because a ``frozen=True`` dataclass is only half an
immutable value: it refuses *attribute rebinding* while a ``dict`` or ``list``
field can still be edited in place.  For a governed strategy version that
difference is load-bearing -- ``parameter_hash`` is read off the version's
identity rather than recomputed, so an in-place edit would leave a version whose
declared hash no longer describes its own parameters.

Two properties matter for these types, and both are deliberate:

* they are ``dict``/``list`` **subclasses**, not ``MappingProxyType`` or tuples.
  Existing consumers branch on ``isinstance(value, dict)`` / ``isinstance(value,
  list)``, compare against plain literals, and JSON-serialise the values; a
  ``MappingProxyType`` breaks ``copy.deepcopy`` and ``pickle``, and a tuple would
  change what those consumers see.  A subclass keeps every one of those working
  while refusing mutation.
* a copy stays frozen.  ``copy.deepcopy`` of a governed value must not hand back
  a mutable one, or the immutability would last only until someone copied it.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal(value: Decimal | str | int | float) -> Decimal:
    """Convert external numeric input without silently keeping binary floats."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class Environment(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class FrozenParameters(dict):
    """A ``dict`` that refuses every in-place mutation.

    Built through :func:`freeze_parameters`, which is what guarantees the nested
    values are frozen too.

    Population happens in ``__new__`` and ``__init__`` only *seals* the result.
    Overriding the mutating methods alone is not enough: ``dict.__init__`` is
    inherited, so ``params.__init__({...})`` would silently re-populate a mapping
    that is supposed to be immutable.  Sealing on the first ``__init__`` closes
    that route while leaving normal construction working.

    What this deliberately does not defend against is calling a base-class method
    *explicitly* -- ``dict.__setitem__(params, key, value)`` still writes.  That
    is the same boundary a frozen dataclass draws: ``object.__setattr__`` also
    bypasses ``frozen=True``.  Both are deliberate escapes from the type's
    contract, not the accidental in-place edit this type exists to prevent.
    """

    __slots__ = ("_sealed",)

    def __new__(cls, *args: Any, **kwargs: Any) -> "FrozenParameters":
        instance = super().__new__(cls)
        dict.__init__(instance, *args, **kwargs)
        return instance

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError(
                "strategy parameters are immutable once a version is governed"
            )
        object.__setattr__(self, "_sealed", True)

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(
            "strategy parameters are immutable once a version is governed"
        )

    __setitem__ = _refuse
    __delitem__ = _refuse
    __ior__ = _refuse
    clear = _refuse
    pop = _refuse
    popitem = _refuse
    setdefault = _refuse
    update = _refuse

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenParameters, (dict(self),))

    def __copy__(self) -> "FrozenParameters":
        return FrozenParameters(dict(self))

    def __deepcopy__(self, memo: dict) -> "FrozenParameters":
        from copy import deepcopy

        return FrozenParameters(deepcopy(dict(self), memo))


class FrozenList(list):
    """A ``list`` that refuses every in-place mutation.

    Sealed on first ``__init__`` for the same reason as
    :class:`FrozenParameters`: ``list.__init__`` is inherited, so
    ``items.__init__([...])`` would otherwise re-populate a frozen list.
    """

    __slots__ = ("_sealed",)

    def __new__(cls, *args: Any, **kwargs: Any) -> "FrozenList":
        instance = super().__new__(cls)
        list.__init__(instance, *args, **kwargs)
        return instance

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError(
                "strategy parameters are immutable once a version is governed"
            )
        object.__setattr__(self, "_sealed", True)

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(
            "strategy parameters are immutable once a version is governed"
        )

    __setitem__ = _refuse
    __delitem__ = _refuse
    __iadd__ = _refuse
    __imul__ = _refuse
    append = _refuse
    extend = _refuse
    insert = _refuse
    remove = _refuse
    pop = _refuse
    clear = _refuse
    sort = _refuse
    reverse = _refuse

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenList, (list(self),))

    def __copy__(self) -> "FrozenList":
        return FrozenList(list(self))

    def __deepcopy__(self, memo: dict) -> "FrozenList":
        from copy import deepcopy

        return FrozenList(deepcopy(list(self), memo))


def freeze_parameters(value: Any) -> Any:
    """Return ``value`` with every mapping and list replaced by a frozen one.

    Recursive on purpose: a shallow freeze would still alias the list a strategy
    runtime reads for its market reference symbols, which is the half that a
    scalar-only check misses.  Scalars are returned unchanged.
    """

    if isinstance(value, dict):
        return FrozenParameters(
            {key: freeze_parameters(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return FrozenList(freeze_parameters(item) for item in value)
    return value
