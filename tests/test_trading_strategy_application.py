"""Strategy application service tests.

This replaces ``tests/test_strategy_registry.py`` and carries its weight, plus
the two things the retired suite could not check: that the default catalogue
still produces exactly the rows it used to, and that the parameter hash is
still computed the same way.

``tests/fixtures/strategy_seed_baseline.json`` was captured from the retired
``StrategyRegistry`` before it was deleted.  Every field of every seeded
version is compared against it, and the ``parameter_hash`` values in that file
are the old ones -- so "old hash == new hash" is an assertion against recorded
evidence, not a re-derivation of the new algorithm against itself.

Most of these tests run over an in-memory port implementation.  That is
deliberate: if the application needed SQLite, or consumed a row shape rather
than a ``StrategyVersion``, that stub could not satisfy the port.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import pathlib

import pytest

from us_quant.trading.adapters.sqlite.strategy_repository import (
    SQLiteStrategyRepository,
)
from us_quant.trading.application.strategies import (
    StrategyApplication,
    StrategyApplicationError,
    StrategyNotFoundError,
    contains_embedded_symbol,
)
from us_quant.trading.application.strategy_defaults import (
    DEFAULT_STRATEGY_SEEDS,
)
from us_quant.trading.domain.strategy import (
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    canonical_parameters_json,
    parameter_hash_for,
)
from us_quant.trading.domain.strategy_parameters import (
    StrategyParameterError,
    validate_strategy_parameters,
)
from us_quant.trading.ports.strategy_repository import (
    StrategyRepositoryNotFound,
)

_BASELINE = json.loads(
    (
        pathlib.Path(__file__).resolve().parent
        / "fixtures"
        / "strategy_seed_baseline.json"
    ).read_text(encoding="utf-8")
)

#: The captured baseline keyed the way the catalogue is keyed, so the
#: comparison does not depend on insertion order.
_BASELINE_BY_KEY = {
    (row["strategy_id"], row["semver"]): row for row in _BASELINE
}


class _InMemoryRepository:
    """A ``StrategyRepositoryPort`` with no database behind it.

    It mirrors one rule of the SQLite adapter, because that rule is part of the
    application's outward contract: a family's definition is established by its
    first version, and later versions of the same family read that name and
    description back.  Without it, ``register`` could not be tested for that
    contract without a database.
    """

    def __init__(self, versions: tuple[StrategyVersion, ...] = ()) -> None:
        self.versions = list(versions)
        self.audits: list = []
        self.definitions: dict[str, object] = {
            version.strategy_id: version.definition for version in versions
        }

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        return tuple(self.versions)

    def get_version(self, version_id: str) -> StrategyVersion:
        for version in self.versions:
            if version.version_id == version_id:
                return version
        raise StrategyRepositoryNotFound(version_id)

    def insert_version(self, version, *, audit) -> None:
        definition = self.definitions.setdefault(
            version.strategy_id, version.definition
        )
        self.versions.append(replace(version, definition=definition))
        self.audits.append(audit)

    def update_deployment(
        self, *, version_id, status, mode, updated_at, audit
    ) -> None:
        for index, version in enumerate(self.versions):
            if version.version_id == version_id:
                self.versions[index] = replace(
                    version,
                    status=status,
                    mode=mode,
                    updated_at=updated_at,
                )
                self.audits.append(audit)
                return
        raise StrategyRepositoryNotFound(version_id)


@pytest.fixture()
def repository() -> _InMemoryRepository:
    return _InMemoryRepository()


@pytest.fixture()
def application(
    repository: _InMemoryRepository,
) -> StrategyApplication:
    return StrategyApplication(repository)


@pytest.fixture()
def seeded(
    repository: _InMemoryRepository,
) -> StrategyApplication:
    application = StrategyApplication(repository)
    application.bootstrap()
    return application


def _register(application: StrategyApplication, **overrides):
    values = {
        "strategy_id": "custom-family",
        "name": "Custom",
        "description": "A custom strategy",
        "semver": "1.0.0-research",
        "parameters": {"whole_shares": True},
        "universe_hash": "u",
        "code_hash": "c",
        "risk_budget_pct": 0.10,
    }
    values.update(overrides)
    return application.register(**values)


def _gated(
    application: StrategyApplication,
    repository: _InMemoryRepository,
    *,
    status: StrategyStatus = StrategyStatus.RESEARCH,
) -> StrategyVersion:
    """A version with the gate marked passed.

    Only an independent evaluator may do this, so the test writes the row
    behind the application's back rather than pretending ``register`` accepts
    it.
    """

    version = _register(application, status=status)
    gated = replace(version, gate_passed=True, gate_reason="independent review")
    repository.versions[-1] = gated
    return gated


# -- parameter hash compatibility (merge blocker) -------------------------


def test_the_hash_matches_the_recorded_old_hash_for_every_seed() -> None:
    """Spec 38/39/100: the algorithm is byte-compatible with the retired one.

    The chain that matters is the whole one: raw seed literal -> parameter
    validation -> canonical JSON -> SHA-256.  A change anywhere in it -- a
    reordered ``json.dumps`` argument, or a validation rule that stopped
    normalising a number to text -- shows up as a mismatched hash against
    evidence the old code recorded.
    """

    assert len(DEFAULT_STRATEGY_SEEDS) == len(_BASELINE)
    for seed in DEFAULT_STRATEGY_SEEDS:
        expected = _BASELINE_BY_KEY[(seed.strategy_id, seed.semver)]
        validated = validate_strategy_parameters(
            seed.strategy_id, seed.parameters
        )
        assert (
            canonical_parameters_json(validated)
            == expected["parameters_json"]
        ), f"{seed.strategy_id} {seed.semver} canonical form drifted"
        assert parameter_hash_for(validated) == expected["parameter_hash"], (
            f"{seed.strategy_id} {seed.semver} parameter hash drifted"
        )


def test_hashes_of_recorded_old_data_recompute_identically() -> None:
    for expected in _BASELINE:
        parameters = json.loads(expected["parameters_json"])
        assert parameter_hash_for(parameters) == expected["parameter_hash"]


def test_the_hash_ignores_key_order() -> None:
    assert parameter_hash_for({"a": 1, "b": 2}) == parameter_hash_for(
        {"b": 2, "a": 1}
    )


# -- catalogue ------------------------------------------------------------


def test_the_seeded_catalogue_matches_the_retired_one(seeded) -> None:
    """Every field of every seeded version, against the captured baseline."""

    versions = seeded.list_versions()
    assert len(versions) == len(_BASELINE)
    for version in versions:
        expected = _BASELINE_BY_KEY[
            (version.strategy_id, version.semver)
        ]
        assert version.name == expected["name"]
        assert version.description == expected["description"]
        assert version.status.value == expected["status"]
        assert version.mode.value == expected["mode"]
        assert (
            canonical_parameters_json(version.parameters)
            == expected["parameters_json"]
        )
        assert version.parameter_hash == expected["parameter_hash"]
        assert version.universe_hash == expected["universe_hash"]
        assert version.code_hash == expected["code_hash"]
        assert (
            repr(float(version.risk_budget_pct))
            == expected["risk_budget_pct"]
        )
        assert version.gate_passed is expected["gate_passed"]
        assert version.gate_reason == expected["gate_reason"]


def test_the_stale_code_hashes_are_left_alone(seeded) -> None:
    """Spec 45: an old-looking hash is not "corrected"."""

    by_key = {
        (version.strategy_id, version.semver): version
        for version in seeded.list_versions()
    }
    assert (
        by_key[("dual-ma-trend", "1.0.0-research")].code_hash
        == "unverified-local-source-0.7.0"
    )
    assert (
        by_key[("intraday-auto-rotation", "1.2.0-research")].code_hash
        == "unverified-local-source-0.18.0"
    )


def test_a_family_reads_back_its_first_registered_definition(seeded) -> None:
    """The stored definition for a family comes from its first version.

    ``sector-momentum`` has two versions whose *description* differs, but
    ``strategy_definition`` is keyed on ``strategy_id`` and written with
    ``INSERT OR IGNORE``, so both versions report the first one's text.  That
    is what the retired registry did and what the captured baseline records;
    changing it would rewrite what the operator sees for a version that was
    never edited.  Asserted so it stays a decision rather than an accident.
    """

    by_key = {
        (version.strategy_id, version.semver): version
        for version in seeded.list_versions()
    }
    older = by_key[("sector-momentum", "2.0.0-research")]
    newer = by_key[("sector-momentum", "2.1.0-research")]
    assert older.description == newer.description
    assert newer.description == _BASELINE_BY_KEY[
        ("sector-momentum", "2.0.0-research")
    ]["description"]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_register_returns_what_the_store_holds(tmp_path, backend) -> None:
    """``register`` and ``get_version`` agree about the same version id.

    A later version of an existing family cannot carry its caller's name and
    description: the family's definition was fixed by its first version.  The
    contract has to hold on the *return value* of the write, not only on a
    later read -- otherwise the same call reports two different definitions
    depending on when you look.  Asserted over both a real store and the
    in-memory port implementation, so the two cannot drift.
    """

    if backend == "memory":
        application = StrategyApplication(_InMemoryRepository())
    else:
        application = StrategyApplication(
            SQLiteStrategyRepository(tmp_path / "strategies.sqlite3")
        )

    first = application.register(
        strategy_id="same-family",
        name="First",
        description="First description",
        semver="1.0.0-research",
        parameters={"whole_shares": True},
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=0.10,
    )
    second = application.register(
        strategy_id="same-family",
        name="Renamed",
        description="Changed",
        semver="2.0.0-research",
        parameters={"whole_shares": True},
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=0.10,
    )

    assert first.name == "First"
    assert first.description == "First description"
    assert second.name == "First"
    assert second.description == "First description"
    assert second.version_id != first.version_id
    assert application.get_version(second.version_id) == second
    assert application.get_version(first.version_id) == first


def test_bootstrap_is_idempotent(application) -> None:
    assert application.bootstrap() == len(DEFAULT_STRATEGY_SEEDS)
    assert application.bootstrap() == 0
    assert len(application.list_versions()) == len(DEFAULT_STRATEGY_SEEDS)


def test_bootstrap_adds_missing_families_to_an_existing_store(
    application,
) -> None:
    _register(application, strategy_id="existing", semver="1")
    application.bootstrap()

    strategy_ids = {
        version.strategy_id for version in application.list_versions()
    }
    assert {
        "existing",
        "buy-hold",
        "dual-ma-trend",
        "donchian-breakout",
        "rsi-mean-reversion",
        "intraday-targeted-t",
        "intraday-auto-rotation",
    } <= strategy_ids


def test_no_seeded_version_hard_codes_a_symbol(seeded) -> None:
    assert not any(
        contains_embedded_symbol(version.parameters)
        for version in seeded.list_versions()
    )


# -- register -------------------------------------------------------------


def test_a_caller_cannot_self_attest_the_gate(application) -> None:
    with pytest.raises(StrategyApplicationError, match="不能自行声明"):
        _register(application, gate_passed=True)


def test_a_registered_version_never_carries_a_gate_pass(application) -> None:
    assert _register(application, gate_passed=False).gate_passed is False


@pytest.mark.parametrize("status", ["bogus", "PAPER_SHADOW"])
def test_an_unsupported_status_is_refused(application, status) -> None:
    with pytest.raises(StrategyApplicationError, match="status"):
        _register(application, status=status)


def test_an_unsupported_mode_is_refused(application) -> None:
    with pytest.raises(StrategyApplicationError, match="paper_shadow"):
        _register(application, mode="live")


@pytest.mark.parametrize("budget", [0, -0.01, 0.11, 1, "nonsense"])
def test_an_out_of_range_risk_budget_is_refused(application, budget) -> None:
    with pytest.raises(ValueError, match="risk budget"):
        _register(application, risk_budget_pct=budget)


def test_the_risk_budget_becomes_a_decimal(application) -> None:
    version = _register(application, risk_budget_pct=0.05)
    assert version.risk_budget_pct == Decimal("0.05")
    assert isinstance(version.risk_budget_pct, Decimal)


def test_parameters_are_validated(application) -> None:
    with pytest.raises(StrategyParameterError):
        _register(
            application,
            strategy_id="dual-ma-trend",
            parameters={
                "short_window": 100,
                "long_window": 20,
                "whole_shares": True,
            },
        )


def test_the_identity_describes_the_validated_parameters(application) -> None:
    """The hash is taken over the *normalised* document, not the raw input."""

    version = _register(
        application,
        strategy_id="rsi-mean-reversion",
        parameters={
            "window": 5,
            "entry_threshold": "25",
            "exit_threshold": "55",
            "whole_shares": True,
        },
    )
    assert version.parameters["entry_threshold"] == "25.0"
    assert version.parameter_hash == parameter_hash_for(version.parameters)
    assert version.parameter_hash != parameter_hash_for(
        {
            "window": 5,
            "entry_threshold": "25",
            "exit_threshold": "55",
            "whole_shares": True,
        }
    )


def test_created_and_updated_start_together_and_aware(application) -> None:
    version = _register(application)
    assert version.created_at == version.updated_at
    assert version.created_at.tzinfo is not None


def test_register_records_an_audit_event(application, repository) -> None:
    version = _register(application)
    assert repository.audits[-1].event == "registered"
    assert repository.audits[-1].version_id == version.version_id
    assert repository.audits[-1].detail == "1.0.0-research / research"


# -- clone ----------------------------------------------------------------


def test_a_clone_is_a_new_version(application) -> None:
    source = _register(application, parameters={"lookback": 20})
    clone = application.clone_version(
        source.version_id,
        semver="1.0.1-research",
        parameters={"lookback": 21},
    )
    assert clone.version_id != source.version_id
    assert clone.strategy_id == source.strategy_id
    assert clone.semver == "1.0.1-research"
    assert clone.parameter_hash != source.parameter_hash
    assert application.get_version(source.version_id).parameters == {
        "lookback": 20
    }
    assert clone.parameters == {"lookback": 21}


def test_a_clone_restarts_governance(application) -> None:
    source = _register(application)
    clone = application.clone_version(
        source.version_id, semver="2", parameters={"whole_shares": True}
    )
    assert clone.status is StrategyStatus.RESEARCH
    assert clone.mode is StrategyMode.RESEARCH
    assert clone.gate_passed is False
    assert clone.gate_reason == "参数变化后必须重新研究验证"


def test_a_clone_inherits_the_hashes_and_budget(application) -> None:
    source = _register(application, universe_hash="u2", code_hash="c2")
    clone = application.clone_version(
        source.version_id, semver="2", parameters={"whole_shares": True}
    )
    assert clone.universe_hash == "u2"
    assert clone.code_hash == "c2"
    assert clone.risk_budget_pct == source.risk_budget_pct


def test_a_retired_version_cannot_be_cloned(application) -> None:
    source = _register(
        application, status=StrategyStatus.LEGACY_INVALIDATED
    )
    with pytest.raises(StrategyApplicationError, match="不能克隆"):
        application.clone_version(
            source.version_id, semver="2", parameters={}
        )


def test_cloning_validates_the_new_parameters(seeded, application) -> None:
    source = next(
        version
        for version in seeded.list_versions()
        if version.strategy_id == "dual-ma-trend"
    )
    with pytest.raises(StrategyParameterError):
        application.clone_version(
            source.version_id,
            semver="2",
            parameters={
                "short_window": 200,
                "long_window": 20,
                "whole_shares": True,
            },
        )


# -- transition -----------------------------------------------------------


@pytest.mark.parametrize(
    "start,target",
    [
        (StrategyStatus.RESEARCH, StrategyStatus.STOPPED),
        (StrategyStatus.PAPER_SHADOW, StrategyStatus.PAUSED),
        (StrategyStatus.PAPER_SHADOW, StrategyStatus.STOPPED),
        (StrategyStatus.PAUSED, StrategyStatus.STOPPED),
    ],
)
def test_the_legal_transitions_are_allowed(
    application, start, target
) -> None:
    version = _register(application, status=start)
    moved = application.transition(
        version.version_id, target, reason="test"
    )
    assert moved.status is target


@pytest.mark.parametrize(
    "start,target",
    [
        (StrategyStatus.RESEARCH, StrategyStatus.PAUSED),
        (StrategyStatus.PAPER_SHADOW, StrategyStatus.RESEARCH),
        (StrategyStatus.PAUSED, StrategyStatus.RESEARCH),
    ],
)
def test_illegal_transitions_are_refused(application, start, target) -> None:
    version = _register(application, status=start)
    with pytest.raises(StrategyApplicationError, match="cannot transition"):
        application.transition(version.version_id, target, reason="test")


def test_entering_paper_shadow_requires_the_gate(application) -> None:
    version = _register(application, gate_reason="cost stress failed")
    with pytest.raises(
        StrategyApplicationError, match="research gate blocked"
    ):
        application.transition(
            version.version_id, StrategyStatus.PAPER_SHADOW, reason="try"
        )
    assert (
        application.get_version(version.version_id).status
        is StrategyStatus.RESEARCH
    )


def test_a_gate_passed_version_may_enter_paper_shadow(
    application, repository
) -> None:
    gated = _gated(application, repository)
    moved = application.transition(
        gated.version_id, StrategyStatus.PAPER_SHADOW, reason="reviewed"
    )
    assert moved.status is StrategyStatus.PAPER_SHADOW
    assert moved.mode is StrategyMode.PAPER_SHADOW


def test_stopped_is_terminal(application) -> None:
    version = _register(application, status=StrategyStatus.STOPPED)
    for target in StrategyStatus:
        with pytest.raises(StrategyApplicationError):
            application.transition(
                version.version_id, target, reason="try"
            )


def test_legacy_invalidated_is_terminal(application) -> None:
    version = _register(
        application, status=StrategyStatus.LEGACY_INVALIDATED
    )
    for target in StrategyStatus:
        with pytest.raises(StrategyApplicationError):
            application.transition(
                version.version_id, target, reason="try"
            )


def test_pausing_switches_the_mode_to_paper_shadow(
    application, repository
) -> None:
    gated = _gated(application, repository)
    application.transition(
        gated.version_id, StrategyStatus.PAPER_SHADOW, reason="reviewed"
    )
    paused = application.transition(
        gated.version_id, StrategyStatus.PAUSED, reason="hold"
    )
    assert paused.mode is StrategyMode.PAPER_SHADOW


def test_stopping_keeps_the_current_mode(application, repository) -> None:
    gated = _gated(application, repository)
    application.transition(
        gated.version_id, StrategyStatus.PAPER_SHADOW, reason="reviewed"
    )
    stopped = application.transition(
        gated.version_id, StrategyStatus.STOPPED, reason="kill"
    )
    assert stopped.mode is StrategyMode.PAPER_SHADOW


def test_a_transition_updates_the_timestamp_and_audits(
    application, repository
) -> None:
    version = _register(application)
    before = version.updated_at
    moved = application.transition(
        version.version_id, StrategyStatus.STOPPED, reason="operator"
    )
    assert moved.created_at == before
    # Windows clock resolution can return the same aware timestamp for two
    # immediate datetime.now() calls.  The contract is that a transition never
    # moves the deployment clock backwards, not that every transition must
    # manufacture a strictly later microsecond.
    assert moved.updated_at >= before
    assert repository.audits[-1].event == "transition"
    assert repository.audits[-1].detail == "research->stopped: operator"
    assert repository.audits[-1].occurred_at == moved.updated_at


def test_a_transition_to_an_unknown_status_is_refused(application) -> None:
    version = _register(application)
    with pytest.raises(StrategyApplicationError, match="status"):
        application.transition(version.version_id, "bogus", reason="x")


# -- legacy invalidation --------------------------------------------------


def test_embedded_symbol_versions_are_retired(
    application, repository
) -> None:
    legacy = _register(
        application,
        strategy_id="embedded-symbol-example",
        parameters={
            "signal_symbol": "ABC",
            "execution_symbol": "ALT",
            "whole_shares": True,
        },
    )
    assert application.retire_embedded_symbol_versions() == 1
    retired = application.get_version(legacy.version_id)
    assert retired.status is StrategyStatus.LEGACY_INVALIDATED
    assert retired.mode is StrategyMode.RESEARCH
    assert repository.audits[-1].event == "legacy_invalidated"


def test_the_retirement_rule_is_recursive() -> None:
    assert contains_embedded_symbol({"outer": {"signal_symbol": "ABC"}})
    assert contains_embedded_symbol(
        [{"nested": [{"execution_symbol": "X"}]}]
    )
    assert not contains_embedded_symbol({"signal_symbol": ""})
    assert not contains_embedded_symbol({"signal_symbol": "   "})
    assert not contains_embedded_symbol({"signal_symbol": 7})
    assert not contains_embedded_symbol({"whole_shares": True})


def test_an_already_retired_version_is_left_alone(application) -> None:
    _register(
        application,
        strategy_id="already",
        status=StrategyStatus.LEGACY_INVALIDATED,
        parameters={"signal_symbol": "ABC"},
    )
    assert application.retire_embedded_symbol_versions() == 0


def test_bootstrap_retires_embedded_symbols_before_seeding(
    application,
) -> None:
    legacy = _register(
        application,
        strategy_id="old-pair",
        parameters={"signal_symbol": "ABC", "whole_shares": True},
    )
    application.bootstrap()
    assert (
        application.get_version(legacy.version_id).status
        is StrategyStatus.LEGACY_INVALIDATED
    )


# -- lookup ---------------------------------------------------------------


def test_get_version_raises_not_found(application) -> None:
    with pytest.raises(StrategyNotFoundError):
        application.get_version("absent")


def test_list_returns_every_version(application) -> None:
    _register(application, semver="1")
    _register(application, semver="2")
    assert len(application.list_versions()) == 2
