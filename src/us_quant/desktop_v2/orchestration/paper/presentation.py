"""The Paper session's *presentation* fact: one immutable, retained view.

v2O-E4 exists to keep two things apart that look almost identical from the inside:

* ``PaperWorkflowController.result`` is the **canonical lifecycle result**.  It is the
  session truth, every operation replaces it, and ``finalize_if_safe()`` clears it as
  part of releasing PAPER -- correctly, because a released session has no current
  result to report;
* :class:`PaperPresentationSnapshot` is the **last officially published presentation
  fact**: a projection of one result, immutable, and deliberately *not* cleared when
  the canonical result is.  Without it the execution route would blank out at the exact
  moment the operator most wants to read what just happened.

The same split is true of every other fact this capability touches.  The snapshot is
**not** workflow truth, **not** broker truth, **not** execution truth and **not** risk
truth, and it is not a lifecycle state machine under another name.  It is not a
business input either: no launch gate, no stop, no reconciliation, no finalization, no
ownership or lease decision, and no risk or execution path may read it.  It answers
exactly one question -- *what should the execution route draw for the last Paper
session?* -- and every other question about a Paper session is still answered by the
workflow, the order-service owner or the broker.

Only the facts the route actually draws are projected.  ``PaperSessionResult`` carries
more -- the health verdict, the reconciliation and broker counts, the events -- and
none of it is copied here, because a field-for-field mirror of a result *is* the second
truth this round removes, and every unused field is a reason for one.  What the route
draws was established by reading the page's own presenter and row builders, not by
copying the result:

* ``runtime_status``      -> ``active``, ``entries_paused``, ``stop_requested``, ``status``
* ``account_metrics``     -> ``estimated_equity``, ``estimated_realized_pnl``,
                             ``estimated_unrealized_pnl``
* ``position_metric``     -> ``positions``, ``pending_orders``, ``trades_today``
* ``summary_text``        -> ``status``, ``candidate_count``, ``initial_equity``,
                             ``session_id``
* ``position_rows``       -> each position's ``symbol``/``quantity``/``average_price``/
                             ``opened_at``/``provider``
* ``fill_rows``           -> each fill's ``occurred_at``/``symbol``/``side``/``quantity``/
                             ``price``/``estimated_commission``/``realized_pnl``
* ``shadow_rows``         -> each pending order's ``execution_symbol`` and ``limit_price``

The rows are carried as their own plain facts rather than as the engine's objects.
That is the point of the projection: the retained view cannot keep a live runtime
snapshot -- and through it an armed session's order intents -- reachable from a route
that is supposed to be drawing strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from us_quant.trading.runtime.paper_models import PaperSessionResult


@dataclass(frozen=True, slots=True)
class PaperPositionFact:
    """One holding the session's own book reports, detached from the runtime."""

    symbol: str
    quantity: int
    average_price: Decimal
    opened_at: str
    provider: str


@dataclass(frozen=True, slots=True)
class PaperPendingOrderFact:
    """One order the session is holding, reduced to what the tables draw.

    ``execution_symbol`` rather than ``symbol`` because that is the name the armed
    symbol carries -- the substitution the session trades under -- and the row it
    feeds is keyed by the symbol the quotes arrive under.
    """

    execution_symbol: str
    limit_price: Decimal


@dataclass(frozen=True, slots=True)
class PaperFillFact:
    """One execution the session recorded, detached from the runtime."""

    occurred_at: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    estimated_commission: Decimal
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class PaperPresentationSnapshot:
    """What the execution route should draw for the last published Paper session.

    Built only by :func:`project_presentation`, from a ``PaperSessionResult`` the
    workflow produced.  There is deliberately no constructor path that patches a field:
    ``active=False`` or ``stop_requested=True`` written by hand would be a *fabricated
    transition*, and every reader would then be reading a guess about a session rather
    than a fact about one.

    The two fallbacks the cards need are optional on purpose.  ``estimated_*`` and
    ``initial_equity`` are the *local* figures the cards show only when neither the
    broker nor the read-only account snapshot has a number, so ``None`` is a real
    answer ("this session has no local estimate") rather than a missing value.
    """

    session_id: str
    status: str
    active: bool
    entries_paused: bool
    stop_requested: bool
    candidate_count: int
    trades_today: int
    initial_equity: Decimal | None
    estimated_equity: Decimal | None
    estimated_realized_pnl: Decimal | None
    estimated_unrealized_pnl: Decimal | None
    positions: tuple[PaperPositionFact, ...]
    pending_orders: tuple[PaperPendingOrderFact, ...]
    fills: tuple[PaperFillFact, ...]


