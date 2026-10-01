"""P01-P12: authority boundaries, retirement and immutable persistence guards."""
import ast
from dataclasses import fields, MISSING
from pathlib import Path
from us_quant.trading.domain.strategy_paper_performance import StrategyPaperPerformancePolicy

ROOT=Path(__file__).resolve().parents[1]
TRADING=ROOT/'src/us_quant/trading'


def tree(path):
    return ast.parse(path.read_text(encoding='utf-8'))


def calls(node):
    return [n.func.id if isinstance(n.func,ast.Name) else n.func.attr
            for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,(ast.Name,ast.Attribute))]


def test_p01_exactly_one_canonical_partial_fill_scaling_implementation():
    owners=[]
    scaling_sites=[]
    for path in TRADING.rglob('*.py'):
        for node in ast.walk(tree(path)):
            if isinstance(node,ast.FunctionDef) and node.name=='_scale_contributions':
                owners.append(path)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='divmod':
                # Risk-approved attribution scales signed_requested_quantity;
                # partial-fill scaling scales the durable signed_quantity.
                if any(isinstance(n,ast.Attribute) and n.attr=='signed_quantity' for n in ast.walk(node)):
                    scaling_sites.append(path)
    assert owners==[TRADING/'domain/portfolio_reconciliation.py']
    assert scaling_sites==[TRADING/'domain/portfolio_reconciliation.py']


def test_p02_reconciliation_consumes_replay_and_old_inline_loop_is_retired():
    nodes={n.name:n for n in tree(TRADING/'domain/portfolio_reconciliation.py').body if isinstance(n,ast.FunctionDef)}
    assert calls(nodes['reconcile_portfolio_truth']).count('replay_portfolio_execution_truth')==1
    assert '_scale_contributions' not in calls(nodes['reconcile_portfolio_truth'])
    assert '_apply_fill' not in calls(nodes['reconcile_portfolio_truth'])
    assert calls(nodes['replay_portfolio_execution_truth']).count('_scale_contributions')==1
    assert calls(nodes['replay_portfolio_execution_truth']).count('_apply_fill')==1


def test_p03_performance_consumes_same_replay_without_accounting_copy():
    app=tree(TRADING/'application/strategy_paper_performance.py')
    assert calls(app).count('replay_portfolio_execution_truth')==1
    for path in (TRADING/'application/strategy_paper_performance.py',TRADING/'domain/strategy_paper_performance.py'):
        names=calls(tree(path))
        assert not {'_scale_contributions','_apply_fill','_allocate_decimal','divmod'} & set(names)


def test_p04_p08_p12_no_runtime_broker_execution_or_lifecycle_authority():
    forbidden=('session_book','sessionbook','adapters','risk','execution','dispatch','ibkr','runtime','desktop','qt','pyside','lifecycle')
    for path in (TRADING/'domain/strategy_paper_performance.py',TRADING/'application/strategy_paper_performance.py'):
        parsed=tree(path)
        for node in ast.walk(parsed):
            if isinstance(node,ast.ImportFrom):
                assert not any(part in forbidden for part in (node.module or '').lower().split('.'))
            if isinstance(node,ast.Import):
                assert not any(part in forbidden for alias in node.names for part in alias.name.lower().split('.'))
            if isinstance(node,ast.Attribute):
                assert node.attr not in ('transition','update_deployment','evaluate_cycle','gate_passed','gate_reason','record_dispatch_outcome','record_intent')
        assert not any(isinstance(node,ast.FunctionDef) and node.name in ('pause_strategy','transition') for node in ast.walk(parsed))


def test_p09_no_policy_defaults():
    assert all(field.default is MISSING and field.default_factory is MISSING for field in fields(StrategyPaperPerformancePolicy))


def test_p10_repository_append_only_cas():
    text=(TRADING/'adapters/sqlite/strategy_paper_performance_repository.py').read_text()
    assert 'BEGIN IMMEDIATE' in text and 'current != expected_current_revision' in text
    assert 'UPDATE ' not in text and 'DELETE ' not in text and 'REPLACE INTO' not in text


def test_p11_semantic_identity_excludes_wall_clock():
    parsed=tree(TRADING/'domain/strategy_paper_performance.py')
    identity=next(node for node in parsed.body if isinstance(node,ast.FunctionDef) and node.name=='stable_strategy_paper_performance_evaluation_id')
    assert "payload.pop('evaluated_at', None)" in ast.unparse(identity)
    assert 'digest' in calls(identity)
