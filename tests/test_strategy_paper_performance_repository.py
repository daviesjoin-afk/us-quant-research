from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import sqlite3
import pytest

from paper_performance_support import NOW, policy
from test_strategy_paper_performance_application import setup_app, evaluate
from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import SQLiteStrategyPaperPerformanceRepository as Repository
from us_quant.trading.ports.strategy_paper_performance_repository import (
    StrategyPaperPerformanceRepositoryError as StoreError,
    StrategyPaperPerformanceRepositoryConflict as Conflict,
    StrategyPaperPerformanceRepositoryNotFound as NotFound,
)


def test_policy_revisions_are_immutable_cas_and_restart_readable(tmp_path):
    path=tmp_path/'performance.sqlite'
    store=Repository(path)
    first=policy()
    store.append_policy_revision(first,expected_current_revision=None)
    with pytest.raises(Conflict):
        store.append_policy_revision(replace(first,revision=3),expected_current_revision=2)
    with pytest.raises(Conflict):
        store.append_policy_revision(replace(first,revision=2),expected_current_revision=None)
    with pytest.raises(Conflict):
        store.append_policy_revision(first,expected_current_revision=None)
    store.append_policy_revision(replace(first,revision=2),expected_current_revision=1)
    restored=Repository(path)
    assert restored.get_policy(first.policy_id,1)==first
    assert restored.active_policy(first.policy_id).revision==2
    assert restored.active_policy('missing') is None
    with pytest.raises(NotFound):
        restored.get_policy('missing',1)


def test_competing_cas_writers_exactly_one_wins(tmp_path):
    path=tmp_path/'performance.sqlite'
    first=policy()
    Repository(path).append_policy_revision(first,expected_current_revision=None)
    def append(_):
        try:
            Repository(path).append_policy_revision(replace(first,revision=2),expected_current_revision=1)
            return 'won'
        except Conflict:
            return 'lost'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(append,range(2))) == ['lost','won']


def test_timestamp_only_retry_keeps_first_row_and_latest_is_stable(tmp_path):
    components,*_=setup_app(tmp_path)
    first=evaluate(components)
    components.repository.record_evaluation(replace(first,evaluated_at=NOW+timedelta(seconds=1)))
    assert components.repository.get_evaluation(first.evaluation_id)==first
    assert components.repository.evaluations_for_version('a')==(first,)
    assert components.repository.latest_for_version('a')==first
    assert components.repository.latest_for_version('missing') is None
    with pytest.raises(NotFound):
        components.repository.get_evaluation('missing')


@pytest.mark.parametrize('table,column,value',[
    ('policy','policy_version','forged'),('policy','revision',8),('policy','created_at','forged'),
    ('evaluation','strategy_version_id','forged'),('evaluation','policy_revision',8),
    ('evaluation','verdict','FAIL'),('evaluation','evaluated_at','forged'),
    ('evaluation','payload_hash','forged'),('policy','payload_hash','forged'),
    ('evaluation','payload_json','{}'),('policy','payload_json','{}'),
])
def test_corrupt_identity_hash_and_payload_refuse_typed_error(tmp_path,table,column,value):
    components,*_=setup_app(tmp_path)
    evaluation=evaluate(components)
    with sqlite3.connect(components.repository.path) as connection:
        connection.execute(f'UPDATE strategy_paper_performance_{table} SET {column}=?',(value,))
    with pytest.raises(StoreError):
        if table=='policy':
            components.repository.active_policy('paper-policy')
        else:
            # Query by the immutable ID still detects corrupted version index.
            components.repository.get_evaluation(evaluation.evaluation_id)


def test_rehashed_semantic_tampering_is_detected(tmp_path):
    components,*_=setup_app(tmp_path)
    evaluation=evaluate(components)
    with sqlite3.connect(components.repository.path) as connection:
        payload=json.loads(connection.execute('SELECT payload_json FROM strategy_paper_performance_evaluation').fetchone()[0])
        payload['metrics']['realized_pnl']='999'
        text=json.dumps(payload,sort_keys=True,separators=(',',':'))
        connection.execute('UPDATE strategy_paper_performance_evaluation SET payload_json=?,payload_hash=?',(text,sha256(text.encode()).hexdigest()))
    with pytest.raises(StoreError):
        components.repository.get_evaluation(evaluation.evaluation_id)


def test_same_id_different_payload_conflicts(tmp_path):
    components,*_=setup_app(tmp_path)
    evaluation=evaluate(components)
    changed=replace(evaluation,evaluated_at=NOW+timedelta(seconds=1))
    # Exercise a hostile producer that circumvents the immutable constructor.
    object.__setattr__(changed,'evaluator_version','forged')
    with pytest.raises(Conflict):
        components.repository.record_evaluation(changed)


def test_incomplete_existing_schema_and_invalid_json_fail_closed(tmp_path):
    path=tmp_path/'broken.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE strategy_paper_performance_policy(policy_id TEXT, revision INTEGER)')
    with pytest.raises(StoreError):
        Repository(path)
