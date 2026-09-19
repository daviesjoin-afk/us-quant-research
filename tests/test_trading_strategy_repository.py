"""SQLite strategy repository tests.

The repository's job is to be boring and total: store what it is given, return
something the domain will accept, and refuse -- never repair -- anything it
cannot believe.

The important test in this file is the first one.  ``RETIRED_DDL`` below is the
``CREATE`` text the retired ``StrategyRegistry`` wrote, indentation included,
because SQLite records the statement text it was handed.  A freshly created
store and a store built from that literal must have identical recorded schema,
which is what makes "an existing ``strategies.sqlite3`` opens directly, with no
drop, delete, rename or rebuild" a checkable claim rather than a promise.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pathlib
import sqlite3

import pytest

from us_quant.trading.adapters.sqlite.strategy_repository import (
    SQLiteStrategyRepository,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    canonical_parameters_json,
)
from us_quant.trading.ports.strategy_repository import (
    StrategyAuditEvent,
    StrategyRepositoryConflict,
    StrategyRepositoryError,
    StrategyRepositoryNotFound,
)

_ADAPTER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "trading"
    / "adapters"
    / "sqlite"
    / "strategy_repository.py"
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

#: The retired ``StrategyRegistry._initialize`` DDL, reproduced exactly.
RETIRED_DDL = """
                    CREATE TABLE IF NOT EXISTS strategy_definition (
                        strategy_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS strategy_version (
                        version_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        semver TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        parameter_hash TEXT NOT NULL,
                        universe_hash TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        risk_budget_pct TEXT NOT NULL,
                        gate_passed INTEGER NOT NULL,
                        gate_reason TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (strategy_id, semver),
                        FOREIGN KEY (strategy_id)
                            REFERENCES strategy_definition(strategy_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_deployment (
                        deployment_id TEXT PRIMARY KEY,
                        version_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (version_id)
                            REFERENCES strategy_version(version_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_audit (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        version_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        occurred_at TEXT NOT NULL
                    );
                    """


def _schema(path: pathlib.Path) -> dict[str, str]:
    connection = sqlite3.connect(path)
    try:
        return {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name != 'sqlite_sequence'"
            )
        }
    finally:
        connection.close()


def _legacy(
    path: pathlib.Path,
    *,
    strategy_id: str = "legacy-family",
    version_id: str = "version-legacy-1",
    semver: str = "1.0.0-research",
    status: str = "research",
    mode: str = "research",
    parameters_json: str = '{"whole_shares":true}',
    parameter_hash: str = "legacy-hash",
    risk_budget_pct: object = "0.1",
    gate_passed: object = 0,
    created_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-02-02T00:00:00+00:00",
) -> None:
    """Build a store using only the retired schema and the retired formats."""

    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executescript(RETIRED_DDL)
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_definition (
                    strategy_id, name, description, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (strategy_id, "Legacy name", "Legacy description", created_at),
            )
            connection.execute(
                """
                INSERT INTO strategy_version (
                    version_id, strategy_id, semver,
                    parameters_json, parameter_hash,
                    universe_hash, code_hash, risk_budget_pct,
                    gate_passed, gate_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    strategy_id,
                    semver,
                    parameters_json,
                    parameter_hash,
                    "legacy-universe",
                    "legacy-code",
                    risk_budget_pct,
                    gate_passed,
                    "legacy gate reason",
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_deployment (
                    deployment_id, version_id, status, mode, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (f"dep-{version_id}", version_id, status, mode, updated_at),
            )
    finally:
        connection.close()


def _block_audit_writes(path: pathlib.Path) -> None:
    """Make every ``strategy_audit`` insert abort.

    Used to prove the two write paths are transactional: a transition whose
    audit event cannot be written must not move the deployment, and an insert
    whose audit event cannot be written must leave nothing behind at all.
    """

    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                """
                CREATE TRIGGER block_audit
                BEFORE INSERT ON strategy_audit
                BEGIN
                    SELECT RAISE(ABORT, 'audit blocked');
                END
                """
            )
    finally:
        connection.close()


def _count(path: pathlib.Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - test-local literal
        ).fetchone()[0]
    finally:
        connection.close()


def _deployment_status(path: pathlib.Path, version_id: str) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT status FROM strategy_deployment WHERE version_id = ?",
            (version_id,),
        ).fetchone()[0]
    finally:
        connection.close()


def _new_version(
    *,
    version_id: str = "version-new-1",
    strategy_id: str = "new-family",
    semver: str = "1.0.0-research",
    status: StrategyStatus = StrategyStatus.RESEARCH,
    mode: StrategyMode = StrategyMode.RESEARCH,
    parameters: dict | None = None,
    risk_budget_pct: Decimal = Decimal("0.1"),
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
) -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name="New name",
            description="New description",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=version_id,
            parameter_hash="new-hash",
        ),
        semver=semver,
        status=status,
        mode=mode,
        parameters=parameters if parameters is not None else {"whole_shares": True},
        universe_hash="new-universe",
        code_hash="new-code",
        risk_budget_pct=risk_budget_pct,
        gate_passed=False,
        gate_reason="new gate reason",
        created_at=created_at,
        updated_at=updated_at,
    )


def _audit(
    version: StrategyVersion,
    *,
    event: str = "registered",
    occurred_at: datetime = NOW,
) -> StrategyAuditEvent:
    return StrategyAuditEvent(
        strategy_id=version.strategy_id,
        version_id=version.version_id,
        event=event,
        detail="detail",
        occurred_at=occurred_at,
    )


@pytest.fixture()
def store(tmp_path) -> SQLiteStrategyRepository:
    return SQLiteStrategyRepository(tmp_path / "strategies.sqlite3")


# -- schema freeze --------------------------------------------------------


def test_the_schema_matches_the_retired_registry_exactly(tmp_path) -> None:
    """A fresh store records byte-identical schema text to a legacy one."""

    legacy = tmp_path / "legacy.sqlite3"
    _legacy(legacy)
    fresh = tmp_path / "fresh.sqlite3"
    SQLiteStrategyRepository(fresh)

    assert _schema(fresh) == _schema(legacy)


def test_the_tables_are_the_frozen_four(store, tmp_path) -> None:
    assert set(_schema(tmp_path / "strategies.sqlite3")) == {
        "strategy_definition",
        "strategy_version",
        "strategy_deployment",
        "strategy_audit",
    }


def test_the_adapter_never_drops_deletes_or_renames() -> None:
    """Spec 26: no destructive migration may appear in the adapter."""

    source = _ADAPTER.read_text(encoding="utf-8")
    for keyword in ("DROP ", "DELETE ", "ALTER ", "RENAME "):
        assert keyword not in source, keyword


def test_opening_an_existing_store_keeps_its_rows(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path)
    SQLiteStrategyRepository(path)
    assert _count(path, "strategy_version") == 1
    assert _count(path, "strategy_deployment") == 1


# -- reading legacy data --------------------------------------------------


def test_a_database_built_from_the_retired_ddl_is_read_directly(
    tmp_path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path)
    version = SQLiteStrategyRepository(path).get_version("version-legacy-1")

    assert version.strategy_id == "legacy-family"
    assert version.name == "Legacy name"
    assert version.description == "Legacy description"
    assert version.version_id == "version-legacy-1"
    assert version.semver == "1.0.0-research"
    assert version.status is StrategyStatus.RESEARCH
    assert version.mode is StrategyMode.RESEARCH
    assert version.parameters == {"whole_shares": True}
    assert version.parameter_hash == "legacy-hash"
    assert version.universe_hash == "legacy-universe"
    assert version.code_hash == "legacy-code"
    assert version.gate_passed is False
    assert version.gate_reason == "legacy gate reason"


def test_risk_budget_text_decodes_to_a_decimal(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, risk_budget_pct="0.08")
    version = SQLiteStrategyRepository(path).get_version("version-legacy-1")
    assert version.risk_budget_pct == Decimal("0.08")
    assert isinstance(version.risk_budget_pct, Decimal)


def test_created_and_updated_times_are_aware_and_from_different_tables(
    tmp_path,
) -> None:
    """``created_at`` is a version fact; ``updated_at`` is a deployment fact."""

    path = tmp_path / "strategies.sqlite3"
    _legacy(
        path,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-02-02T00:00:00+00:00",
    )
    version = SQLiteStrategyRepository(path).get_version("version-legacy-1")

    assert version.created_at == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert version.updated_at == datetime(
        2026, 2, 2, tzinfo=timezone.utc
    )
    assert version.created_at.tzinfo is not None
    assert version.updated_at.tzinfo is not None


def test_a_transition_changes_only_the_deployment_timestamp(
    tmp_path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, updated_at="2026-02-02T00:00:00+00:00")
    repository = SQLiteStrategyRepository(path)
    later = datetime(2026, 3, 3, tzinfo=timezone.utc)
    repository.update_deployment(
        version_id="version-legacy-1",
        status=StrategyStatus.STOPPED,
        mode=StrategyMode.RESEARCH,
        updated_at=later,
        audit=StrategyAuditEvent(
            strategy_id="legacy-family",
            version_id="version-legacy-1",
            event="transition",
            detail="research->stopped",
            occurred_at=later,
        ),
    )
    version = repository.get_version("version-legacy-1")
    assert version.updated_at == later
    assert version.created_at == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert version.status is StrategyStatus.STOPPED


# -- failing closed -------------------------------------------------------


@pytest.mark.parametrize(
    "status,mode",
    [("Research", "research"), ("bogus", "research")],
)
def test_an_unknown_status_fails_closed(tmp_path, status, mode) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, status=status, mode=mode)
    with pytest.raises(StrategyRepositoryError, match="status"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_an_unknown_mode_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, mode="live")
    with pytest.raises(StrategyRepositoryError, match="mode"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_malformed_parameters_json_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, parameters_json="{not json")
    with pytest.raises(StrategyRepositoryError, match="parameters_json"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_a_non_object_parameters_document_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, parameters_json="[1, 2]")
    with pytest.raises(StrategyRepositoryError, match="object"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_a_naive_created_at_fails_closed(tmp_path) -> None:
    """No timezone is assumed for a timestamp that has none."""

    path = tmp_path / "strategies.sqlite3"
    _legacy(path, created_at="2026-01-01T00:00:00")
    with pytest.raises(StrategyRepositoryError, match="created_at"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_a_naive_updated_at_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, updated_at="2026-02-02T00:00:00")
    with pytest.raises(StrategyRepositoryError, match="updated_at"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_a_non_iso_timestamp_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, created_at="yesterday")
    with pytest.raises(StrategyRepositoryError):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


@pytest.mark.parametrize("flag", [2, -1, 3])
def test_a_gate_flag_that_is_not_zero_or_one_fails_closed(
    tmp_path, flag
) -> None:
    """A value that is neither 0 nor 1 must not be coerced to a bool.

    The column has INTEGER affinity, so a text ``"0"`` cannot actually survive
    a write -- which is why the out-of-range integer is the reachable case.
    The reader still refuses non-integers explicitly, because
    ``bool("0")`` is ``True`` and a future schema without affinity would make
    that path live.
    """

    path = tmp_path / "strategies.sqlite3"
    _legacy(path, gate_passed=flag)
    with pytest.raises(StrategyRepositoryError, match="gate_passed"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


def test_a_non_numeric_risk_budget_fails_closed(tmp_path) -> None:
    path = tmp_path / "strategies.sqlite3"
    _legacy(path, risk_budget_pct="not-a-number")
    with pytest.raises(StrategyRepositoryError, match="risk_budget_pct"):
        SQLiteStrategyRepository(path).get_version("version-legacy-1")


# -- not found ------------------------------------------------------------


def test_get_version_raises_not_found(store) -> None:
    with pytest.raises(StrategyRepositoryNotFound):
        store.get_version("absent")


def test_update_deployment_raises_not_found(store) -> None:
    with pytest.raises(StrategyRepositoryNotFound):
        store.update_deployment(
            version_id="absent",
            status=StrategyStatus.STOPPED,
            mode=StrategyMode.RESEARCH,
            updated_at=NOW,
            audit=StrategyAuditEvent(
                strategy_id="x",
                version_id="absent",
                event="transition",
                detail="d",
                occurred_at=NOW,
            ),
        )


# -- writing --------------------------------------------------------------


def test_insert_writes_the_four_rows(store, tmp_path) -> None:
    version = _new_version()
    store.insert_version(version, audit=_audit(version))
    path = tmp_path / "strategies.sqlite3"

    assert _count(path, "strategy_definition") == 1
    assert _count(path, "strategy_version") == 1
    assert _count(path, "strategy_deployment") == 1
    assert _count(path, "strategy_audit") == 1


def test_insert_writes_the_canonical_parameters_document(
    store, tmp_path
) -> None:
    version = _new_version(parameters={"b": 2, "a": 1})
    store.insert_version(version, audit=_audit(version))

    connection = sqlite3.connect(tmp_path / "strategies.sqlite3")
    try:
        stored = connection.execute(
            "SELECT parameters_json FROM strategy_version"
        ).fetchone()[0]
    finally:
        connection.close()
    assert stored == canonical_parameters_json({"b": 2, "a": 1})
    assert stored == '{"a":1,"b":2}'


def test_insert_stores_the_status_and_mode_as_values(store, tmp_path) -> None:
    version = _new_version(
        status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW
    )
    store.insert_version(version, audit=_audit(version))

    connection = sqlite3.connect(tmp_path / "strategies.sqlite3")
    try:
        status, mode = connection.execute(
            "SELECT status, mode FROM strategy_deployment"
        ).fetchone()
        budget = connection.execute(
            "SELECT risk_budget_pct FROM strategy_version"
        ).fetchone()[0]
    finally:
        connection.close()
    assert (status, mode) == ("paper_shadow", "paper_shadow")
    assert budget == "0.1"


def test_insert_round_trips(store) -> None:
    version = _new_version(updated_at=NOW + timedelta(hours=1))
    store.insert_version(version, audit=_audit(version))
    assert store.get_version(version.version_id) == version


def test_insert_keeps_an_existing_definition_row(store) -> None:
    """A second version of the same family must not rewrite the first name."""

    first = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(first, audit=_audit(first))
    second = _new_version(version_id="v2", semver="2.0.0")
    store.insert_version(second, audit=_audit(second))

    assert store.get_version("v1").name == "New name"
    assert store.get_version("v2").version_id == "v2"


def test_insert_refuses_a_duplicate_semver(store) -> None:
    first = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(first, audit=_audit(first))
    clash = _new_version(version_id="v2", semver="1.0.0")
    with pytest.raises(StrategyRepositoryConflict):
        store.insert_version(clash, audit=_audit(clash))


def test_a_refused_duplicate_writes_nothing(store, tmp_path) -> None:
    """A refused insert leaves the store exactly as it was.

    The uniqueness key is ``(strategy_id, semver)``, so the clash has to be the
    same family under the same semver -- a different family reusing a semver is
    a legitimate new version.
    """

    first = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(first, audit=_audit(first))
    clash = _new_version(version_id="v2", semver="1.0.0")
    with pytest.raises(StrategyRepositoryConflict):
        store.insert_version(clash, audit=_audit(clash))

    path = tmp_path / "strategies.sqlite3"
    assert _count(path, "strategy_version") == 1
    assert _count(path, "strategy_definition") == 1
    assert _count(path, "strategy_deployment") == 1
    assert _count(path, "strategy_audit") == 1
    assert store.get_version("v1") == first
    with pytest.raises(StrategyRepositoryNotFound):
        store.get_version("v2")


def test_a_different_family_may_reuse_a_semver(store) -> None:
    first = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(first, audit=_audit(first))
    other = _new_version(
        version_id="v2", strategy_id="other-family", semver="1.0.0"
    )
    store.insert_version(other, audit=_audit(other))
    assert len(store.list_versions()) == 2


def test_a_family_name_comes_from_its_first_version(store) -> None:
    """A definition row is per family, not per version -- the old behaviour.

    ``strategy_definition`` is keyed on ``strategy_id``, so the first version
    registers the name and description and later versions of the same family
    read them back.  Reproduced deliberately: the seeded catalogue has one
    family whose later version carries different description text, and changing
    this would silently rewrite what the operator sees for the earlier version.
    """

    first = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(first, audit=_audit(first))
    renamed = replace(
        first,
        definition=StrategyDefinition(
            strategy_id="new-family",
            name="A different name",
            description="A different description",
        ),
        identity=StrategyIdentity(
            strategy_id="new-family",
            version_id="v2",
            parameter_hash="new-hash",
        ),
        semver="2.0.0",
    )
    store.insert_version(renamed, audit=_audit(renamed))

    assert store.get_version("v2").name == first.name
    assert store.get_version("v2").description == first.description


def test_a_version_without_its_audit_event_is_not_written(store, tmp_path) -> None:
    """Spec 31: the insert and its audit event share one transaction."""

    path = tmp_path / "strategies.sqlite3"
    _block_audit_writes(path)
    version = _new_version()
    with pytest.raises(StrategyRepositoryError):
        store.insert_version(version, audit=_audit(version))

    assert _count(path, "strategy_definition") == 0
    assert _count(path, "strategy_version") == 0
    assert _count(path, "strategy_deployment") == 0
    assert _count(path, "strategy_audit") == 0


def test_a_transition_without_its_audit_event_is_not_applied(
    store, tmp_path
) -> None:
    """Spec 30/31: a blocked audit insert must not move the deployment."""

    version = _new_version()
    store.insert_version(version, audit=_audit(version))
    path = tmp_path / "strategies.sqlite3"
    _block_audit_writes(path)

    with pytest.raises(StrategyRepositoryError):
        store.update_deployment(
            version_id=version.version_id,
            status=StrategyStatus.STOPPED,
            mode=StrategyMode.RESEARCH,
            updated_at=NOW + timedelta(hours=1),
            audit=StrategyAuditEvent(
                strategy_id=version.strategy_id,
                version_id=version.version_id,
                event="transition",
                detail="research->stopped",
                occurred_at=NOW + timedelta(hours=1),
            ),
        )

    assert _deployment_status(path, version.version_id) == "research"
    assert _count(path, "strategy_audit") == 1


def test_a_naive_timestamp_is_refused_on_write(store) -> None:
    """The adapter will not write an un-orderable audit entry."""

    version = _new_version()
    with pytest.raises(StrategyRepositoryError, match="timezone-aware"):
        store.insert_version(
            version,
            audit=StrategyAuditEvent(
                strategy_id=version.strategy_id,
                version_id=version.version_id,
                event="registered",
                detail="d",
                occurred_at=datetime(2026, 1, 1),
            ),
        )


def test_a_naive_deployment_timestamp_is_refused_on_write(store) -> None:
    version = _new_version()
    store.insert_version(version, audit=_audit(version))
    with pytest.raises(StrategyRepositoryError, match="timezone-aware"):
        store.update_deployment(
            version_id=version.version_id,
            status=StrategyStatus.STOPPED,
            mode=StrategyMode.RESEARCH,
            updated_at=datetime(2026, 1, 1),
            audit=StrategyAuditEvent(
                strategy_id=version.strategy_id,
                version_id=version.version_id,
                event="transition",
                detail="d",
                occurred_at=NOW,
            ),
        )


# -- listing --------------------------------------------------------------


def test_list_includes_retired_versions(store) -> None:
    """Retirement is a governance status, not a reason to hide a row."""

    live = _new_version(version_id="v1", semver="1.0.0")
    store.insert_version(live, audit=_audit(live))
    retired = _new_version(
        version_id="v2",
        semver="2.0.0",
        status=StrategyStatus.LEGACY_INVALIDATED,
    )
    store.insert_version(retired, audit=_audit(retired))

    statuses = {version.status for version in store.list_versions()}
    assert statuses == {
        StrategyStatus.RESEARCH,
        StrategyStatus.LEGACY_INVALIDATED,
    }


def test_list_returns_domain_types(store) -> None:
    version = _new_version()
    store.insert_version(version, audit=_audit(version))
    listed = store.list_versions()
    assert len(listed) == 1
    assert isinstance(listed[0], StrategyVersion)


def test_an_empty_store_lists_nothing(store) -> None:
    assert store.list_versions() == ()
