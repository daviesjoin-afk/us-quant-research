"""SQLite persistence for lifecycle policies and lifecycle decisions.

Two independent tables.  The frozen ``strategy_version`` / ``strategy_deployment``
schema is never altered -- ``gate_passed`` stays exactly where it is, as a
read-only compatibility column that no longer authorises anything.

The decision table is the crash-safety mechanism, not just an audit log: a row
is written ``PREPARED`` *before* the state machine moves and marked ``APPLIED``
afterwards, so an interruption leaves a reconcilable record instead of a
half-applied change or a silent repeat.  Stored payloads are hashed and
cross-checked against their indexed columns on every read, so a corrupted row
fails closed rather than being served.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from us_quant.sqlite_support import connect_sqlite, connect_sqlite_readonly
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleBlocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
    LIFECYCLE_CONTROLLER_VERSION,
    StrategyLifecyclePolicy,
    StrategyLifecyclePolicyMalformed,
    policy_from_payload,
    policy_to_payload,
    stable_lifecycle_decision_id,
)
from us_quant.trading.ports.strategy_lifecycle_repository import (
    StrategyLifecycleRepositoryConflict,
    StrategyLifecycleRepositoryError,
    StrategyLifecycleRepositoryNotFound,
)


_CREATE_POLICY_TABLE = """
CREATE TABLE strategy_lifecycle_policy (
    policy_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (policy_id, revision)
)
"""

_CREATE_DECISION_TABLE = """
CREATE TABLE strategy_lifecycle_decision (
    decision_id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL,
    strategy_semver TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    universe_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    source_status TEXT NOT NULL,
    target_status TEXT NOT NULL,
    state TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    triggers_json TEXT NOT NULL,
    policy_id TEXT,
    policy_revision INTEGER,
    policy_version TEXT,
    authentication_id TEXT,
    gate_evaluation_id TEXT,
    coverage_evaluation_id TEXT,
    coverage_policy_id TEXT,
    coverage_policy_revision INTEGER,
    controller_version TEXT NOT NULL,
    authorized_at TEXT NOT NULL,
    applied_at TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
)
"""

_CREATE_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_strategy_lifecycle_version_time
    ON strategy_lifecycle_decision(strategy_version_id, authorized_at, decision_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_strategy_lifecycle_state
    ON strategy_lifecycle_decision(state, strategy_version_id, decision_id);
    """,
)

_POLICY_COLUMNS = (
    "policy_id, revision, policy_version, created_at, payload_json, payload_hash"
)
_POLICY_KEYS = frozenset(
    {
        "policy_id", "revision", "policy_version", "permitted_actions",
        "required_gate_policy_version", "required_coverage_policy_version",
        "maximum_evidence_age_seconds", "created_at",
    }
)

_DECISION_COLUMNS = (
    "decision_id, strategy_version_id, strategy_semver, parameter_hash, "
    "universe_hash, code_hash, action, source_status, target_status, state, "
    "blockers_json, triggers_json, policy_id, policy_revision, policy_version, "
    "authentication_id, gate_evaluation_id, coverage_evaluation_id, "
    "coverage_policy_id, coverage_policy_revision, controller_version, "
    "authorized_at, applied_at, payload_json, payload_hash"
)
_DECISION_KEYS = frozenset(
    {
        "decision_id", "strategy_version_id", "strategy_semver", "parameter_hash",
        "universe_hash", "code_hash", "action", "source_status", "target_status",
        "state", "blockers", "triggers", "policy_id", "policy_revision",
        "policy_version", "authentication_id", "gate_evaluation_id",
        "coverage_evaluation_id", "coverage_policy_id", "coverage_policy_revision",
        "controller_version", "authorized_at", "applied_at",
    }
)
_DECISION_PLACEHOLDERS = ", ".join("?" for _ in _DECISION_COLUMNS.split(","))


