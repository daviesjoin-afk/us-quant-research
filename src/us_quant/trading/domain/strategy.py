"""Strategy domain: identity, governance versions, and the proposal hand-off.

Two boundaries are load-bearing here.

**A strategy's output is a proposal, not an order.**  ``TradeProposal`` is an
*intent to trade*.  ``OrderIntent`` carries an ``order_id`` and a
``client_order_id``, which means it can be submitted; a proposal has neither,
so a strategy physically cannot reach the broker.  The risk engine must
approve it and the execution service must turn it into an ``OrderIntent``.
Keeping the two distinct is what lets a later change delete AutoQuant's
``order_sink`` callback without redesigning the strategy surface.

**A version is immutable and its times are absolute.**  ``StrategyVersion`` is
a frozen record of one governed parameter set.  ``created_at`` / ``updated_at``
must be timezone-aware and are never silently repaired: an ambiguous timestamp
in the governance audit trail is worse than a refusal, because it makes two
different events sort the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping

from us_quant.trading.domain.common import ZERO, freeze_parameters

#: A strategy risk budget may never exceed 10% of the account.
MAX_RISK_BUDGET_PCT = Decimal("0.10")


class TradeAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class StrategyStatus(StrEnum):
    """Governance lifecycle of one strategy version.

    ``STOPPED`` is terminal and ``LEGACY_INVALIDATED`` is permanently terminal;
    neither is recoverable, by design.  A stopped version may be cloned into a
    new version, but the stopped version itself never runs again.
    """

    RESEARCH = "research"
    PAPER_SHADOW = "paper_shadow"
    PAUSED = "paused"
    STOPPED = "stopped"
    LEGACY_INVALIDATED = "legacy_invalidated"


class StrategyMode(StrEnum):
    RESEARCH = "research"
    PAPER_SHADOW = "paper_shadow"


#: The complete state machine, moved verbatim from ``StrategyRegistry``.
#:
#: Behaviour is unchanged on purpose.  The two properties worth stating out
#: loud: ``STOPPED`` cannot transition anywhere, ``LEGACY_INVALIDATED`` cannot
#: transition anywhere, and entering ``PAPER_SHADOW`` additionally requires
#: ``gate_passed`` -- the table below is only the structural half of that rule.
ALLOWED_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.RESEARCH: frozenset(
        {StrategyStatus.PAPER_SHADOW, StrategyStatus.STOPPED}
    ),
    StrategyStatus.PAPER_SHADOW: frozenset(
        {StrategyStatus.PAUSED, StrategyStatus.STOPPED}
    ),
    StrategyStatus.PAUSED: frozenset(
        {StrategyStatus.PAPER_SHADOW, StrategyStatus.STOPPED}
    ),
    StrategyStatus.STOPPED: frozenset(),
    StrategyStatus.LEGACY_INVALIDATED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class StrategyIdentity:
    """The three fields that name a strategy version without describing it.

    Deliberately narrow: no ``status``, no ``mode``, no ``semver``.  An
    identity that carried governance state would change meaning every time the
    version was transitioned, and anything that keyed on it would silently
    start pointing at a different thing.
    """

    strategy_id: str
    version_id: str
    parameter_hash: str


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """The human-facing half of a strategy: what it is called and why."""

    strategy_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """One immutable, governed strategy version."""

    definition: StrategyDefinition
    identity: StrategyIdentity

    semver: str
    status: StrategyStatus
    mode: StrategyMode

    parameters: Mapping[str, Any]

    universe_hash: str
    code_hash: str

    risk_budget_pct: Decimal

    gate_passed: bool
    gate_reason: str

    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.definition.strategy_id != self.identity.strategy_id:
            raise ValueError(
                "definition and identity must describe the same strategy"
            )
        if not isinstance(self.status, StrategyStatus):
            raise TypeError("status must be a StrategyStatus")
        if not isinstance(self.mode, StrategyMode):
            raise TypeError("mode must be a StrategyMode")
        # Normalise to a real ``dict``.  The declared type is ``Mapping`` so
        # callers can hand in anything mapping-shaped, but JSON serialisation
        # (and therefore the parameter hash) requires a concrete dict.
        #
        # Frozen, not merely copied.  ``frozen=True`` refuses attribute
        # rebinding but a plain dict field can still be edited in place, and
        # ``parameter_hash`` is read off the identity rather than recomputed --
        # so an in-place edit would leave a governed version whose declared hash
        # no longer describes its own parameters.  ``freeze_parameters`` also
        # freezes the nested lists, which is the half a shallow copy misses.
        object.__setattr__(
            self, "parameters", freeze_parameters(dict(self.parameters))
        )
        if not isinstance(self.risk_budget_pct, Decimal):
            raise TypeError("risk_budget_pct must be a Decimal")
        if not ZERO < self.risk_budget_pct <= MAX_RISK_BUDGET_PCT:
            raise ValueError("strategy risk budget must be in (0, 10%]")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")

    # -- read-only conveniences ------------------------------------------
    #
    # These exist so consumers migrating off the retired ``StrategyRecord``
    # can keep reading ``version.strategy_id`` instead of
    # ``version.definition.strategy_id``.  They are projections, not a
    # compatibility shim: nothing here re-creates the old row type.

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def version_id(self) -> str:
        return self.identity.version_id

    @property
    def parameter_hash(self) -> str:
        return self.identity.parameter_hash


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """An intent to trade.  Never an order.

    It carries no ``order_id``, ``broker_order_id``, ``client_order_id``,
    ``tif``, ``outsideRth``, ``transmit`` or ``risk_approved``, and none may be
    added: any one of them would make this type submittable and collapse the
    boundary the risk engine and execution service exist to enforce.
    """

    strategy: StrategyIdentity
    symbol: str
    action: TradeAction
    desired_quantity: int
    reference_price: Decimal
    reason: str
    generated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.action, TradeAction):
            raise TypeError("action must be a TradeAction")
        if not self.symbol.strip():
            raise ValueError("proposal symbol must not be empty")
        if not isinstance(self.reference_price, Decimal):
            raise TypeError("reference price must be a Decimal")
        if self.reference_price <= ZERO:
            raise ValueError("reference price must be positive")
        # ``isinstance(True, int)`` is true in Python, so a bool would sail
        # straight through a plain ``> 0`` check and become a quantity of one.
        if isinstance(self.desired_quantity, bool) or not isinstance(
            self.desired_quantity, int
        ):
            raise ValueError("desired quantity must be a whole number")
        if self.action is TradeAction.HOLD:
            if self.desired_quantity != 0:
                raise ValueError(
                    "a HOLD proposal must carry a zero quantity"
                )
        elif self.desired_quantity <= 0:
            raise ValueError(
                "a BUY or SELL proposal must carry a positive quantity"
            )
        _require_aware(self.generated_at, "generated_at")


def canonical_parameters_json(parameters: Mapping[str, Any]) -> str:
    """The canonical JSON form that parameter identity is taken over.

    Reproduced character for character from ``StrategyRegistry`` -- sorted
    keys, no whitespace, no ASCII escaping.  Every already-governed version
    has its ``parameter_hash`` stored in the database and referenced by
    backtest artifacts, so changing any one of these four arguments would
    silently re-hash the entire catalogue and orphan that evidence.
    """

    return json.dumps(
        dict(parameters),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parameter_hash_for(parameters: Mapping[str, Any]) -> str:
    """The SHA-256 identity of a parameter set."""

    return sha256(
        canonical_parameters_json(parameters).encode("utf-8")
    ).hexdigest()


def _require_aware(value: datetime, name: str) -> None:
    """Refuse a naive timestamp instead of assuming a timezone for it.

    Guessing UTC here would silently re-date an operator's action; the caller
    knows which zone the clock was in, so the domain declines to invent one.
    """

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "MAX_RISK_BUDGET_PCT",
    "StrategyDefinition",
    "StrategyIdentity",
    "StrategyMode",
    "StrategyStatus",
    "StrategyVersion",
    "TradeAction",
    "TradeProposal",
    "canonical_parameters_json",
    "parameter_hash_for",
]
