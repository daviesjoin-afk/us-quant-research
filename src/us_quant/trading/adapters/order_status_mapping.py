"""The one mapping from stored channel status text to ``OrderStatus``.

The order store keeps the status text its channel reported, because that text is
what reconciliation compares and what an operator reads in the audit view, and
because databases written by earlier builds must keep opening unchanged.  The
domain, however, must not have upper layers comparing raw strings -- a policy
that spells ``status.casefold() == "apicancelled"`` is one channel's vocabulary
leaking into a decision.

So the translation happens once, here, and both adapters that touch status text
use it: the IBKR execution adapter when it turns an order-status callback into an
``OrderEvent``, and the SQLite order repository when it reads a stored row back
into an ``OrderStatus``.

Unrecognised text maps to ``OrderStatus.UNKNOWN`` and never to a terminal
status.  Guessing "FILLED" from a status this system has never seen would make an
order disappear from the pending book on the strength of a spelling.
"""

from __future__ import annotations

from us_quant.trading.domain.orders import OrderStatus

#: The text forms each domain status is reached from.  Both the domain spelling
#: and the channel spellings are listed so a value this tree writes itself reads
#: back identically to one a broker reported.
_STATUS_TEXT: dict[str, OrderStatus] = {
    "created": OrderStatus.CREATED,
    "risk_approved": OrderStatus.RISK_APPROVED,
    "risk_rejected": OrderStatus.RISK_REJECTED,
    "submitting": OrderStatus.SUBMITTING,
    "pending": OrderStatus.SUBMITTING,
    "pendingsubmit": OrderStatus.SUBMITTING,
    "presubmitted": OrderStatus.ACKNOWLEDGED,
    "submitted": OrderStatus.ACKNOWLEDGED,
    "acknowledged": OrderStatus.ACKNOWLEDGED,
    "partiallyfilled": OrderStatus.PARTIALLY_FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "pendingcancel": OrderStatus.CANCEL_PENDING,
    "cancel_pending": OrderStatus.CANCEL_PENDING,
    "cancelpending": OrderStatus.CANCEL_PENDING,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELED,
    "canceled": OrderStatus.CANCELED,
    "apicancelled": OrderStatus.CANCELED,
    "inactive": OrderStatus.INACTIVE,
    "error": OrderStatus.BROKER_REJECTED,
    "rejected": OrderStatus.BROKER_REJECTED,
    "broker_rejected": OrderStatus.BROKER_REJECTED,
    "unknown": OrderStatus.UNKNOWN,
    # A submission whose outcome could not be established locally.  It is not a
    # terminal status: the order may well be live at the broker, which is
    # exactly why it halts for reconciliation instead of resolving itself.
    "submituncertain": OrderStatus.UNKNOWN,
}


def order_status_from_text(text: str) -> OrderStatus:
    """Map one stored or reported status text to the domain status."""

    return _STATUS_TEXT.get(str(text).strip().casefold(), OrderStatus.UNKNOWN)


def status_text_for(status: OrderStatus) -> str:
    """The text to store when only a domain status is known."""

    return status.value