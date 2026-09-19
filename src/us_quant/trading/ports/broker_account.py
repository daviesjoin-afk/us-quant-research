"""Broker account port: read-only account and position access.

This is the interface the IBKR account adapter satisfies and the only way
the application layer reaches a broker for account data.  It is strictly
read-only -- there is no submit, cancel or arm here.  Order submission
belongs to ``BrokerExecutionPort``, which has not migrated yet, so a
read-only account link can never be mistaken for a channel that can trade.

The surface matches what the implementation actually does.  Reading an IBKR
account is a one-shot *connect / read / disconnect* cycle, so the port is a
single :meth:`BrokerAccountPort.refresh` rather than a pretend persistent
session.  A durable account socket, a reconnect daemon or an account event
bus would be a different design with different failure modes; inventing the
interface for one before it exists would make the port a lie.

Errors are provider-neutral on purpose.  Callers must not catch
``IBKRReadOnlyError`` or ``IBKRAPIUnavailable`` -- those are vendor details
that belong inside the adapter, which translates them into the errors below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from us_quant.trading.domain.account import BrokerAccountPortfolio


class BrokerAccountError(RuntimeError):
    """Base class for every provider-neutral account failure."""


class BrokerAccountUnavailable(BrokerAccountError):
    """The account could not be reached at all.

    Raised when the vendor API is missing, the socket cannot be established,
    or the protocol handshake times out.  The account is not known to be
    wrong -- it simply could not be read.
    """


class BrokerAccountValidationError(BrokerAccountError):
    """The account data was read but is not acceptable as account truth.

    Raised for a rejected configuration (not Paper port 4002, not read-only,
    submission enabled), a non-``DU`` account in a Paper environment, and a
    managed-account count other than exactly one.  Nothing is published to
    the domain in these cases: the adapter raises instead of guessing.
    """


class BrokerAccountActiveError(BrokerAccountError):
    """A refresh is already running, so a second one is refused.

    Fail closed rather than opening a second client-id socket: two
    concurrent read-only sessions would race to publish account truth and
    could exhaust the gateway's client-id budget.
    """


#: Builds one adapter bound to a specific connection config.  The
#: application holds this instead of importing a concrete adapter, which is
#: what keeps ``application/accounts.py`` free of any ``trading.adapters``
#: import.  Composition supplies the real factory.
BrokerAccountAdapterFactory = Callable[[], "BrokerAccountPort"]


class BrokerAccountPort(Protocol):
    """Read-only account data entry point.

    ``refresh`` performs one complete read and returns the domain portfolio.
    It is the caller's job to decide whether a failed refresh should discard
    a previously good snapshot; the port itself holds no state.
    """

    def refresh(
        self,
        *,
        timeout_seconds: float,
    ) -> BrokerAccountPortfolio: ...


__all__ = [
    "BrokerAccountActiveError",
    "BrokerAccountAdapterFactory",
    "BrokerAccountError",
    "BrokerAccountPort",
    "BrokerAccountUnavailable",
    "BrokerAccountValidationError",
]
