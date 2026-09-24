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
    "current_inputs_match",
    "freeze_launch",
    "launch_attempt_in_flight",
    "preflight_failed",
    "preflight_failure_text",
    "strategy_launch_fact",
    "validate_broker_state",
]