def _text(value: object) -> str:
    """One display string, with a missing session id reading as "no session"."""

    return "" if value is None else str(value)


def _decimal(value: object) -> Decimal | None:
    """``None`` stays ``None``; everything else crosses as a ``Decimal``.

    Decimal rather than float for the same reason the capital chain is: converting
    here would apply a rounding rule nobody chose.
    """

    return None if value is None else Decimal(str(value))


def _amount(value: object) -> Decimal:
    """A figure the row draws as a number; a missing one reads as zero.

    Distinct from :func:`_decimal` on purpose: the cards *want* ``None`` (``不可用`` is a
    real answer for a P&L nobody reported), while a row's price or quantity is a column
    that has to print something.
    """

    projected = _decimal(value)
    return Decimal("0") if projected is None else projected


def _positions(snapshot: object) -> tuple[PaperPositionFact, ...]:
    return tuple(
        PaperPositionFact(
            symbol=str(getattr(row, "symbol", "")),
            quantity=int(getattr(row, "quantity", 0)),
            average_price=_amount(getattr(row, "average_price", None)),
            opened_at=_text(getattr(row, "opened_at", "")),
            provider=_text(getattr(row, "provider", "")),
        )
        for row in (getattr(snapshot, "positions", ()) or ())
    )


def _pending_orders(snapshot: object) -> tuple[PaperPendingOrderFact, ...]:
    return tuple(
        PaperPendingOrderFact(
            execution_symbol=str(getattr(row, "execution_symbol", "")),
            limit_price=_amount(getattr(row, "limit_price", None)),
        )
        for row in (getattr(snapshot, "pending_orders", ()) or ())
    )


def _fills(snapshot: object) -> tuple[PaperFillFact, ...]:
    return tuple(
        PaperFillFact(
            occurred_at=_text(getattr(row, "occurred_at", "")),
            symbol=str(getattr(row, "symbol", "")),
            side=str(getattr(row, "side", "")),
            quantity=int(getattr(row, "quantity", 0)),
            price=_amount(getattr(row, "price", None)),
            estimated_commission=_amount(getattr(row, "estimated_commission", None)),
            realized_pnl=_decimal(getattr(row, "realized_pnl", None)),
        )
        for row in (getattr(snapshot, "fills", ()) or ())
    )


def project_presentation(
    result: PaperSessionResult,
) -> PaperPresentationSnapshot | None:
    """Project one published result into the presentation fact, or ``None``.

    ``None`` means the result carried no engine snapshot, so there is nothing to draw.
    It is returned rather than raised because a publication with nothing displayable is
    **not** a reason to blank a page: the caller keeps the fact it already had, which is
    the same rule that keeps a finalized session on screen.

    Pure: one result in, one immutable view out.  No service, no repository, no broker,
    no Qt, no clock and no I/O -- which is what makes "the retained view is a projection
    of a result" checkable instead of asserted.
    """

    snapshot = getattr(result, "engine_snapshot", None)
    if snapshot is None:
        return None
    return PaperPresentationSnapshot(
        session_id=_text(getattr(snapshot, "session_id", None)),
        status=_text(getattr(snapshot, "status", "")),
        active=bool(getattr(snapshot, "active", False)),
        entries_paused=bool(getattr(snapshot, "entries_paused", False)),
        stop_requested=bool(getattr(snapshot, "stop_requested", False)),
        candidate_count=int(getattr(snapshot, "candidate_count", 0)),
        trades_today=int(getattr(snapshot, "trades_today", 0)),
        initial_equity=_decimal(getattr(snapshot, "initial_equity", None)),
        estimated_equity=_decimal(getattr(snapshot, "estimated_equity", None)),
        estimated_realized_pnl=_decimal(
            getattr(snapshot, "estimated_realized_pnl", None)
        ),
        estimated_unrealized_pnl=_decimal(
            getattr(snapshot, "estimated_unrealized_pnl", None)
        ),
        positions=_positions(snapshot),
        pending_orders=_pending_orders(snapshot),
        fills=_fills(snapshot),
    )


__all__ = [
    "PaperFillFact",
    "PaperPendingOrderFact",
    "PaperPositionFact",
    "PaperPresentationSnapshot",
    "project_presentation",
]
