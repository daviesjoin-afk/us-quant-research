from dataclasses import replace, fields, MISSING
from datetime import timedelta
from decimal import Decimal
import pytest

from paper_performance_support import NOW, history, project, policy, verdict
from us_quant.trading.domain.strategy_paper_performance import (
    StrategyPaperPerformanceBlocker as B, StrategyPaperPerformanceVerdict as V,
    StrategyPaperPerformancePolicy, evaluate_strategy_paper_performance_policy,
)
from us_quant.trading.domain.portfolio_reconciliation import PortfolioReconciliationBlocker as RB


def test_multi_strategy_risk_scaled_partial_fills_use_stage5_accounting():
    m, replay, rec = project()
    assert rec.blockers == ()
    assert [(f.execution_id, f.signed_quantity) for f in replay.attributed_fills if f.strategy_version_id == 'a'] == [('b1',18),('b2',30),('s1',-48)]
    assert [(f.execution_id, f.signed_quantity) for f in replay.attributed_fills if f.strategy_version_id == 'b'] == [('b1',12),('b2',20),('s1',-32)]
    for strategy in ('a','b'):
        metrics, _, _ = project(strategy=strategy)
        current = next(x for x in rec.strategy_accounting if x.strategy_version_id == strategy)
        assert metrics.realized_pnl == current.realized_pnl
        assert metrics.fees == current.fees
        assert metrics.total_slippage == current.slippage
        assert metrics.completed_round_trips == 1
    assert m.realized_pnl == Decimal(480)
    assert m.fees == Decimal('1.8')
    assert m.net_realized_pnl == Decimal('478.2')
    assert m.gross_traded_notional == Decimal(10080)
    assert m.adverse_slippage == Decimal(4320)
    assert verdict(m,rec,maximum_adverse_slippage=Decimal(5000))[0] is V.PASS


def test_pre_window_basis_notional_and_round_trip():
    m, _, _ = project(data=history(pre_window=True))
    assert m.realized_pnl == Decimal(480)
    assert m.completed_round_trips == 1
    assert m.gross_traded_notional == Decimal(5280)
    assert m.fees == Decimal('0.6')
    assert m.paper_session_ids == ('sell-session',)
    assert m.evidence_duration == timedelta(0)
    assert m.average_cost_basis_exposure == Decimal(3200)


def test_time_weighted_cost_basis_exposure():
    m, _, _ = project()
    # Zero for day 1, 1800 for one hour, 4800 for 23 hours, zero for day 3.
    assert m.average_cost_basis_exposure == Decimal(112200)/Decimal(72)
    assert m.max_cost_basis_exposure == Decimal(4800)


def test_realized_net_drawdown_and_no_double_slippage_deduction():
    m, _, rec = project(data=history(sell_price='90'))
    assert m.net_realized_pnl == Decimal('-481.8')
    assert m.max_realized_net_drawdown == Decimal('481.8')
    assert verdict(m,rec,maximum_adverse_slippage=Decimal(10000))[0] is V.FAIL


