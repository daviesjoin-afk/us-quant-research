"""The execution route's session read model, as one pure projection.

``MainWindow._render_auto_quant_snapshot`` used to *assemble* this: it collected reason
from the broker's account, filtered the holdings down to the session's symbols, keyed the
session's pending orders by symbol, filtered the journal's audit rows by session id, and
only then handed the pile to :func:`~us_quant.desktop_v2.pages.execution.presenter.build_runtime_view`.
Assembling a read model is neither a fetch nor a draw -- it is a pure conversion of
already-read facts -- so v2O-E4 moved it here, where it can be tested without a window, a
service, a broker or a repository.

The split this module keeps is the one the round turns on:

* **the session** -- the immutable
  :class:`~us_quant.desktop_v2.orchestration.paper.presentation.PaperPresentationSnapshot`
  the Paper capability retains and publishes -- is the only session fact that reaches this
  module.  It may be stale *by design*: it survives ``finalize_if_safe`` clearing the
  canonical result, which is exactly why the route still has something to draw after a
  session ends.  Nothing here may draw an inference about a *live* session from it -- not
  "a launch is allowed", not "ownership is held", not "the broker is flat";
* **everything else is ambient route context**: quotes, the candidate shortlist, the
  read-only account snapshot, the broker's own account reading, and the journal rows.  All
  of it is a fact about *now*, fetched by the composition root and handed in.

Two consequences are deliberate rather than incidental:

* the module is Qt-free, service-free and repository-free, and it imports **no
  orchestrator**.  The session view arrives as an already-built immutable value and is
  read structurally, which is the same convention ``presenter`` and ``rows`` already use
  for the snapshot they draw.  ``test_desktop_paper_presentation_closure`` pins the field
  names read here to the model that produces them, so a structural read cannot drift
  silently;
* **the row budgets are the route's own.**  How many journal rows the orders and latency
  tables ask for is a presentation decision about what the operator can read, so the
  numbers live here rather than as call-site literals in the window.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Sequence

from us_quant.desktop_v2.pages.execution.models import ExecutionRuntimeView
from us_quant.desktop_v2.pages.execution.presenter import build_runtime_view

#: How many reconciled order rows the orders table reads.
RECONCILIATION_ROW_LIMIT = 50

#: How many audit rows the orders table is allowed to join against.
AUDIT_ROW_LIMIT = 1000

#: How many journal rows the latency table reads.
LATENCY_ROW_LIMIT = 100


def session_positions(
    broker_state: object | None, *, symbols: Iterable[str]
) -> tuple[object, ...]:
    """The broker's holdings that belong to this session's shortlist.

    Scoping is a decision, and it is made once, here, from the symbols the caller names.
    ``presenter.position_metric`` deliberately refuses to widen the scope from
    ``broker_state.positions`` -- the position card would then read "2" beside a one-row
    table whenever the account holds a symbol the current session never touched -- so the
    scope has to be settled *before* the projection, and this function is that settlement.

    Which symbols to scope by is the caller's fact, not this module's: the session's
    shortlist is route context, and inferring it from an armed session would be reading a
    business fact out of a presentation snapshot.
    """

    if broker_state is None:
        return ()
    scope = {str(symbol) for symbol in symbols}
    return tuple(
        row
        for row in (getattr(broker_state, "positions", ()) or ())
        if getattr(row, "symbol", None) in scope
    )


def pending_by_symbol(session: object) -> dict[str, object]:
    """The session's pending orders, keyed by the symbol their quotes arrive under.

    ``execution_symbol`` because the substitution the session trades under is the name
    the quote stream carries; the shadow table looks a candidate's row up by the same
    key, which is what makes "this candidate has a resting limit" answerable.
    """

    return {
        str(getattr(row, "execution_symbol", "")): row
        for row in (getattr(session, "pending_orders", ()) or ())
    }


def audit_by_intent(
    rows: Sequence[Mapping[str, object]], *, session_id: str
) -> dict[str, Mapping[str, object]]:
    """The journal's audit rows for this session, keyed by intent id.

    A session with no id has no audit rows of its own -- answering ``{}`` rather than
    matching every row whose ``session_id`` is absent, which is the one shape in which
    "no session" could silently become "somebody else's session".
    """

    if not session_id:
        return {}
    return {
        str(row["intent_id"]): row
        for row in rows
        if row.get("session_id") == session_id
    }


def build_session_view(
    *,
    session: object,
    account: object | None,
    broker_state: object | None,
    quotes: Mapping[str, object],
    candidates: Sequence[object],
    reconciliations: Sequence[object],
    audit_rows: Sequence[Mapping[str, object]],
    latency: Sequence[Mapping[str, object]],
    recently_ready: Callable[[str], bool],
) -> ExecutionRuntimeView:
    """Assemble the session read model and project it into the one renderable view.

    The single entry point, so "how does a Paper session become the execution route's
    view?" has one answer.  Everything it needs is either the session view or an
    already-fetched ambient fact, and everything it returns is immutable.
    """

    session_id = str(getattr(session, "session_id", "") or "")
    return build_runtime_view(
        snapshot=session,
        account=account,
        broker_state=broker_state,
        broker_positions=session_positions(
            broker_state, symbols=(row.symbol for row in candidates)
        ),
        quotes=quotes,
        pending_by_symbol=pending_by_symbol(session),
        latency=latency,
        reconciliations=reconciliations,
        audit_by_intent=audit_by_intent(audit_rows, session_id=session_id),
        candidates=candidates,
        recently_ready=recently_ready,
    )


__all__ = [
    "AUDIT_ROW_LIMIT",
    "LATENCY_ROW_LIMIT",
    "RECONCILIATION_ROW_LIMIT",
    "audit_by_intent",
    "build_session_view",
    "pending_by_symbol",
    "session_positions",
]
