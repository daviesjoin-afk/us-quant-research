"""Shared IBKR primitives used by more than one IBKR channel.

This module exists because three *different* IBKR channels need the same few
things, and duplicating them would be a safety regression rather than a
layering win:

* :func:`mask_account_id` -- the single definition of how a raw IBKR account
  id becomes the masked alias (``DU1234567`` -> ``DU***67``).  The account
  adapter masks before it publishes to the domain, and the Paper order
  service masks the alias it records.  Two copies of this function could
  drift, and the one that drifted would be the one that leaked an account
  number into a store or an export.
* :data:`INFORMATIONAL_ERROR_CODES` -- the IBKR error codes that are
  notices, not failures.  The account read, the history read and the Paper
  order path all have to classify them the same way.
* :func:`ensure_readonly_paper_config` -- the read-only Paper safety gate
  (port 4002, API read-only, no order submission).  Every read-only IBKR
  channel must apply it identically; a channel that skipped it would be the
  one that connected to a live port.
* :func:`optional_decimal` -- the conversion that turns IBKR's "unset"
  sentinel into ``None`` instead of a number.

It is *not* a general IBKR grab-bag and it is deliberately tiny.  It depends
only on the standard library and :mod:`us_quant.ibkr`, so every IBKR channel
can import it without any of them reaching into another.

The errors here are vendor-shaped on purpose.  The account adapter translates
them into the provider-neutral errors in
:mod:`us_quant.trading.ports.broker_account` at its boundary, so no upper
layer ever sees an IBKR name.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from us_quant.ibkr import IBKRConnectionConfig


#: IBKR error codes that report connectivity/notice conditions rather than a
#: failed request.  Treating one as fatal would abort a read that actually
#: succeeded.
INFORMATIONAL_ERROR_CODES = {
    2104,
    2106,
    2107,
    2108,
    2119,
    2158,
}

#: IBKR's sentinel for "no value" on a double field.  Any magnitude at or
#: above this means unset, so it must become ``None`` rather than a number
#: the operator would read as real.
IBKR_UNSET_DOUBLE = float("1.7976931348623157e308")

#: The account-summary tags the account read requests.
ACCOUNT_SUMMARY_TAGS = (
    "NetLiquidation,TotalCashValue,BuyingPower,AvailableFunds,"
    "GrossPositionValue,ExcessLiquidity,MaintMarginReq,Cushion,"
    "Currency"
)


class IBKRAPIUnavailable(RuntimeError):
    """The official IBKR Python API is not installed."""


class IBKRReadOnlyError(RuntimeError):
    """A read-only IBKR channel refused or failed.

    Covers both the config gate (not Paper port 4002, not read-only,
    submission enabled) and runtime read failures (socket failure, timeout).
    Adapters translate it at their boundary.
    """


def mask_account_id(account: str) -> str:
    """Reduce an IBKR account id to a non-reversible display alias.

    Short ids are masked entirely so no prefix/suffix of a short identifier
    survives.  This is the only account-id transformation in the tree.
    """

    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}***{account[-2:]}"


def ensure_readonly_paper_config(config: IBKRConnectionConfig) -> None:
    """Refuse any read-only IBKR connection that is not Paper read-only.

    All three conditions are required together: the Paper gateway port, the
    API-level read-only flag, and no order submission.  Each one alone is
    insufficient -- read-only on the live port still reads a live account,
    and the Paper port with submission enabled can trade.
    """

    if config.port != 4002:
        raise IBKRReadOnlyError(
            "read-only integration is locked to IB Gateway Paper port 4002"
        )
    if not config.api_read_only:
        raise IBKRReadOnlyError(
            "IBKR API read-only mode must be enabled"
        )
    if config.paper_order_submission_enabled:
        raise IBKRReadOnlyError(
            "paper order submission must remain disabled"
        )


def optional_decimal(value: Any) -> Decimal | None:
    """Convert an IBKR numeric field, treating its unset sentinel as absent.

    Returns ``None`` rather than ``0`` for an unset or unparseable value:
    "the broker did not report this" and "the broker reported zero" are
    different facts, and collapsing them would fabricate a number.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) >= IBKR_UNSET_DOUBLE:
        return None
    return Decimal(str(value))


__all__ = [
    "ACCOUNT_SUMMARY_TAGS",
    "IBKR_UNSET_DOUBLE",
    "IBKRAPIUnavailable",
    "IBKRReadOnlyError",
    "INFORMATIONAL_ERROR_CODES",
    "ensure_readonly_paper_config",
    "mask_account_id",
    "optional_decimal",
]
