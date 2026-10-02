"""SQLite persistence for immutable strategy-search policies and generations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    STRATEGY_CANDIDATE_GENERATOR_VERSION,
    StrategyCandidateLineage,
    StrategySearchGeneration,
    StrategySearchParameterRule,
    StrategySearchPolicy,
    StrategySearchValueKind,
    generation_semantic_payload,
)
from us_quant.trading.ports.strategy_search_repository import (
    StrategySearchRepositoryConflict,
    StrategySearchRepositoryError,
    StrategySearchRepositoryNotFound,
)

_CREATE_POLICY_TABLE = """
CREATE TABLE IF NOT EXISTS strategy_search_policy (
    policy_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (policy_id, revision)
)
"""

_CREATE_GENERATION_TABLE = """
CREATE TABLE IF NOT EXISTS strategy_search_generation (
    generation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    parent_version_id TEXT NOT NULL,
    parent_parameter_hash TEXT NOT NULL,
    generation_number INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    admitted_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
)
"""

_POLICY_COLUMNS = (
    "policy_id, revision, policy_version, strategy_id, created_at, "
    "payload_json, payload_hash"
)
_GENERATION_COLUMNS = (
    "generation_id, strategy_id, parent_version_id, parent_parameter_hash, "
    "generation_number, policy_id, policy_revision, policy_version, "
    "generated_at, admitted_at, payload_json, payload_hash"
)


class SQLiteStrategySearchRepository:
    """One durable surface for search-policy revisions and generation facts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._initialize_generation_lock()

    @contextmanager
    def serialize_generation_admission(self):
        """Serialize admission across processes without locking the data DB."""
        lock_path = self.path.with_name(self.path.name + ".generation-lock.sqlite3")
        try:
            connection = sqlite3.connect(lock_path, timeout=30)
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot serialize generation admission: {error}"
            ) from error

    def _initialize_generation_lock(self) -> None:
        lock_path = self.path.with_name(self.path.name + ".generation-lock.sqlite3")
        try:
            with closing(sqlite3.connect(lock_path, timeout=30)) as connection:
                connection.execute("PRAGMA busy_timeout = 30000")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS generation_lock (id INTEGER PRIMARY KEY)"
                )
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot initialize generation admission lock: {error}"
            ) from error

    def append_policy_revision(
        self,
        policy: StrategySearchPolicy,
        *,
        expected_current_revision: int | None,
    ) -> None:
        if not isinstance(policy, StrategySearchPolicy):
            raise TypeError("policy must be StrategySearchPolicy")
        if expected_current_revision is not None and (
            type(expected_current_revision) is not int or expected_current_revision < 1
        ):
            raise ValueError("expected_current_revision must be positive or None")
        expected_next = 1 if expected_current_revision is None else expected_current_revision + 1
        if policy.revision != expected_next:
            raise StrategySearchRepositoryConflict(
                "policy revision must advance exactly once"
            )
        payload_json = _canonical_json(_policy_to_payload(policy))
        payload_hash = _sha256(payload_json)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM strategy_search_policy "
                    "WHERE policy_id = ? ORDER BY revision",
                    (policy.policy_id,),
                ).fetchall()
                revisions = tuple(row["revision"] for row in rows)
                if revisions != tuple(range(1, len(revisions) + 1)):
                    raise StrategySearchRepositoryError("policy revision history has a gap")
                current = revisions[-1] if revisions else None
                if current != expected_current_revision:
                    raise StrategySearchRepositoryConflict(
                        f"policy compare-and-set failed: expected "
                        f"{expected_current_revision}, found {current}"
                    )
                connection.execute(
                    f"INSERT INTO strategy_search_policy ({_POLICY_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        policy.policy_id,
                        policy.revision,
                        policy.policy_version,
                        policy.strategy_id,
                        _utc_text(policy.created_at),
                        payload_json,
                        payload_hash,
                    ),
                )
                connection.commit()
        except StrategySearchRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategySearchRepositoryConflict(
                f"cannot append search policy revision: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot append search policy revision: {error}"
            ) from error

    def get_policy(self, policy_id: str, revision: int) -> StrategySearchPolicy:
        _require_text(policy_id, "policy_id")
        if type(revision) is not int or revision < 1:
            raise ValueError("revision must be a positive integer")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM strategy_search_policy "
                    "WHERE policy_id = ? AND revision = ?",
                    (policy_id, revision),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot read search policy: {error}"
            ) from error
        if row is None:
            raise StrategySearchRepositoryNotFound(f"{policy_id}@{revision}")
        return _row_to_policy(row)

    def active_policy(self, policy_id: str) -> StrategySearchPolicy | None:
        _require_text(policy_id, "policy_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM strategy_search_policy "
                    "WHERE policy_id = ? ORDER BY revision",
                    (policy_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot read active search policy: {error}"
            ) from error
        revisions = tuple(row["revision"] for row in rows)
        if revisions != tuple(range(1, len(revisions) + 1)):
            raise StrategySearchRepositoryError("policy revision history has a gap")
        values = tuple(_row_to_policy(row) for row in rows)
        return values[-1] if values else None

    def record_generation(
        self, generation: StrategySearchGeneration
    ) -> StrategySearchGeneration:
        if not isinstance(generation, StrategySearchGeneration):
            raise TypeError("generation must be StrategySearchGeneration")
        payload_json = _canonical_json(_generation_to_payload(generation))
        payload_hash = _sha256(payload_json)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    f"SELECT {_GENERATION_COLUMNS} FROM strategy_search_generation "
                    "WHERE generation_id = ?",
                    (generation.generation_id,),
                ).fetchone()
                if row is not None:
                    stored = _row_to_generation(row)
                    if generation_semantic_payload(stored) != generation_semantic_payload(generation):
                        raise StrategySearchRepositoryConflict(
                            "generation identity already stores different semantics"
                        )
                    connection.commit()
                    return stored
                connection.execute(
                    f"INSERT INTO strategy_search_generation ({_GENERATION_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        generation.generation_id,
                        generation.strategy_id,
                        generation.parent_version_id,
                        generation.parent_parameter_hash,
                        generation.generation,
                        generation.policy_id,
                        generation.policy_revision,
                        generation.policy_version,
                        _utc_text(generation.generated_at),
                        _utc_text(generation.admitted_at),
                        payload_json,
                        payload_hash,
                    ),
                )
                connection.commit()
                return generation
        except StrategySearchRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategySearchRepositoryConflict(
                f"cannot record search generation: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot record search generation: {error}"
            ) from error

    def get_generation(self, generation_id: str) -> StrategySearchGeneration:
        _require_text(generation_id, "generation_id")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_GENERATION_COLUMNS} FROM strategy_search_generation "
                    "WHERE generation_id = ?",
                    (generation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot read search generation: {error}"
            ) from error
        if row is None:
            raise StrategySearchRepositoryNotFound(generation_id)
        return _row_to_generation(row)

    def generations_for_policy(
        self, strategy_id: str, policy_id: str
    ) -> tuple[StrategySearchGeneration, ...]:
        _require_text(strategy_id, "strategy_id")
        _require_text(policy_id, "policy_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_GENERATION_COLUMNS} FROM strategy_search_generation "
                    "WHERE strategy_id = ? AND policy_id = ?",
                    (strategy_id, policy_id),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot read search generations: {error}"
            ) from error
        values = tuple(_row_to_generation(row) for row in rows)
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item.generated_at.astimezone(timezone.utc), item.generation_id
                ),
            )
        )

    def generations_for_strategy(
        self, strategy_id: str
    ) -> tuple[StrategySearchGeneration, ...]:
        _require_text(strategy_id, "strategy_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_GENERATION_COLUMNS} FROM strategy_search_generation "
                    "WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot read strategy search generations: {error}"
            ) from error
        values = tuple(_row_to_generation(row) for row in rows)
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item.generated_at.astimezone(timezone.utc), item.generation_id
                ),
            )
        )

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(_CREATE_POLICY_TABLE)
                    connection.execute(_CREATE_GENERATION_TABLE)
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_strategy_search_generation_policy "
                        "ON strategy_search_generation "
                        "(strategy_id, policy_id, generation_number, parent_version_id, generated_at)"
                    )
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot initialize search repository: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = connect_sqlite(self.path)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as error:
            raise StrategySearchRepositoryError(
                f"cannot open search repository: {error}"
            ) from error