class SQLiteStrategyLifecycleRepository:
    """Persist lifecycle policies and decisions without owning the state machine."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self._read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    # -- policies --------------------------------------------------------

    def append_policy_revision(
        self,
        policy: StrategyLifecyclePolicy,
        *,
        expected_current_revision: int | None,
    ) -> None:
        if not isinstance(policy, StrategyLifecyclePolicy):
            raise TypeError("policy must be StrategyLifecyclePolicy")
        if expected_current_revision is not None and (
            type(expected_current_revision) is not int
            or expected_current_revision < 1
        ):
            raise ValueError(
                "expected_current_revision must be a positive integer or None"
            )
        expected_next = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if policy.revision != expected_next:
            raise StrategyLifecycleRepositoryConflict(
                "policy revision must be exactly one greater than the expected current"
            )
        payload_json = _canonical_json(policy_to_payload(policy))
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                with connection:
                    row = connection.execute(
                        "SELECT MAX(revision) FROM strategy_lifecycle_policy "
                        "WHERE policy_id = ?",
                        (policy.policy_id,),
                    ).fetchone()
                    current = row[0] if row is not None else None
                    if current != expected_current_revision:
                        raise StrategyLifecycleRepositoryConflict(
                            "policy revision compare-and-set failed: expected "
                            f"{expected_current_revision}, found {current}"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_lifecycle_policy ({_POLICY_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            policy.policy_id,
                            policy.revision,
                            policy.policy_version,
                            policy.created_at.isoformat(),
                            payload_json,
                            payload_hash,
                        ),
                    )
        except StrategyLifecycleRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategyLifecycleRepositoryConflict(
                f"cannot append lifecycle policy: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot append lifecycle policy: {error}"
            ) from error

    def get_policy(self, policy_id: str, revision: int) -> StrategyLifecyclePolicy:
        _require_text(policy_id, "policy_id")
        if type(revision) is not int or revision < 1:
            raise ValueError("revision must be a positive integer")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM strategy_lifecycle_policy "
                    "WHERE policy_id = ? AND revision = ?",
                    (policy_id, revision),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot read lifecycle policy: {error}"
            ) from error
        if row is None:
            raise StrategyLifecycleRepositoryNotFound(f"{policy_id}@{revision}")
        return _row_to_policy(row)

    def active_policy(self, policy_id: str) -> StrategyLifecyclePolicy | None:
        _require_text(policy_id, "policy_id")
        revisions = self.policy_revisions(policy_id)
        if not revisions:
            return None
        return self.get_policy(policy_id, revisions[-1])

    def policy_revisions(self, policy_id: str) -> tuple[int, ...]:
        _require_text(policy_id, "policy_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT revision FROM strategy_lifecycle_policy "
                    "WHERE policy_id = ? ORDER BY revision",
                    (policy_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot list lifecycle policy revisions: {error}"
            ) from error
        return tuple(int(row[0]) for row in rows)

    # -- decisions -------------------------------------------------------

    def record_decision(self, decision: StrategyLifecycleDecision) -> None:
        if not isinstance(decision, StrategyLifecycleDecision):
            raise TypeError("decision must be StrategyLifecycleDecision")
        expected_id = _canonical_decision_id(decision)
        if (_is_current_controller(decision)
                and expected_id != decision.decision_id):
            raise StrategyLifecycleRepositoryError(
                "current lifecycle controller decision ID is not canonical"
            )
        payload_json = _canonical_json(_decision_payload(decision))
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                with connection:
                    existing = connection.execute(
                        f"SELECT {_DECISION_COLUMNS} FROM strategy_lifecycle_decision "
                        "WHERE decision_id = ?",
                        (decision.decision_id,),
                    ).fetchone()
                    if existing is not None:
                        stored = _row_to_decision(existing)
                        if _semantic_payload(
                            _canonical_json(_decision_payload(stored))
                        ) == _semantic_payload(payload_json):
                            return
                        raise StrategyLifecycleRepositoryConflict(
                            "decision id already has a different immutable payload"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_lifecycle_decision ({_DECISION_COLUMNS}) "
                        f"VALUES ({_DECISION_PLACEHOLDERS})",
                        _decision_column_values(decision, payload_json, payload_hash),
                    )
        except StrategyLifecycleRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategyLifecycleRepositoryConflict(
                f"cannot record lifecycle decision: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot record lifecycle decision: {error}"
            ) from error

    def get_decision(self, decision_id: str) -> StrategyLifecycleDecision:
        _require_text(decision_id, "decision_id")
        row = self._decision_row(decision_id)
        if row is None:
            raise StrategyLifecycleRepositoryNotFound(decision_id)
        return _row_to_decision(row)

    def decisions_for_version(
        self, version_id: str
    ) -> tuple[StrategyLifecycleDecision, ...]:
        values = self._query_decisions(
            "WHERE strategy_version_id = ?", (version_id,), "list lifecycle decisions"
        )
        return tuple(sorted(values, key=_sort_key, reverse=True))

    def prepared_decisions(
        self, version_id: str
    ) -> tuple[StrategyLifecycleDecision, ...]:
        values = self._query_decisions(
            "WHERE strategy_version_id = ? AND state = ?",
            (version_id, StrategyLifecycleDecisionState.PREPARED.value),
            "list prepared lifecycle decisions",
        )
        return tuple(sorted(values, key=_sort_key))

    def mark_applied(
        self, decision_id: str, *, applied_at: datetime
    ) -> StrategyLifecycleDecision:
        if not isinstance(applied_at, datetime):
            raise TypeError("applied_at must be a datetime")
        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise ValueError("applied_at must be timezone-aware")
        return self._advance(
            decision_id,
            expected=StrategyLifecycleDecisionState.PREPARED,
            target=StrategyLifecycleDecisionState.APPLIED,
            applied_at=applied_at,
        )

    def mark_superseded(self, decision_id: str) -> StrategyLifecycleDecision:
        return self._advance(
            decision_id,
            expected=StrategyLifecycleDecisionState.PREPARED,
            target=StrategyLifecycleDecisionState.SUPERSEDED,
            applied_at=None,
        )

    def _advance(
        self,
        decision_id: str,
        *,
        expected: StrategyLifecycleDecisionState,
        target: StrategyLifecycleDecisionState,
        applied_at: datetime | None,
    ) -> StrategyLifecycleDecision:
        _require_text(decision_id, "decision_id")
        current = self.get_decision(decision_id)
        if current.state is target:
            # Idempotent: a crash-recovered caller may safely re-assert.
            return current
        if current.state is not expected:
            raise StrategyLifecycleRepositoryConflict(
                f"{decision_id} is {current.state.value}, not {expected.value}"
            )
        advanced = _replace_state(current, target, applied_at)
        payload_json = _canonical_json(_decision_payload(advanced))
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        "UPDATE strategy_lifecycle_decision SET state = ?, "
                        "applied_at = ?, payload_json = ?, payload_hash = ? "
                        "WHERE decision_id = ? AND state = ?",
                        (
                            target.value,
                            None if applied_at is None else applied_at.isoformat(),
                            payload_json,
                            payload_hash,
                            decision_id,
                            expected.value,
                        ),
                    )
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot advance lifecycle decision: {error}"
            ) from error
        return self.get_decision(decision_id)

    def _decision_row(self, decision_id: str) -> tuple[Any, ...] | None:
        try:
            with closing(self._connect()) as connection:
                return connection.execute(
                    f"SELECT {_DECISION_COLUMNS} FROM strategy_lifecycle_decision "
                    "WHERE decision_id = ?",
                    (decision_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot read lifecycle decision: {error}"
            ) from error

    def _query_decisions(
        self, clause: str, parameters: tuple[object, ...], action: str
    ) -> tuple[StrategyLifecycleDecision, ...]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_DECISION_COLUMNS} FROM strategy_lifecycle_decision "
                    f"{clause}",
                    parameters,
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot {action}: {error}"
            ) from error
        return tuple(_row_to_decision(row) for row in rows)

    # -- storage ---------------------------------------------------------

    def _initialize(self) -> None:
        connection = None
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                for table, create, required in (
                    (
                        "strategy_lifecycle_policy", _CREATE_POLICY_TABLE,
                        {
                            "policy_id", "revision", "policy_version",
                            "created_at", "payload_json", "payload_hash",
                        },
                    ),
                    (
                        "strategy_lifecycle_decision", _CREATE_DECISION_TABLE,
                        {
                            "decision_id", "strategy_version_id", "strategy_semver",
                            "parameter_hash", "universe_hash", "code_hash", "action",
                            "source_status", "target_status", "state",
                            "blockers_json", "triggers_json", "policy_id",
                            "policy_revision", "policy_version", "authentication_id",
                            "gate_evaluation_id", "coverage_evaluation_id",
                            "coverage_policy_id", "coverage_policy_revision",
                            "controller_version", "authorized_at", "applied_at",
                            "payload_json", "payload_hash",
                        },
                    ),
                ):
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,),
                    ).fetchone() is not None
                    if not exists:
                        connection.execute(create)
                    else:
                        _require_columns(connection, table, required)
                for statement in _CREATE_INDEXES:
                    connection.execute(statement)
                connection.commit()
        except StrategyLifecycleRepositoryError:
            raise
        except sqlite3.Error as error:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise StrategyLifecycleRepositoryError(
                f"cannot initialize lifecycle store: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        try:
            if self._read_only:
                return connect_sqlite_readonly(self.path)
            return connect_sqlite(self.path)
        except sqlite3.Error as error:
            raise StrategyLifecycleRepositoryError(
                f"cannot open lifecycle store: {error}"
            ) from error


def _require_columns(
    connection: sqlite3.Connection, table: str, required: set[str]
) -> None:
    columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    missing = required - columns
    if missing:
        raise StrategyLifecycleRepositoryError(
            f"{table} is missing columns: " + ", ".join(sorted(missing))
        )


def _replace_state(
    decision: StrategyLifecycleDecision,
    state: StrategyLifecycleDecisionState,
    applied_at: datetime | None,
) -> StrategyLifecycleDecision:
    from dataclasses import replace

    return replace(decision, state=state, applied_at=applied_at)


def _decision_payload(decision: StrategyLifecycleDecision) -> dict[str, Any]:
    payload = {
        "decision_id": decision.decision_id,
        "strategy_version_id": decision.strategy_version_id,
        "strategy_semver": decision.strategy_semver,
        "parameter_hash": decision.parameter_hash,
        "universe_hash": decision.universe_hash,
        "code_hash": decision.code_hash,
        "action": decision.action.value,
        "source_status": decision.source_status.value,
        "target_status": decision.target_status.value,
        "state": decision.state.value,
        "blockers": [item.value for item in decision.blockers],
        "triggers": [item.value for item in decision.triggers],
        "policy_id": decision.policy_id,
        "policy_revision": decision.policy_revision,
        "policy_version": decision.policy_version,
        "authentication_id": decision.authentication_id,
        "gate_evaluation_id": decision.gate_evaluation_id,
        "coverage_evaluation_id": decision.coverage_evaluation_id,
        "coverage_policy_id": decision.coverage_policy_id,
        "coverage_policy_revision": decision.coverage_policy_revision,
        "controller_version": decision.controller_version,
        "authorized_at": decision.authorized_at.isoformat(),
        "applied_at": (
            None if decision.applied_at is None else decision.applied_at.isoformat()
        ),
    }
    if decision.paper_performance_evaluation_id is not None:
        payload["paper_performance_evaluation_id"] = decision.paper_performance_evaluation_id
    return payload


def _decision_column_values(
    decision: StrategyLifecycleDecision, payload_json: str, payload_hash: str
) -> tuple[object, ...]:
    return (
        decision.decision_id,
        decision.strategy_version_id,
        decision.strategy_semver,
        decision.parameter_hash,
        decision.universe_hash,
        decision.code_hash,
        decision.action.value,
        decision.source_status.value,
        decision.target_status.value,
        decision.state.value,
        _canonical_json([item.value for item in decision.blockers]),
        _canonical_json([item.value for item in decision.triggers]),
        decision.policy_id,
        decision.policy_revision,
        decision.policy_version,
        decision.authentication_id,
        decision.gate_evaluation_id,
        decision.coverage_evaluation_id,
        decision.coverage_policy_id,
        decision.coverage_policy_revision,
        decision.controller_version,
        decision.authorized_at.isoformat(),
        None if decision.applied_at is None else decision.applied_at.isoformat(),
        payload_json,
        payload_hash,
    )


def _row_to_policy(row: tuple[Any, ...]) -> StrategyLifecyclePolicy:
    if len(row) != 6:
        raise StrategyLifecycleRepositoryError(
            "lifecycle policy row has an unexpected column count"
        )
    policy_id, revision, policy_version, created_at, payload_json, payload_hash = row
    parsed = _verified_payload(
        payload_json, payload_hash, _POLICY_KEYS, "lifecycle policy",
        optional_keys=frozenset({"paper_performance_policy_id"}),
    )
    try:
        policy = policy_from_payload(parsed)
    except StrategyLifecyclePolicyMalformed as error:
        raise StrategyLifecycleRepositoryError(
            f"stored lifecycle policy is invalid: {error}"
        ) from error
    if (
        policy.policy_id != policy_id
        or policy.revision != revision
        or policy.policy_version != policy_version
        or policy.created_at.isoformat() != created_at
    ):
        raise StrategyLifecycleRepositoryError(
            "indexed lifecycle policy columns disagree with payload"
        )
    return policy


def _row_to_decision(row: tuple[Any, ...]) -> StrategyLifecycleDecision:
    if len(row) != 25:
        raise StrategyLifecycleRepositoryError(
            "lifecycle decision row has an unexpected column count"
        )
    (
        decision_id, strategy_version_id, strategy_semver, parameter_hash,
        universe_hash, code_hash, action, source_status, target_status, state,
        blockers_json, triggers_json, policy_id, policy_revision, policy_version,
        authentication_id, gate_evaluation_id, coverage_evaluation_id,
        coverage_policy_id, coverage_policy_revision, controller_version,
        authorized_at, applied_at, payload_json, payload_hash,
    ) = row
    parsed = _verified_payload(
        payload_json, payload_hash, _DECISION_KEYS, "lifecycle decision",
        optional_keys=frozenset({"paper_performance_evaluation_id"}),
    )
    try:
        decision = StrategyLifecycleDecision(
            decision_id=_payload_text(parsed, "decision_id"),
            strategy_version_id=_payload_text(parsed, "strategy_version_id"),
            strategy_semver=_payload_text(parsed, "strategy_semver"),
            parameter_hash=_payload_text(parsed, "parameter_hash"),
            universe_hash=_payload_text(parsed, "universe_hash"),
            code_hash=_payload_text(parsed, "code_hash"),
            action=StrategyLifecycleAction(parsed["action"]),
            source_status=StrategyStatus(parsed["source_status"]),
            target_status=StrategyStatus(parsed["target_status"]),
            state=StrategyLifecycleDecisionState(parsed["state"]),
            blockers=tuple(
                StrategyLifecycleBlocker(item) for item in parsed["blockers"]
            ),
            triggers=tuple(
                StrategyLifecycleBlocker(item) for item in parsed["triggers"]
            ),
            policy_id=_payload_optional_text(parsed, "policy_id"),
            policy_revision=_payload_optional_int(parsed, "policy_revision"),
            policy_version=_payload_optional_text(parsed, "policy_version"),
            authentication_id=_payload_optional_text(parsed, "authentication_id"),
            gate_evaluation_id=_payload_optional_text(parsed, "gate_evaluation_id"),
            coverage_evaluation_id=_payload_optional_text(
                parsed, "coverage_evaluation_id"
            ),
            coverage_policy_id=_payload_optional_text(parsed, "coverage_policy_id"),
            coverage_policy_revision=_payload_optional_int(
                parsed, "coverage_policy_revision"
            ),
            controller_version=_payload_text(parsed, "controller_version"),
            paper_performance_evaluation_id=_payload_optional_text(
                parsed, "paper_performance_evaluation_id"
            ),
            authorized_at=datetime.fromisoformat(parsed["authorized_at"]),
            applied_at=(
                None
                if parsed["applied_at"] is None
                else datetime.fromisoformat(parsed["applied_at"])
            ),
        )
    except StrategyLifecycleRepositoryError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise StrategyLifecycleRepositoryError(
            f"stored lifecycle decision is invalid: {error}"
        ) from error

    indexed = (
        decision.decision_id, decision.strategy_version_id, decision.strategy_semver,
        decision.parameter_hash, decision.universe_hash, decision.code_hash,
        decision.action.value, decision.source_status.value,
        decision.target_status.value, decision.state.value,
        _canonical_json([item.value for item in decision.blockers]),
        _canonical_json([item.value for item in decision.triggers]),
        decision.policy_id, decision.policy_revision, decision.policy_version,
        decision.authentication_id, decision.gate_evaluation_id,
        decision.coverage_evaluation_id, decision.coverage_policy_id,
        decision.coverage_policy_revision, decision.controller_version,
        decision.authorized_at.isoformat(),
        None if decision.applied_at is None else decision.applied_at.isoformat(),
    )
    stored = (
        decision_id, strategy_version_id, strategy_semver, parameter_hash,
        universe_hash, code_hash, action, source_status, target_status, state,
        blockers_json, triggers_json, policy_id, policy_revision, policy_version,
        authentication_id, gate_evaluation_id, coverage_evaluation_id,
        coverage_policy_id, coverage_policy_revision, controller_version,
        authorized_at, applied_at,
    )
    if indexed != stored:
        raise StrategyLifecycleRepositoryError(
            "indexed lifecycle decision columns disagree with payload"
        )
    expected_id = _canonical_decision_id(decision)
    if (expected_id != decision.decision_id
            and (_is_current_controller(decision)
                 or decision.paper_performance_evaluation_id is not None)):
        raise StrategyLifecycleRepositoryError(
            "lifecycle decision identity disagrees with performance evidence"
        )
    return decision


def _canonical_decision_id(decision: StrategyLifecycleDecision) -> str:
    return stable_lifecycle_decision_id(
        strategy_version_id=decision.strategy_version_id,
        action=decision.action,
        source_status=decision.source_status,
        target_status=decision.target_status,
        policy_id=decision.policy_id,
        policy_revision=decision.policy_revision,
        coverage_evaluation_id=decision.coverage_evaluation_id,
        blockers=decision.blockers,
        triggers=decision.triggers,
        controller_version=decision.controller_version,
        paper_performance_evaluation_id=decision.paper_performance_evaluation_id,
    )


def _is_current_controller(decision: StrategyLifecycleDecision) -> bool:
    return decision.controller_version == LIFECYCLE_CONTROLLER_VERSION


def _verified_payload(
    payload_json: object, payload_hash: object, keys: frozenset[str], label: str,
    *, optional_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    _require_text(payload_json, "payload_json")
    _require_text(payload_hash, "payload_hash")
    try:
        parsed = json.loads(payload_json)
    except (TypeError, ValueError) as error:
        raise StrategyLifecycleRepositoryError(
            f"stored {label} payload is malformed"
        ) from error
    if not isinstance(parsed, dict) or _canonical_json(parsed) != payload_json:
        raise StrategyLifecycleRepositoryError(
            f"stored {label} payload is not canonical JSON"
        )
    if sha256(payload_json.encode("utf-8")).hexdigest() != payload_hash:
        raise StrategyLifecycleRepositoryError(f"{label} payload hash mismatch")
    if not keys <= set(parsed) or not set(parsed) <= keys | optional_keys:
        raise StrategyLifecycleRepositoryError(
            f"{label} payload has an unexpected shape"
        )
    return parsed


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StrategyLifecycleRepositoryError(f"stored {key} is missing or blank")
    return value


def _payload_optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise StrategyLifecycleRepositoryError(
            f"stored {key} is not valid optional text"
        )
    return value


def _payload_optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and (type(value) is not int or value < 1):
        raise StrategyLifecycleRepositoryError(
            f"stored {key} is not a valid positive integer"
        )
    return value


def _sort_key(value: StrategyLifecycleDecision) -> tuple[datetime, str]:
    return value.authorized_at.astimezone(timezone.utc), value.decision_id


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _semantic_payload(payload_json: str) -> str:
    """Ignore the clock fields when deciding whether a retry is the same decision.

    ``authorized_at`` and ``applied_at`` are progress markers, not part of what
    was decided; treating them as payload differences would make a legitimate
    retry look like a conflict.
    """

    payload = json.loads(payload_json)
    payload.pop("authorized_at", None)
    payload.pop("applied_at", None)
    return _canonical_json(payload)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StrategyLifecycleRepositoryError(f"{name} must be nonblank text")


__all__ = ["SQLiteStrategyLifecycleRepository"]
