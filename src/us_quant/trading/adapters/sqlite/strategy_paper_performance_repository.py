"""Append-only SQLite performance policy/evaluation stores with checked JSON."""
from contextlib import closing, contextmanager
from dataclasses import fields
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from us_quant.trading.domain.strategy_paper_performance import (
    StrategyPaperPerformancePolicy, StrategyPaperPerformanceEvaluation,
    StrategyPaperPerformanceMetrics, StrategyPaperPerformanceVerdict,
    StrategyPaperPerformanceBlocker, canonical_json, canonical_value,
)
from us_quant.trading.ports.strategy_paper_performance_repository import (
    StrategyPaperPerformanceRepositoryError as StoreError,
    StrategyPaperPerformanceRepositoryConflict as Conflict,
    StrategyPaperPerformanceRepositoryNotFound as NotFound,
)


def _seconds(value):
    micros = Decimal(value) * Decimal(1000000)
    if not micros.is_finite() or micros != micros.to_integral_value():
        raise ValueError('invalid exact duration')
    return timedelta(microseconds=int(micros))


def _decode_policy(payload):
    values = dict(payload)
    if set(values) != {field.name for field in fields(StrategyPaperPerformancePolicy)}:
        raise ValueError('policy schema mismatch')
    for name in ('minimum_evidence_duration', 'maximum_reconciliation_age'):
        values[name] = _seconds(values[name])
    for name in ('maximum_realized_net_drawdown', 'maximum_cumulative_loss', 'maximum_adverse_slippage'):
        values[name] = Decimal(values[name])
    values['created_at'] = datetime.fromisoformat(values['created_at'])
    return StrategyPaperPerformancePolicy(**values)


def _decode_evaluation(payload):
    values = dict(payload)
    if set(values) != {field.name for field in fields(StrategyPaperPerformanceEvaluation)}:
        raise ValueError('evaluation schema mismatch')
    metrics = dict(values['metrics'])
    if set(metrics) != {field.name for field in fields(StrategyPaperPerformanceMetrics)}:
        raise ValueError('metrics schema mismatch')
    for name in ('window_start', 'window_end', 'first_evidence_at', 'last_evidence_at'):
        metrics[name] = datetime.fromisoformat(metrics[name]) if metrics[name] is not None else None
    for name in ('gross_traded_notional', 'realized_pnl', 'fees', 'net_realized_pnl',
                 'total_slippage', 'adverse_slippage', 'average_cost_basis_exposure',
                 'max_cost_basis_exposure', 'max_realized_net_drawdown'):
        metrics[name] = Decimal(metrics[name])
    metrics['evidence_duration'] = _seconds(metrics['evidence_duration'])
    metrics['paper_session_ids'] = tuple(metrics['paper_session_ids'])
    values['metrics'] = StrategyPaperPerformanceMetrics(**metrics)
    for name in ('window_start', 'window_end', 'evaluated_at', 'reconciliation_observed_at'):
        values[name] = datetime.fromisoformat(values[name]) if values[name] is not None else None
    for name in ('paper_session_ids', 'source_portfolio_decision_ids', 'source_order_ids', 'source_execution_ids'):
        values[name] = tuple(values[name])
    values['verdict'] = StrategyPaperPerformanceVerdict(values['verdict'])
    values['blockers'] = tuple(StrategyPaperPerformanceBlocker(x) for x in values['blockers'])
    return StrategyPaperPerformanceEvaluation(**values)


class SQLiteStrategyPaperPerformanceRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS strategy_paper_performance_policy (
                    policy_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    policy_version TEXT NOT NULL, created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    PRIMARY KEY (policy_id, revision));
                CREATE TABLE IF NOT EXISTS strategy_paper_performance_evaluation (
                    evaluation_id TEXT PRIMARY KEY, strategy_version_id TEXT NOT NULL,
                    policy_id TEXT, policy_revision INTEGER, policy_version TEXT,
                    verdict TEXT NOT NULL, evaluated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_paper_performance_version_time ON
                    strategy_paper_performance_evaluation(strategy_version_id, evaluated_at, evaluation_id);
            ''')
            for table, required in (
                ('strategy_paper_performance_policy', {'policy_id', 'revision', 'policy_version', 'created_at', 'payload_json', 'payload_hash'}),
                ('strategy_paper_performance_evaluation', {'evaluation_id', 'strategy_version_id', 'policy_id', 'policy_revision', 'policy_version', 'verdict', 'evaluated_at', 'payload_json', 'payload_hash'}),
            ):
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
                if not required <= columns:
                    raise StoreError(f'{table}: incomplete schema')

    @contextmanager
    def _connection(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=30)) as connection:
                connection.row_factory = sqlite3.Row
                with connection:
                    yield connection
        except sqlite3.IntegrityError as error:
            raise Conflict(str(error)) from error
        except sqlite3.Error as error:
            raise StoreError(str(error)) from error

    @staticmethod
    def _read(row, *, policy=False):
        if row is None:
            raise NotFound('performance record not found')
        try:
            text = row['payload_json']
            if sha256(text.encode('utf-8')).hexdigest() != row['payload_hash']:
                raise ValueError('payload hash mismatch')
            payload = json.loads(text)
            if canonical_json(payload) != text:
                raise ValueError('noncanonical JSON')
            value = _decode_policy(payload) if policy else _decode_evaluation(payload)
            if canonical_json(value) != text:
                raise ValueError('payload semantic mismatch')
            for name in row.keys():
                if name not in ('payload_json', 'payload_hash') and row[name] != canonical_value(getattr(value, name)):
                    raise ValueError(f'indexed {name} mismatch')
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, OverflowError) as error:
            raise StoreError(f'corrupt performance record: {error}') from error

    def append_policy_revision(self, policy, *, expected_current_revision):
        if not isinstance(policy, StrategyPaperPerformancePolicy):
            raise TypeError('policy required')
        if expected_current_revision is not None and (type(expected_current_revision) is not int or expected_current_revision < 1):
            raise ValueError('invalid expected revision')
        if policy.revision != (1 if expected_current_revision is None else expected_current_revision + 1):
            raise Conflict('revision must increment exactly once')
        text = canonical_json(policy)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('SELECT * FROM strategy_paper_performance_policy WHERE policy_id=? ORDER BY revision', (policy.policy_id,)).fetchall()
            for row in rows:
                self._read(row, policy=True)
            current = rows[-1]['revision'] if rows else None
            if current != expected_current_revision:
                raise Conflict('policy compare-and-set failed')
            connection.execute('INSERT INTO strategy_paper_performance_policy VALUES (?,?,?,?,?,?)',
                               (policy.policy_id, policy.revision, policy.policy_version,
                                canonical_value(policy.created_at), text, sha256(text.encode()).hexdigest()))

    def get_policy(self, policy_id, revision):
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM strategy_paper_performance_policy WHERE policy_id=? AND revision=?', (policy_id, revision)).fetchone()
            return self._read(row, policy=True)

    def active_policy(self, policy_id):
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM strategy_paper_performance_policy WHERE policy_id=? ORDER BY revision', (policy_id,)).fetchall()
            values = tuple(self._read(row, policy=True) for row in rows)
            if tuple(value.revision for value in values) != tuple(range(1, len(values)+1)):
                raise StoreError('policy revision gap')
            return values[-1] if values else None

    def record_evaluation(self, evaluation):
        if not isinstance(evaluation, StrategyPaperPerformanceEvaluation):
            raise TypeError('evaluation required')
        text = canonical_json(evaluation)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM strategy_paper_performance_evaluation WHERE evaluation_id=?', (evaluation.evaluation_id,)).fetchone()
            if row is not None:
                stored = self._read(row)
                before = canonical_value(stored)
                after = canonical_value(evaluation)
                before.pop('evaluated_at')
                after.pop('evaluated_at')
                if before != after:
                    raise Conflict('immutable evaluation payload conflict')
                return
            connection.execute('INSERT INTO strategy_paper_performance_evaluation VALUES (?,?,?,?,?,?,?,?,?)',
                               (evaluation.evaluation_id, evaluation.strategy_version_id, evaluation.policy_id,
                                evaluation.policy_revision, evaluation.policy_version, evaluation.verdict.value,
                                canonical_value(evaluation.evaluated_at), text, sha256(text.encode()).hexdigest()))

    def get_evaluation(self, evaluation_id):
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM strategy_paper_performance_evaluation WHERE evaluation_id=?', (evaluation_id,)).fetchone()
            return self._read(row)

    def evaluations_for_version(self, version_id):
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM strategy_paper_performance_evaluation WHERE strategy_version_id=? ORDER BY evaluated_at DESC, evaluation_id', (version_id,)).fetchall()
            return tuple(self._read(row) for row in rows)

    def latest_for_version(self, version_id):
        values = self.evaluations_for_version(version_id)
        return values[0] if values else None
