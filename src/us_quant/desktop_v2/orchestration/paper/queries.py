"""Pure Paper launch rules: no Qt, no broker, no workflow mutation, no I/O.

``MainWindow`` used to run this launch as one handler pair that read facts off the
window, built a plan inline, decided whether a late callback was stale, re-ran the
preflight and then judged the broker reading.  Some of that is *rule*, not
sequencing: which plan a second start must be refused against, which broker reading
is acceptable, whether the operator's inputs still match the plan that was frozen,
and how a failed preflight is worded.  This module is those rules.

Everything here is a plain function over plain data.  There is no workflow, no
service, no widget and no callback: :func:`freeze_launch` receives already-read
facts and returns an immutable request, and :func:`validate_broker_state` receives
an already-read broker reading and returns a message or ``None``.  Deciding *when*
to read a fact, and what to do with the verdict, is the orchestrator's job.

Two rules are deliberate rather than accidental:

* **the launch fingerprint is not re-implemented here.**  ``us_quant.auto_launch``
  already owns ``AutoLaunchPlan``, its normalization and its match rule, and this
  module delegates to it rather than keeping a second copy of the same comparison.
  A second copy is how "the plan matched" starts meaning two different things;
* **"an attempt is in flight" is the workflow's phase, not a stored plan.**  The
  retired handler kept ``_active_auto_launch_plan`` and tested it for ``None``;
  that is exactly equivalent to ``phase is CONNECTING`` -- ``begin_connecting`` is
  the only transition into it and both exits clear the plan -- with the difference
  that the phase keeps being the truth *after* publication, when the plan
  legitimately outlives the attempt into ``RUNNING``.  Reading the phase therefore
  guards both the duplicate start and every later launch gate without a mirror.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from us_quant.auto_launch import (
    AutoLaunchPlan,
    build_auto_launch_plan,
    auto_launch_plan_matches,
)
from us_quant.desktop_v2.orchestration.paper.models import (
    CASH_MESSAGE,
    NET_LIQUIDATION_MESSAGE,
    POSITIONS_MESSAGE,
    PaperAccountReading,
    PaperControlFacts,
    PaperLaunchIntegrityError,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperStrategyLaunchFact,
)
from us_quant.trading.domain.strategy import parameter_hash_for
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from us_quant.paper_order_models import PaperBrokerState
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.preflight import AutoQuantPreflight


def launch_attempt_in_flight(phase: PaperWorkflowPhase) -> bool:
    """Whether a launch attempt currently owns the connect step.

    The canonical answer, read off the workflow rather than a mirrored plan: this
    is the one phase in which a broker connection is being established, which is
    the condition a second ``start()`` must refuse against.
    """

    return phase is PaperWorkflowPhase.CONNECTING


#: The phases in which a live session owns the run loop.  ``STOPPING`` is in the set
#: on purpose: an orderly stop still needs the market fact, because the exits, broker
#: events and the zero-state proof all read it while the session flattens.  Every other
#: phase -- the not-yet-connected ones, HALTED, the RECONCILING pair, FINALIZED -- gets
#: nothing, and a halted session must stay halted rather than being re-entered by the
#: next tick.
_ACTIVE_SESSION_PHASES = frozenset(
    {
        PaperWorkflowPhase.RUNNING,
        PaperWorkflowPhase.PAUSED,
        PaperWorkflowPhase.STOPPING,
    }
)


def active_session_phase(phase: PaperWorkflowPhase) -> bool:
    """Whether ``phase`` is one in which market ingress and the watchdog are legal.

    The one rule shared by :meth:`PaperOrchestrator.on_market_snapshot` and
    :meth:`PaperOrchestrator.poll`, so the two can never disagree about which
    sessions are live.
    """

    return phase in _ACTIVE_SESSION_PHASES


#: The phases whose *only* exit is an explicit operator recovery step.  v2O-E3 added this
#: set: the halt announcement and the shutdown disposition both need the answer, and two
#: copies of "which sessions are waiting on a human" is how one of them starts offering an
#: automatic route the other forbids.
_MANUAL_RECOVERY_PHASES = frozenset(
    {
        PaperWorkflowPhase.HALTED,
        PaperWorkflowPhase.RECONCILING,
        PaperWorkflowPhase.RECONCILING_READY,
    }
)


def manual_recovery_phase(phase: PaperWorkflowPhase) -> bool:
    """Whether ``phase`` can only be left by the operator.

    ``RUNNING``/``PAUSED`` are excluded deliberately: closing those still has an
    automatic route (``request_stop`` -> ``STOPPING`` -> the zero-state proof), so a
    caller that treated them as manual-recovery would hold the admission gate down for a
    drain that is still running.  ``STOPPING`` is excluded for the same reason.
    """

    return phase in _MANUAL_RECOVERY_PHASES


def reconciliation_resume_ready(
    phase: PaperWorkflowPhase, evidence: object | None
) -> bool:
    """Whether a fresh proof is waiting for the operator's confirmation.

    A **delegated** question, not a verdict: it reports that the workflow holds a one-shot
    proof produced by an explicit reconciliation, which is the condition under which
    asking the operator to confirm is meaningful.  Whether the proof is still *current*
    is not answered here -- only the workflow can decide that, and it revalidates the
    evidence it is handed when the confirmation actually arrives.
    """

    return phase is PaperWorkflowPhase.RECONCILING_READY and evidence is not None


def control_facts(
    phase: PaperWorkflowPhase, *, awaiting_confirmation: bool
) -> PaperControlFacts:
    """Translate the canonical phase into which session controls may be offered.

    The one place "which phase enables which button" is written down.  It used to live in
    ``MainWindow._publish_execution_controls``, which compared phase values itself: that
    is Paper phase reasoning on a presentation path, and v2O-E4 moved it into the
    capability that owns the phase.  The window still *reads* the canonical phase (a
    launch or a close may not be decided from anything else) and still hands the page
    booleans; it no longer interprets what the phase means.

    ``reconcile_available`` and ``resume_ready`` are deliberately separate facts rather
    than one "is the session halted" flag: starting a reconciliation is available while
    halted, and confirming one only once the halted session has produced a proof and is
    waiting for a human to look at it.
    """

    return PaperControlFacts(
        running=phase is PaperWorkflowPhase.RUNNING,
        paused=phase is PaperWorkflowPhase.PAUSED,
        reconcile_available=phase is PaperWorkflowPhase.HALTED,
        resume_ready=(
            phase is PaperWorkflowPhase.RECONCILING_READY and awaiting_confirmation
        ),
    )


def session_active(snapshot: object | None) -> bool:
    """Whether one engine snapshot positively reports a live session.

    A missing snapshot, or one with no active flag, answers ``False`` because this
    helper only reports positive runtime activity from the workflow's canonical
    result.

    Callers that need admission or launch safety must combine this fact with the
    canonical workflow phase, active order-service ownership and the shared
    execution lease.  Absence here is not, by itself, a fail-closed launch verdict.
    """

    return bool(getattr(snapshot, "active", False))


def runtime_obligations(snapshot: object | None) -> bool:
    """Whether one engine snapshot shows facts a market stop would strand.

    Three facts, and any one of them is enough: the session is still active, it holds
    positions, or it has orders in flight.  This is the *rendered* snapshot the workflow
    owns rather than a count mirrored anywhere, so the interlock that refuses a market
    stop reads the same truth the operator is looking at.

    Read with ``getattr`` because the value crosses the capability boundary as an
    engine snapshot; a missing attribute means "no such obligation", which is the
    fail-open direction for a *stop request* only -- the stop itself is still refused by
    the workflow once it sees the phase.
    """

    if snapshot is None:
        return False
    return bool(
        session_active(snapshot)
        or getattr(snapshot, "positions", ())
        or getattr(snapshot, "pending_orders", ())
    )


def preflight_failed(preflight: AutoQuantPreflight) -> bool:
    """Whether the first (or second) preflight refuses the launch."""

    return not preflight.ready


def preflight_failure_text(preflight: AutoQuantPreflight) -> str:
    """The bulleted list of failed checks, exactly as the operator saw it."""

    return "\n".join(
        f"\u2022 {row.name}：{row.detail}"
        for row in preflight.checks
        if not row.passed
    )


def freeze_launch(
    *,
    attempt_id: int,
    strategy: Any,
    candidates: Iterable[AutoQuantCandidate],
    requested_capital_limit: Decimal,
    order_channel: PaperOrderChannel,
) -> PaperLaunchRequest:
    """Freeze every input of one attempt into an immutable request.

    Read at the moment the last gate passed, so the attempt cannot be assembled
    from a strategy the operator changed after confirming, nor from an order
    channel whose settings changed while the broker was connecting.  The plan is
    built through ``us_quant.auto_launch`` so the fingerprint has one definition.

    **The strategy's parameters are deep-copied, and the copy is verified.**  Holding
    the live ``StrategyVersion`` would not freeze anything: it is a frozen dataclass,
    but its ``parameters`` is a plain mutable ``dict`` and ``parameter_hash`` is read
    off the governed identity rather than recomputed, so an in-place edit during the
    broker connect would leave the plan naming hash A while the session was built
    from parameters B.  :func:`strategy_launch_fact` takes the copy and refuses a
    version whose declared hash does not describe its own parameters.
    """

    rows = tuple(candidates)
    fact = strategy_launch_fact(strategy)
    plan = build_auto_launch_plan(
        attempt_id=attempt_id,
        strategy_version_id=fact.version_id,
        parameter_hash=fact.parameter_hash,
        candidate_symbols=(row.symbol for row in rows),
        requested_capital_limit=requested_capital_limit,
    )
    return PaperLaunchRequest(
        plan=plan,
        strategy=fact,
        candidates=rows,
        order_channel=order_channel,
    )


def strategy_launch_fact(strategy: Any) -> PaperStrategyLaunchFact:
    """Detach one strategy version from its live, mutable parameter mapping.

    Two checks, and both are deliberate rather than defensive.  The **deep copy**
    means a later ``strategy.parameters[...] = ...`` cannot reach the frozen
    request.  The **hash re-computation** means a version whose declared
    ``parameter_hash`` disagrees with its own parameters is refused outright: such a
    version cannot be launched under either hash honestly, and quietly using either
    one is the split-identity failure this whole shape exists to prevent.
    """

    parameters = copy.deepcopy(strategy.parameters)
    recomputed = parameter_hash_for(parameters)
    if recomputed != strategy.parameter_hash:
        raise PaperLaunchIntegrityError(
            "strategy version "
            f"{strategy.version_id!r} declares parameter_hash "
            f"{strategy.parameter_hash!r} but its parameters hash to "
            f"{recomputed!r}; refusing to launch an inconsistent version"
        )
    return PaperStrategyLaunchFact(
        version_id=strategy.version_id,
        parameter_hash=strategy.parameter_hash,
        identity=strategy.identity,
        parameters=parameters,
    )


def current_inputs_match(
    plan: AutoLaunchPlan,
    *,
    strategy: Any | None,
    candidates: Iterable[AutoQuantCandidate],
    requested_capital_limit: Decimal,
) -> bool:
    """Whether the operator's *current* inputs still match the frozen plan.

    Delegated to ``us_quant.auto_launch`` rather than re-compared, so "still
    matches" cannot drift from "was built".  A missing strategy is a mismatch for
    the same reason a changed one is: the plan names a version that must still be
    the selection this launch would arm.

    A version whose declared hash does not describe its own parameters is also a
    mismatch, **not** an exception.  This gate runs on the connect callback, where an
    exception would escape the slot and strand the attempt in ``CONNECTING`` holding
    the lease; answering "mismatch" routes it through the normal rejection, which
    disposes the candidate and releases PAPER.  Either way the launch does not
    proceed, which is the fail-closed outcome an inconsistent catalogue demands.
    """

    if strategy is None:
        return False
    try:
        fact = strategy_launch_fact(strategy)
    except PaperLaunchIntegrityError:
        return False
    return auto_launch_plan_matches(
        plan,
        strategy_version_id=fact.version_id,
        parameter_hash=fact.parameter_hash,
        candidate_symbols=(row.symbol for row in candidates),
        requested_capital_limit=requested_capital_limit,
    )


def validate_broker_state(state: PaperBrokerState) -> str | None:
    """The broker gate, as one refusal message or ``None`` when it passes.

    Four conditions, in the retired order, and every one is load-bearing:

    * net liquidation must be present and positive -- it sizes the session, so a
      missing reading is not a degraded input;
    * the account must be flat.  The first auto-rotation session requires it, so an
      existing position is a refusal rather than something to work around;
    * cash must be present, because the session is bounded by *cash*.  Substituting
      buying power would silently introduce margin borrowing;
    * the capital resolution itself is left to the composition root, which owns
      the ``Decimal`` chain.

    Decimal throughout, never float: a float conversion here would be a rounding
    rule nobody chose.
    """

    if state.net_liquidation is None or state.net_liquidation <= 0:
        return NET_LIQUIDATION_MESSAGE
    if state.positions:
        symbols = ", ".join(row.symbol for row in state.positions)
        return POSITIONS_MESSAGE.format(symbols=symbols)
    if state.cash is None:
        return CASH_MESSAGE
    return None


def account_reading(state: PaperBrokerState, *, account_alias: str) -> PaperAccountReading:
    """Project a validated broker reading into the value the build seam consumes.

    Called only after :func:`validate_broker_state` returned ``None``, so the three
    fields are known to be present -- the assertions document that contract rather
    than re-checking it, because a second check here would be a second gate whose
    failure mode is a different message for the same condition.
    """

    assert state.net_liquidation is not None and state.cash is not None
    return PaperAccountReading(
        net_liquidation=state.net_liquidation,
        cash=state.cash,
        account_alias=account_alias,
    )


__all__ = [
    "account_reading",
    "active_session_phase",
    "control_facts",
    "current_inputs_match",
    "freeze_launch",
    "launch_attempt_in_flight",
    "manual_recovery_phase",
    "preflight_failed",
    "preflight_failure_text",
    "reconciliation_resume_ready",
    "runtime_obligations",
    "session_active",
    "strategy_launch_fact",
    "validate_broker_state",
]
