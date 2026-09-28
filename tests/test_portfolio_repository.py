from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioSide,
    PortfolioVerdict,
)
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioStoreUnreadable,
)


OBSERVED = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def _record(*, zero_net: bool = False) -> PortfolioDecisionRecord:
    signed = (10, -10) if zero_net else (10, -6)
    decision_id = "decision-a"
    attribution = tuple(
        PortfolioOrderAttribution(
            portfolio_decision_id=decision_id,
            strategy_version_id=strategy,
            proposal_id=proposal,
            symbol=" aapl ",
            signed_requested_quantity=quantity,
        )
        for strategy, proposal, quantity in zip(
            ("strategy-a", "strategy-b"), ("proposal-a", "proposal-b"), signed
        )
    )
    net = sum(signed)
    decision = PortfolioDecision(
        decision_id=decision_id,
        decision=PortfolioVerdict.APPROVE,
        symbol="AAPL",
        strategy_version_ids=("strategy-a", "strategy-b"),
        requested_quantity=sum(abs(value) for value in signed),
        net_quantity=net,
        blocker=None,
        attribution=attribution,
        action=(
            PortfolioAction(
                symbol="AAPL",
                side=PortfolioSide.BUY if net > 0 else PortfolioSide.SELL,
                quantity=abs(net),
                reference_price=Decimal("10"),
            )
            if net
            else None
        ),
    )
    return PortfolioDecisionRecord(
        decision=decision,
        portfolio_cycle_id="cycle-1",
        observed_at=OBSERVED,
        policy_identity="paper-default",
        policy_revision="7",
        created_at=OBSERVED,
    )


def test_portfolio_decision_round_trip_survives_repository_restart(tmp_path):
    path = tmp_path / "portfolio.sqlite"
    first = SQLitePortfolioRepository(path)
    saved = first.record_decision(_record())

    restarted = SQLitePortfolioRepository(path)
    loaded = restarted.decision("decision-a")

    assert loaded == saved
    assert loaded is not None
    assert loaded.decision.symbol == "AAPL"
    assert [item.signed_requested_quantity for item in loaded.decision.attribution] == [10, -6]
    assert loaded.decision.action is not None and loaded.decision.action.quantity == 4


def test_zero_net_decision_persists_without_action(tmp_path):
    repository = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")

    saved = repository.record_decision(_record(zero_net=True))

    assert saved.decision.net_quantity == 0
    assert saved.decision.action is None
    assert repository.decision("decision-a") == saved


def test_same_decision_payload_is_idempotent_and_conflict_is_refused(tmp_path):
    repository = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")
    saved = repository.record_decision(_record())

    assert repository.record_decision(_record()) == saved
    with pytest.raises(ValueError, match="conflicting"):
        repository.record_decision(replace(_record(), portfolio_cycle_id="cycle-2"))


def test_decision_update_uses_compare_and_swap_revision(tmp_path):
    repository = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")
    saved = repository.record_decision(_record())
    approved = replace(saved, risk_outcome="approved")
    updated = repository.update_decision(approved, expected_revision=saved.revision)

    assert updated.revision == saved.revision + 1
    assert repository.decision("decision-a") == updated
    with pytest.raises(ValueError, match="stale"):
        repository.update_decision(replace(saved, risk_outcome="rejected"), expected_revision=saved.revision)


def test_corrupt_json_row_is_unreadable_not_an_empty_ledger(tmp_path):
    path = tmp_path / "portfolio.sqlite"
    repository = SQLitePortfolioRepository(path)
    repository.record_decision(_record())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE portfolio_decision SET payload = ? WHERE decision_id = ?",
            ("{broken", "decision-a"),
        )

    with pytest.raises(PortfolioStoreUnreadable):
        repository.decisions()


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda row: row.update(attribution=[]),
        lambda row: row.update(net_quantity=3),
        lambda row: row["action"].update(quantity=2),
    ],
    ids=("missing-attribution", "net-mismatch", "action-mismatch"),
)
def test_invariant_violations_in_stored_rows_fail_closed(tmp_path, corrupt):
    path = tmp_path / "portfolio.sqlite"
    repository = SQLitePortfolioRepository(path)
    repository.record_decision(_record())
    with sqlite3.connect(path) as connection:
        (payload,) = connection.execute(
            "SELECT payload FROM portfolio_decision WHERE decision_id = ?", ("decision-a",)
        ).fetchone()
        value = json.loads(payload)
        corrupt(value)
        connection.execute(
            "UPDATE portfolio_decision SET payload = ? WHERE decision_id = ?",
            (json.dumps(value), "decision-a"),
        )

    with pytest.raises(PortfolioStoreUnreadable):
        repository.decision("decision-a")


def test_portfolio_persistence_contains_no_broker_credentials_or_live_account_id(tmp_path):
    path = tmp_path / "portfolio.sqlite"
    repository = SQLitePortfolioRepository(path)
    repository.record_decision(_record())
    raw = path.read_bytes()

    assert b"broker_password" not in raw
    assert b"live_account_id" not in raw
    assert b"DU1234567" not in raw
