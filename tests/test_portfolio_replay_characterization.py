"""Frozen Stage 5 outputs captured before extracting its accounting replay."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
from test_portfolio_reconciliation import (
    NOW, Side, OrderStatus, decision_record, linked_order, fill, reconcile,
)


def scenarios():
    buy = decision_record('buy', (('a', 'pa', 6), ('b', 'pb', 4)), order_id='z-buy')
    sell = decision_record('sell', (('a', 'sa', -6), ('b', 'sb', -4)), order_id='a-sell')
    bo, ba = linked_order(buy, fills=(
        fill('b2', 'z-buy', 7, price='11', fee='1', occurred_at=NOW-timedelta(hours=2)),
        fill('b1', 'z-buy', 3, fee='1', occurred_at=NOW-timedelta(hours=3)),
    ))
    so, sa = linked_order(sell, fills=(fill('s1', 'a-sell', 10, side=Side.SELL, price='12', fee='1'),))
    yield 'netting_multiple_partial_fills_sell_global_order', reconcile({}, records=(sell,buy), orders=(so,bo), attributions=(sa,ba))
    yield 'partial_cancel_missing_fee', reconcile({'AAPL':3}, records=(buy,), orders=(replace(bo, fills=(replace(bo.fills[1], fee=None),), events=(replace(bo.events[0], status=OrderStatus.CANCELED, filled=Decimal(3), remaining=Decimal(7)),)),), attributions=(ba,))
    yield 'duplicate_execution', reconcile({}, records=(buy,), orders=(replace(bo, fills=(bo.fills[0],bo.fills[0])),), attributions=(ba,))
    yield 'duplicate_cross_order', reconcile({}, records=(buy,sell), orders=(bo,replace(so,fills=(replace(so.fills[0],execution_id='b1'),))), attributions=(ba,sa))
    yield 'attribution_mismatch', reconcile({}, records=(buy,), orders=(replace(bo,intent=replace(bo.intent,quantity=11)),), attributions=(ba,))


def encoded(value):
    return json.loads(json.dumps(asdict(value), default=lambda x: str(x), sort_keys=True))


@pytest.mark.parametrize('name,result', list(scenarios()))
def test_stage5_frozen_outputs(name, result):
    golden = json.loads((Path(__file__).parent/'fixtures/portfolio_replay_stage5.json').read_text())
    assert encoded(result) == golden[name]
