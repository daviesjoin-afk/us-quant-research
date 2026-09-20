"""Signal detection: the minute histories and the gates that read them.

The entry side of the strategy is two separable jobs -- *is there a signal*, and
*what would we do about it* -- and this is the first one.  It accumulates one
mark per candidate per minute, keeps the market references that gate the whole
scan, and answers two questions: which symbols pass every signal gate, strongest
first, and is the regime open at all.

It holds no positions, no policy and no proposal: ranking a symbol says nothing
about whether it may be bought or in what size.  That separation is what lets
the gates be tested without a session, a book or a risk verdict anywhere near
them.

The gap rule in ``_append`` is the subtle part.  A symbol that stopped ticking
and resumed later must not have its history read as a momentum series, so a gap
longer than a minute clears the series and starts again.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.market import MarketQuote
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.models import AutoQuantCandidate


class SignalScanner:
    def __init__(
        self,
        *,
        config: TradingSessionConfig,
        candidates: tuple[AutoQuantCandidate, ...],
        market_reference_symbols: tuple[str, ...],
    ) -> None:
        self.config = config
        self.candidates = candidates
        references = tuple(
            dict.fromkeys(
                symbol.strip().upper()
                for symbol in market_reference_symbols
                if symbol.strip()
            )
        )
        self._histories: dict[str, deque[tuple[datetime, Decimal]]] = {
            row.symbol: deque(
                maxlen=max(60, config.warmup_minutes + 5)
            )
            for row in candidates
        }
        if any(symbol in self._histories for symbol in references):
            raise ValueError(
                "market reference symbols must not be trade candidates"
            )
        self.market_reference_symbols = references
        self._reference_histories: dict[
            str, deque[tuple[datetime, Decimal]]
        ] = {symbol: deque(maxlen=3) for symbol in references}
        self._scores = {
            row.symbol: row.scan_score for row in candidates
        }

    def clear(self) -> None:
        """Forget every history: a new session or a new day starts cold."""

        for history in self._histories.values():
            history.clear()
        for history in self._reference_histories.values():
            history.clear()

    def observe(
        self,
        *,
        now: datetime,
        ready: dict[str, MarketQuote],
        reference_ready: dict[str, MarketQuote],
    ) -> None:
        """Fold this tick's marks into the per-symbol minute histories."""

        for symbol, quote in ready.items():
            assert quote.bid is not None and quote.ask is not None
            self._append(
                self._histories[symbol],
                now,
                (quote.bid + quote.ask) / Decimal("2"),
            )
        for symbol, quote in reference_ready.items():
            assert quote.bid is not None and quote.ask is not None
            self._append(
                self._reference_histories[symbol],
                now,
                (quote.bid + quote.ask) / Decimal("2"),
            )

    @staticmethod
    def _append(
        history: deque[tuple[datetime, Decimal]],
        now: datetime,
        price: Decimal,
    ) -> None:
        minute = now.replace(second=0, microsecond=0)
        if history and history[-1][0] == minute:
            history[-1] = (minute, price)
            return
        if (
            history
            and (minute - history[-1][0]).total_seconds() > 60
        ):
            history.clear()
        history.append((minute, price))

    def required_history(self) -> int:
        return max(
            self.config.warmup_minutes,
            self.config.momentum_lookback_minutes + 1,
        )

    def warmed_count(self) -> int:
        required = self.required_history()
        return sum(
            len(history) >= required
            for history in self._histories.values()
        )

    def ranked(
        self, ready: dict[str, MarketQuote]
    ) -> list[tuple[Decimal, str, MarketQuote, int, int]]:
        """The symbols passing every signal gate, strongest signal first.

        The gates, in order: enough history, a spread inside the ceiling, a
        momentum inside its band, no single minute moving further than the
        spike limit, at least the configured number of positive steps, and a
        price above the session's own average.

        There is no ``allowed`` filter: whether a symbol may be bought is a
        risk verdict, and filtering it out of the scan would also mean a
        blocked candidate could never *report* why it was blocked.
        """

        required = self.required_history()
        ranked: list[
            tuple[Decimal, Decimal, str, MarketQuote, int, int]
        ] = []
        for symbol, quote in ready.items():
            history = self._histories[symbol]
            if len(history) < required:
                continue
            assert quote.bid is not None and quote.ask is not None
            mid = (quote.bid + quote.ask) / Decimal("2")
            if (
                quote.ask - quote.bid
            ) / mid > self.config.maximum_spread_fraction:
                continue
            lookback = history[
                -(self.config.momentum_lookback_minutes + 1)
            ][1]
            momentum = history[-1][1] / lookback - Decimal("1")
            recent = tuple(history)[
                -(self.config.momentum_lookback_minutes + 1):
            ]
            step_returns = tuple(
                recent[index][1] / recent[index - 1][1]
                - Decimal("1")
                for index in range(1, len(recent))
            )
            if any(
                abs(value) > self.config.maximum_one_minute_move
                for value in step_returns
            ):
                continue
            positive_steps = sum(
                value > 0 for value in step_returns
            )
            if (
                positive_steps
                < self.config.minimum_positive_steps
            ):
                continue
            average = sum(
                (row[1] for row in history), Decimal("0")
            ) / len(history)
            if (
                self.config.minimum_momentum
                <= momentum
                <= self.config.maximum_momentum
                and history[-1][1] > average
            ):
                ranked.append(
                    (
                        momentum,
                        self._scores[symbol],
                        symbol,
                        quote,
                        positive_steps,
                        len(step_returns),
                    )
                )
        return [
            (momentum, symbol, quote, positive_steps, step_count)
            for momentum, _score, symbol, quote, positive_steps, step_count
            in sorted(ranked, reverse=True)
        ]

    def regime_block(
        self, reference_ready: dict[str, MarketQuote]
    ) -> str | None:
        """The entry regime gate, which fails closed.

        Exits are evaluated before this gate is consulted, so a closed regime
        can never trap a position: it stops new entries only.
        """

        for symbol in self.market_reference_symbols:
            if symbol not in reference_ready:
                return f"{symbol} is not fresh"
            history = self._reference_histories[symbol]
            if len(history) < 2:
                return f"{symbol} needs two consecutive minute marks"
            one_minute_return = (
                history[-1][1] / history[-2][1] - Decimal("1")
            )
            if one_minute_return < 0:
                return f"{symbol} one-minute return is negative"
        return None