def test_missing_fee_is_incomplete_not_zero_evidence():
    m, _, rec = project(data=history(missing_fee=True))
    assert not m.fees_complete
    v, blockers = verdict(m,rec,maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.FEES_INCOMPLETE in blockers


def test_adverse_slippage_cannot_be_cancelled_by_favorable_sell():
    m, _, rec = project()
    assert m.total_slippage == Decimal(-480)
    assert m.adverse_slippage == Decimal(4320)
    v, blockers = verdict(m,rec)
    assert v is V.FAIL and B.MAX_ADVERSE_SLIPPAGE_BREACHED in blockers


def test_partial_sell_is_not_completed_round_trip():
    m, _, _ = project(data=history(partial_sell=True), quantities={'AAPL':40})
    assert m.completed_round_trips == 0
    assert m.realized_pnl == Decimal(240)


@pytest.mark.parametrize('change,reason', [
    ({'minimum_distinct_sessions':3},B.INSUFFICIENT_SESSIONS),
    ({'minimum_completed_round_trips':2},B.INSUFFICIENT_ROUND_TRIPS),
    ({'minimum_evidence_duration':timedelta(days=2)},B.INSUFFICIENT_EVIDENCE_DURATION),
])
def test_sufficiency_is_distinct_from_failure(change,reason):
    m, _, rec = project()
    v, blockers = verdict(m,rec,maximum_adverse_slippage=Decimal(10000), **change)
    assert v is V.INSUFFICIENT and reason in blockers


def test_hard_loss_precedes_insufficient_sample():
    m, _, rec = project(data=history(sell_price='90'))
    v, blockers = verdict(m,rec,minimum_distinct_sessions=3, maximum_cumulative_loss=Decimal(10), maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.MAX_CUMULATIVE_LOSS_BREACHED in blockers
    assert B.INSUFFICIENT_SESSIONS in blockers


def test_drawdown_limit_is_hard_even_when_sample_insufficient():
    m, _, rec = project(data=history(sell_price='90'))
    v, blockers = verdict(m,rec,minimum_distinct_sessions=3,maximum_realized_net_drawdown=Decimal(10),maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.MAX_DRAWDOWN_BREACHED in blockers


@pytest.mark.parametrize('blocker',[RB.UNEXPLAINED_FILL,RB.ATTRIBUTION_MISMATCH,RB.STALE_SNAPSHOT])
def test_dirty_reconciliation_is_hard_failure(blocker):
    m, _, rec = project()
    v, blockers = verdict(m,replace(rec,blockers=(blocker,)),maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.RECONCILIATION_NOT_CLEAN in blockers


@pytest.mark.parametrize('moment',[NOW-timedelta(minutes=6), NOW+timedelta(seconds=1), NOW.replace(tzinfo=None)])
def test_reconciliation_freshness_is_required(moment):
    m, _, rec = project()
    v, blockers = verdict(m,replace(rec,observed_at=moment),maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.RECONCILIATION_STALE in blockers


def test_no_policy_no_pass_and_unsupported_policy_no_pass():
    m, _, rec = project()
    v,b = evaluate_strategy_paper_performance_policy(policy=None,metrics=m,reconciliation=rec,evaluated_at=NOW)
    assert v is V.FAIL and b == (B.POLICY_MISSING,)
    v,b = verdict(m,rec,policy_version='future',maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.POLICY_VERSION_UNSUPPORTED in b
    assert all(f.default is MISSING and f.default_factory is MISSING for f in fields(StrategyPaperPerformancePolicy))


def test_incomplete_attribution_is_hard_failure():
    m,_,rec = project()
    v,b = verdict(replace(m,attribution_complete=False),rec,maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.ATTRIBUTION_INCOMPLETE in b


def test_positive_net_requirement_only_after_sufficiency():
    m,_,rec = project(data=history(sell_price='100'))
    v,b = verdict(m,rec,maximum_adverse_slippage=Decimal(10000))
    assert v is V.FAIL and B.NON_POSITIVE_NET_RESULT in b
    assert verdict(m,rec,require_positive_net_result=False,maximum_adverse_slippage=Decimal(10000))[0] is V.PASS
    v,b = verdict(m,rec,minimum_distinct_sessions=3,maximum_adverse_slippage=Decimal(10000))
    assert v is V.INSUFFICIENT and B.NON_POSITIVE_NET_RESULT not in b


def test_declared_window_is_not_evidence_duration_and_empty_session_not_counted():
    m,_,_ = project(start=NOW-timedelta(days=90))
    assert m.evidence_duration == timedelta(days=1)
    assert m.paper_session_ids == ('buy-session','sell-session')
    m,_,rec = project(strategy='empty')
    assert m.paper_session_ids == () and m.evidence_duration == timedelta(0)
    assert verdict(m,rec)[0] is V.INSUFFICIENT


def test_source_missing_and_sorted_blockers():
    m,_,_ = project()
    v,b = evaluate_strategy_paper_performance_policy(policy=policy(),metrics=m,reconciliation=None,evaluated_at=NOW)
    assert v is V.FAIL and B.SOURCE_TRUTH_MISSING in b
    assert b == tuple(sorted(set(b)))


def test_fee_rounding_residue_stays_with_stable_last_strategy():
    from us_quant.trading.domain.portfolio_reconciliation import _allocate_decimal
    result = _allocate_decimal(Decimal(1), {'c':1,'a':1,'b':1})
    assert result['a'] == Decimal(1)/Decimal(3)
    assert result['b'] == Decimal(1)/Decimal(3)
    assert result['c'] == Decimal(1)-result['a']-result['b']
    assert sum(result.values()) == Decimal(1)


def test_real_fill_fee_75_25_uses_stage5_authority():
    from paper_performance_support import ACCOUNT_ALIAS
    from test_portfolio_reconciliation import decision_record, linked_order, fill
    from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth, replay_portfolio_execution_truth
    record = decision_record('fee',(('a','pa',75),('b','pb',25)),order_id='fee-order')
    order, attr = linked_order(record,fills=(fill('fee-execution','fee-order',100,fee='1'),))
    replay = replay_portfolio_execution_truth(now=NOW,account_alias=ACCOUNT_ALIAS,order_truth=PortfolioOrderTruth((order,)),decisions=(record,),execution_attributions=(attr,))
    assert [(f.strategy_version_id,f.allocated_fee) for f in replay.attributed_fills] == [('a',Decimal('.75')),('b',Decimal('.25'))]


def test_exact_august_buy_september_close_example():
    from datetime import datetime, timezone
    from test_portfolio_reconciliation import decision_record, linked_order, fill, Side
    from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth
    buy=decision_record('aug',(('a','aug-buy',10),),order_id='z-aug')
    sell=decision_record('sep',(('a','sep-sell',-10),),order_id='a-sep')
    bo,ba=linked_order(buy,fills=(fill('aug-fill','z-aug',10,price='100',occurred_at=datetime(2026,8,30,tzinfo=timezone.utc)),))
    so,sa=linked_order(sell,fills=(fill('sep-fill','a-sep',10,side=Side.SELL,price='110',occurred_at=datetime(2026,9,5,tzinfo=timezone.utc)),))
    m,_,_=project(data=((buy,sell),(ba,sa),PortfolioOrderTruth((so,bo))),start=datetime(2026,9,1,tzinfo=timezone.utc),end=datetime(2026,9,30,tzinfo=timezone.utc))
    assert m.realized_pnl==100 and m.completed_round_trips==1
    assert m.gross_traded_notional==1100


def test_reopen_and_close_counts_two_round_trips():
    from test_portfolio_reconciliation import decision_record, linked_order, fill, Side
    from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth
    records=[]
    attrs=[]
    orders=[]
    for index,quantity in enumerate((10,-10,10,-10)):
        order_id=f'order-{3-index}'
        record=decision_record(f'd-{index}',(('a',f'p-{index}',quantity),),order_id=order_id)
        order,attr=linked_order(record,fills=(fill(f'e-{index}',order_id,10,side=Side.BUY if quantity>0 else Side.SELL,price='100' if quantity>0 else '110',occurred_at=NOW-timedelta(hours=4-index)),))
        records.append(record);attrs.append(attr);orders.append(order)
    m,_,_=project(data=(tuple(records),tuple(attrs),PortfolioOrderTruth(tuple(orders))))
    assert m.completed_round_trips==2 and m.realized_pnl==200


@pytest.mark.parametrize('changes',[{'revision':True},{'minimum_distinct_sessions':0},{'maximum_cumulative_loss':Decimal('NaN')},
    {'minimum_evidence_duration':timedelta(0)},{'require_complete_fees':1},{'created_at':NOW.replace(tzinfo=None)}])
def test_policy_refuses_invalid_values(changes):
    with pytest.raises(ValueError):
        policy(**changes)
