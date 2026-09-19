"""Account and position domain.

Three concepts live here, and they are deliberately distinct:

``RiskAccountSnapshot``
    The *risk calculation* inputs ``PreTradeRiskEngine`` consumes:
    net liquidation, cash, the day's starting equity and the high-water
    mark.  It was called ``AccountSnapshot`` until Broker/Account v2; that
    generic name invited the mistake this module now makes impossible --
    reading a risk calculation's state as if it were broker truth.

``BrokerAccountSnapshot`` / ``BrokerPositionSnapshot`` / ``BrokerAccountPortfolio``
    What the broker actually reports: balances, buying power, margin, and
    the real position quantities.  These are *observations*, not opinions.
    A read-only account link can never place, cancel or exercise anything,
    so nothing here is a trading instruction.

``BrokerConnectionState`` describes the broker link in transport terms only:
whether the socket is up, whether the account is readable, and whether an
execution channel is armed.

Two invariants are load-bearing and enforced by construction:

* **No raw account identifier.**  ``BrokerAccountSnapshot.account_alias`` is
  the masked form (``DU***67``) and there is no field for the unmasked
  account number.  The raw id exists only inside the IBKR adapter, which
  needs it for ``reqPnL``/``reqPnLSingle`` and masks before converting.  A
  domain type cannot leak what it cannot hold.
* **No fabricated quantity.**  ``BrokerPositionSnapshot.quantity`` is a
  ``Decimal`` and is never rounded to whole shares.  Whole-share trading is
  an *execution* policy; observation must stay honest, so a fractional
  position the broker reports is reported as it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from us_quant.trading.domain.common import ONE, ZERO, Environment


def _require_aware(value: datetime, label: str) -> None:
    """Raise unless ``value`` carries a real UTC offset.

    ``tzinfo is not None`` alone is not enough: a ``tzinfo`` whose
    ``utcoffset()`` returns ``None`` is still effectively naive, and
    subtracting it from an aware value raises deep inside the caller.  Both
    checks are needed for the contract to mean what it says.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{label} observed_at must be timezone-aware"
        )


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: int
    average_price: Decimal
    exposure_multiplier: Decimal = ONE

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError("MVP positions cannot be short")
        if self.average_price < ZERO:
            raise ValueError("average price cannot be negative")
        if self.exposure_multiplier <= ZERO:
            raise ValueError("exposure multiplier must be positive")

    def market_value(self, price: Decimal) -> Decimal:
        return price * self.quantity

    def risk_exposure(self, price: Decimal) -> Decimal:
        return self.market_value(price) * self.exposure_multiplier


@dataclass(frozen=True, slots=True)
class RiskAccountSnapshot:
    """The account state the pre-trade risk engine evaluates against.

    Renamed from ``AccountSnapshot`` in Broker/Account v2 with fields and
    validation unchanged.  It is *not* broker truth: it carries the
    day-start equity and high-water mark a risk calculation needs, which no
    broker reports.
    """

    net_liquidation: Decimal
    cash: Decimal
    day_start_equity: Decimal
    high_watermark: Decimal
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if min(
            self.net_liquidation,
            self.cash,
            self.day_start_equity,
            self.high_watermark,
        ) < ZERO:
            raise ValueError("account values cannot be negative")
        # The default is already aware; a caller-supplied one must be too.
        # The daily-loss and drawdown halts compare a snapshot against a
        # day's starting equity, so a timestamp with no offset would put "how
        # old is this reading" at the mercy of the machine's locale -- and a
        # risk decision is exactly the wrong place to guess.
        _require_aware(self.timestamp, "risk account")


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    """Broker-reported account balances.

    ``account_alias`` is the only account identifier that may exist here --
    already masked by the adapter.  There is deliberately no ``account_id``,
    ``account_number``, ``raw_account`` or ``managed_account`` field, so the
    domain cannot be handed ``DU1234567`` even by mistake.

    Every money field is optional: a broker that did not report a metric
    yields ``None``, never ``0``.  Substituting zero would turn "unknown"
    into a confident, and possibly fatal, number.

    ``pnl_source`` names where the P&L figures came from so a local estimate
    can never be mistaken for broker-reported P&L.
    """

    environment: Environment
    account_alias: str

    net_liquidation: Decimal | None
    cash: Decimal | None
    available_funds: Decimal | None
    buying_power: Decimal | None

    gross_position_value: Decimal | None
    excess_liquidity: Decimal | None
    maintenance_margin: Decimal | None
    cushion: Decimal | None

    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None

    observed_at: datetime
    pnl_source: str

    def __post_init__(self) -> None:
        """Refuse a timestamp with no timezone.

        An aware ``datetime`` is the contract, and a naive one is not
        quietly promoted to UTC: guessing a timezone is how a stale account
        would come to look fresh, and the preflight's 300-second freshness
        gate reads this field.  Failing here means the mistake surfaces at
        construction, next to the code that made it, rather than as a
        silently wrong age at the point of use.
        """

        _require_aware(self.observed_at, "broker account")


