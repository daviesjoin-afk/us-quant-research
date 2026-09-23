"""The v2O-C5B targeted session service: its boundary, not its algorithm.

The service is deliberately two boundaries and no rules -- the minute store and the
domain preflight evaluator -- so these tests assert the two properties that would
be lost if it grew: the preflight is *delegated* to rather than reimplemented, and
the broker route is hard-disabled with no parameter to open it.

The preflight algorithm itself is frozen and covered by
``tests/test_targeted_preflight.py``; this file must not duplicate those cases.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from us_quant.desktop_targeted_session_service import (
    DesktopTargetedSessionService,
)
from us_quant.minute_data import MinuteDataSummary, MinuteQuoteStore
from us_quant.targeted_preflight import (
    MAXIMUM_ACCOUNT_AGE_SECONDS,
    evaluate_target_preflight,
)
from us_quant.trading.domain.account import BrokerAccountSnapshot, Environment
from us_quant.trading.domain.market import MarketDataMode, MarketQuote
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.universe import UniverseRecord

_SERVICE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_targeted_session_service.py"
)

_NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)


def _record() -> UniverseRecord:
    return UniverseRecord(
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        security_type="STK",
        sector="信息技术",
        leader_tier=1,
        country_status="美国注册",
        country_evidence_level="verified_non_china",
        eligible_for_research=True,
        eligible_for_trading=True,
        exclusion_reason="",
    )


def _quote() -> MarketQuote:
    return MarketQuote(
        symbol="AAPL",
        bid=Decimal("199.90"),
        ask=Decimal("200"),
        last=Decimal("199.95"),
        close=None,
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=_NOW,
        age_seconds=1,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit test",
    )


def _account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias="DU***123",
        net_liquidation=Decimal("10000"),
        cash=Decimal("10000"),
        available_funds=Decimal("10000"),
        buying_power=Decimal("10000"),
        gross_position_value=Decimal("0"),
        excess_liquidity=Decimal("10000"),
        maintenance_margin=Decimal("0"),
        cushion=Decimal("1"),
        daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        observed_at=datetime.now(timezone.utc),
        pnl_source="IBKR reqPnL",
    )


def _strategy() -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id="intraday-targeted-t",
            name="指定标的日内 T",
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id="intraday-targeted-t",
            version_id="version-1",
            parameter_hash="p",
        ),
        semver="1.0.0-research",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters={
            "max_position_fraction": "0.10",
            "commission_per_order": "0.35",
            "slippage_bps": "2",
            "warmup_minutes": 10,
        },
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.10"),
        gate_passed=False,
        gate_reason="research",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _summary(usable: int, total: int) -> MinuteDataSummary:
    return MinuteDataSummary(
        symbol="AAPL",
        total_rows=total,
        usable_rows=usable,
        first_minute=None,
        last_minute=None,
        providers=("IBKR",),
        evidence_origins=("captured_stream",),
    )


class _Store:
    """A store that answers one scripted summary, counting its reads."""

    def __init__(self, summary: MinuteDataSummary | None = None) -> None:
        self._summary = summary or _summary(0, 0)
        self.reads: list[str] = []

    def summary(self, symbol: str) -> MinuteDataSummary:
        self.reads.append(symbol)
        return self._summary


def _service(store) -> DesktopTargetedSessionService:
    return DesktopTargetedSessionService(minute_quote_store=store)


class TargetedSessionServiceTests(unittest.TestCase):
    def test_the_minute_summary_delegates_to_the_store(self) -> None:
        store = _Store(_summary(12, 15))
        service = _service(store)

        result = service.minute_summary("aapl")

        self.assertEqual(result.usable_rows, 12)
        self.assertEqual(store.reads, ["aapl"])

    def test_the_preflight_is_delegated_to_the_domain_function(self) -> None:
        """Frozen: the service is a call site, not a second implementation.

        Asserted on the *whole* result against the domain function called the same
        way, so a local re-derivation -- a renamed gate, a re-ordered list, a
        "simplified" decision string -- fails here.  ``generated_at`` is the one
        field excluded, because two calls taken a millisecond apart differ in it by
        construction and nothing else may.
        """

        store = _Store(_summary(12, 15))
        service = _service(store)
        arguments = dict(
            universe_record=_record(),
            quote=_quote(),
            account=_account(),
            strategy=_strategy(),
            exposure_multiplier=Decimal("1"),
        )

        delegated = service.evaluate_preflight("AAPL", **arguments)
        direct = evaluate_target_preflight(
            "AAPL",
            minute_summary=store.summary("AAPL"),
            broker_orders_available=False,
            **arguments,
        )

        self.assertEqual(
            replace(delegated, generated_at=""),
            replace(direct, generated_at=""),
        )

    def test_the_broker_route_is_hard_disabled_and_not_a_parameter(self) -> None:
        """Research Targeted cannot acquire broker execution authority.

        Two assertions, because either alone is passable by accident: the literal
        the call passes, and the absence of a parameter a caller could flip.
        """

        store = _Store(_summary(12, 15))
        service = _service(store)
        result = service.evaluate_preflight(
            "AAPL",
            universe_record=_record(),
            quote=_quote(),
            account=_account(),
            strategy=_strategy(),
        )

        self.assertIs(result.broker_orders_available, False)
        self.assertFalse(result.orders_submitted)
        self.assertTrue(
            all(
                gate.passed
                for gate in result.gates
                if gate.code == "broker_order_route"
            )
        )
        self.assertNotIn(
            "broker_orders_available",
            inspect.signature(service.evaluate_preflight).parameters,
        )

    def test_the_capitals_stay_decimal_end_to_end(self) -> None:
        """The evaluator does whole-share math on these; a float would change it.

        ``net_liquidation`` and the multiplier are handed over untouched, so the
        sizing is the domain's own decimal arithmetic.
        """

        store = _Store(_summary(12, 15))
        service = _service(store)
        account = _account()

        result = service.evaluate_preflight(
            "AAPL",
            universe_record=_record(),
            quote=_quote(),
            account=account,
            strategy=_strategy(),
            exposure_multiplier=Decimal("2"),
        )

        self.assertIsInstance(result.account_net_liquidation, Decimal)
        self.assertIsInstance(result.position_budget, Decimal)
        # 10000 * 0.10 / 2 == 500, untouched by a float round trip.
        self.assertEqual(result.position_budget, Decimal("500"))

    def test_the_minute_evidence_comes_from_the_store_not_the_caller(self) -> None:
        """The evidence is read at evaluation time, so the caller cannot pass it."""

        store = _Store(_summary(12, 15))
        service = _service(store)

        result = service.evaluate_preflight(
            "AAPL",
            universe_record=_record(),
            quote=_quote(),
            account=_account(),
            strategy=_strategy(),
        )

        self.assertEqual(result.minute_usable_rows, 12)
        self.assertEqual(result.minute_total_rows, 15)
        self.assertNotIn(
            "minute_summary",
            inspect.signature(service.evaluate_preflight).parameters,
        )

    def test_the_service_delegates_the_whole_preflight_call(self) -> None:
        """The call site passes exactly the domain function's inputs, no extras.

        A ``now=`` here would let this layer decide what "fresh" means, and a
        missing argument would silently change which gates run.  Checked on the
        *call's* keywords rather than the public signature, because the mutation
        worth catching is an argument added at the delegation, not on the wrapper.
        """

        tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "evaluate_target_preflight"
        ]
        self.assertEqual(len(calls), 1, "one call site, delegated once")

        keywords = {kw.arg for kw in calls[0].keywords}
        self.assertEqual(
            keywords,
            {
                "universe_record",
                "quote",
                "account",
                "minute_summary",
                "strategy",
                "exposure_multiplier",
                "broker_orders_available",
            },
            keywords,
        )
        # And no positional argument beyond the symbol.
        self.assertEqual(len(calls[0].args), 1, "only the symbol is positional")

    def test_the_account_freshness_window_is_the_domains(self) -> None:
        """The 300-second rule is not restated or shadowed here.

        Checked against the executable code, not the prose: the docstring names the
        window to explain what is delegated, which is not a second rule.
        """

        self.assertEqual(MAXIMUM_ACCOUNT_AGE_SECONDS, Decimal("300"))
        tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                self.assertNotIn(
                    node.value, (300, 30), "the freshness window is not restated here"
                )

    def test_the_service_holds_no_rules_of_its_own(self) -> None:
        """Two boundaries and no policy: a rule here would be a second home."""

        tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {
                    item.name
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                self.assertEqual(
                    methods,
                    {"__init__", "minute_summary", "evaluate_preflight"},
                    methods,
                )


if __name__ == "__main__":
    unittest.main()