def _policy_to_payload(policy: StrategySearchPolicy) -> dict[str, Any]:
    return {
        "policy_id": policy.policy_id,
        "revision": policy.revision,
        "policy_version": policy.policy_version,
        "strategy_id": policy.strategy_id,
        "parameter_rules": [
            {
                "parameter_key": rule.parameter_key,
                "value_kind": rule.value_kind.value,
                "minimum": _decimal_text(rule.minimum),
                "maximum": _decimal_text(rule.maximum),
                "step": _decimal_text(rule.step),
                "maximum_delta": _decimal_text(rule.maximum_delta),
            }
            for rule in policy.parameter_rules
        ],
        "maximum_candidates_per_generation": policy.maximum_candidates_per_generation,
        "maximum_active_candidates": policy.maximum_active_candidates,
        "maximum_total_candidates": policy.maximum_total_candidates,
        "maximum_generations": policy.maximum_generations,
        "generation_cooldown_microseconds": _timedelta_microseconds(
            policy.generation_cooldown
        ),
        "deterministic_seed": policy.deterministic_seed,
        "created_at": _utc_text(policy.created_at),
    }


def _policy_from_payload(payload: Any) -> StrategySearchPolicy:
    expected_keys = {
        "policy_id", "revision", "policy_version", "strategy_id", "parameter_rules",
        "maximum_candidates_per_generation", "maximum_active_candidates",
        "maximum_total_candidates", "maximum_generations",
        "generation_cooldown_microseconds", "deterministic_seed", "created_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("search policy payload keys are invalid")
    if type(payload["generation_cooldown_microseconds"]) is not int:
        raise ValueError("generation cooldown must be integer microseconds")
    rules = payload["parameter_rules"]
    if not isinstance(rules, list):
        raise ValueError("parameter_rules must be a list")
    decoded_rules = []
    rule_keys = {
        "parameter_key", "value_kind", "minimum", "maximum", "step", "maximum_delta"
    }
    for item in rules:
        if not isinstance(item, dict) or set(item) != rule_keys:
            raise ValueError("search parameter rule keys are invalid")
        decoded_rules.append(
            StrategySearchParameterRule(
                parameter_key=item["parameter_key"],
                value_kind=StrategySearchValueKind(item["value_kind"]),
                minimum=_parse_decimal(item["minimum"]),
                maximum=_parse_decimal(item["maximum"]),
                step=_parse_decimal(item["step"]),
                maximum_delta=_parse_decimal(item["maximum_delta"]),
            )
        )
    return StrategySearchPolicy(
        policy_id=payload["policy_id"],
        revision=payload["revision"],
        policy_version=payload["policy_version"],
        strategy_id=payload["strategy_id"],
        parameter_rules=tuple(decoded_rules),
        maximum_candidates_per_generation=payload["maximum_candidates_per_generation"],
        maximum_active_candidates=payload["maximum_active_candidates"],
        maximum_total_candidates=payload["maximum_total_candidates"],
        maximum_generations=payload["maximum_generations"],
        generation_cooldown=timedelta(
            microseconds=payload["generation_cooldown_microseconds"]
        ),
        deterministic_seed=payload["deterministic_seed"],
        created_at=_parse_timestamp(payload["created_at"]),
    )


def _generation_to_payload(
    generation: StrategySearchGeneration,
) -> dict[str, Any]:
    return {
        **generation_semantic_payload(generation),
        "generated_at": _utc_text(generation.generated_at),
        "admitted_at": _utc_text(generation.admitted_at),
        "candidate_lineages": [
            {
                **_lineage_to_payload(item),
            }
            for item in generation.candidate_lineages
        ],
    }


def _lineage_to_payload(item: StrategyCandidateLineage) -> dict[str, Any]:
    return {
        "child_version_id": item.child_version_id,
        "parent_version_id": item.parent_version_id,
        "strategy_id": item.strategy_id,
        "generation": item.generation,
        "ordinal": item.ordinal,
        "policy_id": item.policy_id,
        "policy_revision": item.policy_revision,
        "policy_version": item.policy_version,
        "parent_parameter_hash": item.parent_parameter_hash,
        "candidate_parameter_hash": item.candidate_parameter_hash,
        "changed_parameter_key": item.changed_parameter_key,
        "parent_value": item.parent_value,
        "candidate_value": item.candidate_value,
        "generation_id": item.generation_id,
        "generator_version": item.generator_version,
        "created_at": _utc_text(item.created_at),
    }


def _generation_from_payload(payload: Any) -> StrategySearchGeneration:
    expected = {
        "generation_id", "strategy_id", "parent_version_id", "parent_parameter_hash",
        "generation", "policy_id", "policy_revision", "policy_version",
        "deterministic_seed", "generator_version", "generated_at", "admitted_at",
        "candidate_lineages",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("search generation payload keys are invalid")
    lineages = payload["candidate_lineages"]
    if not isinstance(lineages, list):
        raise ValueError("candidate_lineages must be a list")
    lineage_keys = {
        "child_version_id", "parent_version_id", "strategy_id", "generation", "ordinal",
        "policy_id", "policy_revision", "policy_version", "parent_parameter_hash",
        "candidate_parameter_hash", "changed_parameter_key", "parent_value",
        "candidate_value", "generation_id", "generator_version", "created_at",
    }
    values = []
    for item in lineages:
        if not isinstance(item, dict) or set(item) != lineage_keys:
            raise ValueError("candidate lineage keys are invalid")
        values.append(
            StrategyCandidateLineage(
                child_version_id=item["child_version_id"],
                parent_version_id=item["parent_version_id"],
                strategy_id=item["strategy_id"],
                generation=item["generation"],
                ordinal=item["ordinal"],
                policy_id=item["policy_id"],
                policy_revision=item["policy_revision"],
                policy_version=item["policy_version"],
                parent_parameter_hash=item["parent_parameter_hash"],
                candidate_parameter_hash=item["candidate_parameter_hash"],
                changed_parameter_key=item["changed_parameter_key"],
                parent_value=item["parent_value"],
                candidate_value=item["candidate_value"],
                generation_id=item["generation_id"],
                generator_version=item["generator_version"],
                created_at=_parse_timestamp(item["created_at"]),
            )
        )
    return StrategySearchGeneration(
        generation_id=payload["generation_id"],
        strategy_id=payload["strategy_id"],
        parent_version_id=payload["parent_version_id"],
        parent_parameter_hash=payload["parent_parameter_hash"],
        generation=payload["generation"],
        policy_id=payload["policy_id"],
        policy_revision=payload["policy_revision"],
        policy_version=payload["policy_version"],
        deterministic_seed=payload["deterministic_seed"],
        candidate_lineages=tuple(values),
        generator_version=payload["generator_version"],
        generated_at=_parse_timestamp(payload["generated_at"]),
        admitted_at=_parse_timestamp(payload["admitted_at"]),
    )


def _row_to_policy(row: sqlite3.Row) -> StrategySearchPolicy:
    try:
        payload = _verified_payload(row["payload_json"], row["payload_hash"])
        policy = _policy_from_payload(payload)
        if _canonical_json(_policy_to_payload(policy)) != row["payload_json"]:
            raise ValueError("policy payload is not canonical")
        if (
            row["policy_id"] != policy.policy_id
            or row["revision"] != policy.revision
            or row["policy_version"] != policy.policy_version
            or row["strategy_id"] != policy.strategy_id
            or row["created_at"] != _utc_text(policy.created_at)
        ):
            raise ValueError("indexed policy identity differs from its payload")
        return policy
    except StrategySearchRepositoryError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation) as error:
        raise StrategySearchRepositoryError(
            f"corrupt search policy row: {error}"
        ) from error


def _row_to_generation(row: sqlite3.Row) -> StrategySearchGeneration:
    try:
        payload = _verified_payload(row["payload_json"], row["payload_hash"])
        generation = _generation_from_payload(payload)
        if _canonical_json(_generation_to_payload(generation)) != row["payload_json"]:
            raise ValueError("generation payload is not canonical")
        if (
            row["generation_id"] != generation.generation_id
            or row["strategy_id"] != generation.strategy_id
            or row["parent_version_id"] != generation.parent_version_id
            or row["parent_parameter_hash"] != generation.parent_parameter_hash
            or row["generation_number"] != generation.generation
            or row["policy_id"] != generation.policy_id
            or row["policy_revision"] != generation.policy_revision
            or row["policy_version"] != generation.policy_version
            or row["generated_at"] != _utc_text(generation.generated_at)
            or row["admitted_at"] != _utc_text(generation.admitted_at)
        ):
            raise ValueError("indexed generation identity differs from its payload")
        return generation
    except StrategySearchRepositoryError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation) as error:
        raise StrategySearchRepositoryError(
            f"corrupt search generation row: {error}"
        ) from error


def _verified_payload(payload_json: Any, payload_hash: Any) -> dict[str, Any]:
    if not isinstance(payload_json, str) or not isinstance(payload_hash, str):
        raise ValueError("payload and hash must be text")
    if _sha256(payload_json) != payload_hash:
        raise ValueError("payload hash mismatch")
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if _canonical_json(payload) != payload_json:
        raise ValueError("payload JSON is not canonical")
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse_decimal(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Decimal payload values must be strings")
    parsed = Decimal(value)
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise ValueError("Decimal payload value is not canonical")
    return parsed


def _timedelta_microseconds(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds


def _utc_text(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "timestamp")
    if _utc_text(parsed) != value:
        raise ValueError("timestamp is not canonical UTC")
    return parsed


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = ["SQLiteStrategySearchRepository"]