@dataclass(frozen=True, slots=True)
class BrokerPositionSnapshot:
    """One broker-reported position.

    ``quantity`` keeps full ``Decimal`` precision.  The system's whole-share
    rule is an execution policy applied later; an account reader that
    rounded here would be fabricating broker truth.

    There are deliberately no risk fields (``risk_multiplier``,
    ``risk_exposure``, ``max_position_fraction``): those are Risk/Application
    projections over broker facts, not broker facts.  The UI computes
    exposure for display and never writes it back.
    """

    account_alias: str

    con_id: int
    symbol: str
    local_symbol: str

    security_type: str
    exchange: str
    currency: str

    quantity: Decimal
    average_cost: Decimal

    market_value: Decimal | None

    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None

    observed_at: datetime

    def __post_init__(self) -> None:
        """Refuse a timestamp with no timezone.  See the account type."""

        _require_aware(self.observed_at, "broker position")

    @property
    def cost_basis(self) -> Decimal:
        """What the position cost, from the broker's own average cost."""

        return self.average_cost * self.quantity

    @property
    def mark(self) -> Decimal | None:
        """The broker's per-share mark, derived from its market value.

        This is ``market_value / quantity`` -- the broker's own valuation,
        not a quote from a market-data feed.  Deriving it here is what lets
        the account path stay free of market data.  ``None`` when either
        input is missing or the position is flat, because a mark is
        undefined without a position to divide by.
        """

        if self.market_value is None or self.quantity == ZERO:
            return None
        return self.market_value / self.quantity

    @property
    def local_unrealized_pnl(self) -> Decimal | None:
        """Broker market value minus cost basis, when a value exists.

        A pure derivation over broker fields -- no market-data adapter is
        involved.  Kept separate from the broker-reported
        ``unrealized_pnl`` so the two can never be confused.
        """

        if self.market_value is None:
            return None
        return self.market_value - self.cost_basis


@dataclass(frozen=True, slots=True)
class BrokerDiagnostic:
    """A provider message worth showing the operator.

    Carries no credential and no raw account id: it is a code and a
    message, which is all the UI or CLI needs to explain itself.
    """

    code: int
    message: str
    informational: bool


@dataclass(frozen=True, slots=True)
class BrokerAccountPortfolio:
    """The main account snapshot the desktop and preflight consume.

    ``diagnostics`` is optional and defaults to empty, so the common case
    carries nothing extra while a provider that produced warnings can still
    surface them without leaking an adapter-specific DTO upward.
    """

    account: BrokerAccountSnapshot
    positions: tuple[BrokerPositionSnapshot, ...]
    diagnostics: tuple[BrokerDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class BrokerConnectionState:
    connected: bool
    account_ready: bool
    execution_ready: bool
    message: str
