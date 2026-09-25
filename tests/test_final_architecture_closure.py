"""Final Architecture Closure: cross-layer invariants.

This file is deliberately **not** a second copy of the per-capability suites.
Each capability already owns its fine-grained guards:

* ``tests/test_trading_architecture.py`` -- layer direction, canonical types,
  the execution/strategy/risk seams;
* ``tests/test_desktop_*_orchestration_architecture.py`` -- one capability each;
* ``tests/test_trading_framework_closure.py`` -- the framework closure set.

What is added here is the small set of invariants that are *cross-layer* or
*cross-capability*: properties no single capability's suite can assert, because
they are about the relationship between two of them.  The rule for adding a
test to this file is that it must fail if the boundary between two layers or two
subsystems is broken -- not if one layer's internals drift.

Guards here use ``ast`` for import and call-graph facts, ``dataclasses.fields``
for field facts, and real objects for behaviour.  None of them asserts on a
substring of the tree: ``"IBKR"`` legitimately appears in ``adapters`` and
``composition``, and ``submit`` legitimately appears in the broker adapter and
in its tests, so a text scan would be both over- and under-sensitive.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import pathlib
import pickle
import sys

import pytest

from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    parameter_hash_for,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"


# =====================================================================
# 6. Strategy immutable lifecycle
# =====================================================================


def _governed_version(parameters: Mapping[str, object]) -> StrategyVersion:
    """One internally consistent governed version, built the way the app does.

    The parameters are *not* validated here, because ``StrategyVersion`` itself
    does not validate them -- that is the application's job.  Tests that go
    through ``StrategyApplication`` pass a valid ``dual-ma-trend`` set.
    """

    now = datetime.now(timezone.utc)
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id="dual-ma-trend", name="双均线", description="fixture"
        ),
        identity=StrategyIdentity(
            strategy_id="dual-ma-trend",
            version_id="v-1",
            parameter_hash=parameter_hash_for(parameters),
        ),
        semver="1.0.0",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters=parameters,
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.05"),
        gate_passed=False,
        gate_reason="",
        created_at=now,
        updated_at=now,
    )


#: A parameter set ``validate_strategy_parameters`` accepts for the family above.
_VALID_PARAMETERS = {"short_window": "5", "long_window": "20"}


def test_fa26_a_governed_versions_parameters_cannot_be_edited_in_place() -> None:
    """FA26: ``frozen dataclass + mutable dict`` is not an immutable version.

    ``StrategyVersion`` is a frozen dataclass, but a frozen dataclass only
    refuses *attribute rebinding*.  A plain ``dict`` field can still be edited
    in place, and ``parameter_hash`` is read off the governed identity rather
    than recomputed -- so an in-place edit leaves a version whose declared hash
    no longer describes its own parameters.  That is a split identity in the
    governance audit trail, and it is the reason "immutable version" has to be
    a property of the value and not of the annotation.

    The nested list is exercised as well as the top-level scalar: a shallow
    freeze passes a scalar-only test while still aliasing the list the runtime
    reads for its market reference symbols.
    """

    version = _governed_version(
        {"short_window": "5", "lookbacks": ["63", "126"]}
    )
    before = version.parameter_hash

    with pytest.raises(TypeError):
        version.parameters["short_window"] = "999"  # type: ignore[index]

    with pytest.raises(TypeError):
        version.parameters["lookbacks"].append("504")  # type: ignore[union-attr]

    with pytest.raises(TypeError):
        version.parameters.update({"short_window": "999"})  # type: ignore[union-attr]

    # The version still describes itself: the declared hash and the parameters
    # it claims to describe agree, before and after the refused edits.
    assert version.parameters["short_window"] == "5"
    assert list(version.parameters["lookbacks"]) == ["63", "126"]
    assert parameter_hash_for(version.parameters) == before


def test_fa26b_a_governed_versions_parameters_survive_the_copy_protocol() -> None:
    """FA26b: the copy protocol used in production preserves the frozen form.

    ``strategy_launch_fact`` takes a ``copy.deepcopy`` of the parameters before
    freezing them into a launch request.  If deepcopy handed back a plain dict,
    the "frozen" request would be mutable again and the split-identity failure
    the copy exists to prevent would come straight back.

    Scope is stated precisely: what is locked is ``copy.copy`` /
    ``copy.deepcopy`` and a ``pickle`` round trip -- the three ways this codebase
    actually copies a governed value.  It is **not** a claim that every way of
    copying a mapping yields a frozen one.  The inherited
    ``FrozenParameters.copy()`` returns an ordinary ``dict``, as it does for any
    ``dict`` subclass; that is Python's own collection semantics, it cannot
    mutate the governed version it was copied from, and no production path uses
    it.  The last block records that boundary rather than pretending it away.
    """

    version = _governed_version(
        {"short_window": "5", "lookbacks": ["63", "126"]}
    )

    for clone in (copy.copy(version.parameters), copy.deepcopy(version.parameters)):
        assert isinstance(clone, dict)
        with pytest.raises(TypeError):
            clone["short_window"] = "999"
        with pytest.raises(TypeError):
            clone["lookbacks"].append("504")

    round_tripped = pickle.loads(pickle.dumps(version.parameters))
    with pytest.raises(TypeError):
        round_tripped["short_window"] = "999"

    # The values still read as ordinary dict/list values, which is what the
    # existing consumers -- json, isinstance, == against a literal -- depend on.
    assert version.parameters == {"short_window": "5", "lookbacks": ["63", "126"]}
    assert isinstance(version.parameters["lookbacks"], list)
    assert list(version.parameters["lookbacks"]) == ["63", "126"]

    # The documented boundary, asserted so the docstring cannot drift: the
    # inherited ``copy()`` yields a plain, mutable dict -- and the governed
    # version it came from is untouched by editing it.
    detached = version.parameters.copy()
    assert type(detached) is dict
    detached["short_window"] = "999"
    assert version.parameters["short_window"] == "5"
    assert parameter_hash_for(version.parameters) == version.parameter_hash


def test_fa26c_the_repository_round_trip_still_reproduces_the_same_hash(
    tmp_path: pathlib.Path,
) -> None:
    """FA26c: the freeze is representation-only -- storage evidence is unchanged.

    Every already-governed version has its ``parameter_hash`` in the database and
    referenced by backtest artifacts.  A freeze that changed the canonical JSON
    would silently re-hash the catalogue and orphan that evidence, so the
    round trip is asserted against the hash the version was built with.
    """

    from us_quant.trading.adapters.sqlite.strategy_repository import (
        SQLiteStrategyRepository,
    )
    from us_quant.trading.ports.strategy_repository import StrategyAuditEvent

    version = _governed_version(
        {"short_window": "5", "lookbacks": ["63", "126"]}
    )
    store = SQLiteStrategyRepository(tmp_path / "strategies.db")
    store.insert_version(
        version,
        audit=StrategyAuditEvent(
            strategy_id=version.strategy_id,
            version_id=version.version_id,
            event="registered",
            detail="fac fixture",
            occurred_at=version.created_at,
        ),
    )

    read_back = store.get_version(version.version_id)

    assert read_back.parameter_hash == version.parameter_hash
    assert parameter_hash_for(read_back.parameters) == version.parameter_hash
    assert dict(read_back.parameters) == dict(version.parameters)
    assert isinstance(read_back.parameters["lookbacks"], list)
    with pytest.raises(TypeError):
        read_back.parameters["short_window"] = "999"


def test_fa26d_a_frozen_mapping_cannot_be_re_populated_through_init() -> None:
    """FA26d: overriding the mutating methods is not enough on its own.

    ``dict.__init__`` is inherited, so ``params.__init__({...})`` would
    re-populate a mapping that is supposed to be immutable -- the same hole
    ``list.__init__`` opens on a frozen list.  Both types seal on the first
    ``__init__``, which closes that route while leaving normal construction
    working.
    """

    from us_quant.trading.domain.common import FrozenList, FrozenParameters

    params = FrozenParameters({"a": "1"})
    with pytest.raises(TypeError):
        params.__init__({"a": "999"})
    with pytest.raises(TypeError):
        params.__init__(a="777")
    assert dict(params) == {"a": "1"}

    # An empty frozen mapping is sealed too -- otherwise the first later
    # ``__init__`` would be treated as construction.
    empty = FrozenParameters({})
    with pytest.raises(TypeError):
        empty.__init__({"x": "1"})
    assert dict(empty) == {}

    items = FrozenList([1, 2])
    with pytest.raises(TypeError):
        items.__init__([9, 9])
    assert list(items) == [1, 2]

    # Construction and the copy protocol still work, so the seal is not simply
    # "this type cannot be built".
    assert dict(FrozenParameters({"b": "2"})) == {"b": "2"}
    assert list(FrozenList([3])) == [3]
    assert dict(copy.copy(params)) == {"a": "1"}
    assert list(copy.deepcopy(items)) == [1, 2]


def test_fa26e_tuple_descendants_of_governed_parameters_are_frozen() -> None:
    """FA26e: a tuple is immutable, but what it *holds* need not be.

    ``json.dumps`` accepts a tuple and encodes it as a JSON array, so a tuple
    container is a legitimate, hashable parameter value.  Freezing only ``dict``
    and ``list`` therefore leaves a hole: the tuple itself cannot be edited, but
    a dict or list *inside* it is never visited, so it stays a plain mutable
    container and the split identity this whole fix removes comes straight back.

    The tuple stays a tuple because converting it to a list is unnecessary
    representation normalisation -- it would change what Python consumers see
    (``isinstance(value, tuple)``) and the fix's job is to freeze descendants, not
    to re-shape the parameter.  JSON and hash compatibility are asserted below
    rather than argued: ``json.dumps`` encodes a tuple as a JSON array, so the two
    forms already hash the same, and the assertion is what proves the freeze did
    not disturb that.
    """

    parameters = {
        "custom": (
            {
                "items": [1, 2],
                "nested": {"value": ["a"]},
            },
        )
    }

    version = _governed_version(parameters)
    before = version.parameter_hash

    # The container is still a tuple, and the descendants are frozen.
    assert isinstance(version.parameters["custom"], tuple)
    assert isinstance(version.parameters["custom"][0], dict)

    with pytest.raises(TypeError):
        version.parameters["custom"][0]["items"].append(3)

    with pytest.raises(TypeError):
        version.parameters["custom"][0]["nested"]["value"].append("b")

    with pytest.raises(TypeError):
        version.parameters["custom"][0]["new"] = "x"

    # A tuple inside a tuple is reached too, so the recursion is not one level.
    deeper = _governed_version({"deep": (({"inner": [1]},),)})
    with pytest.raises(TypeError):
        deeper.parameters["deep"][0][0]["inner"].append(2)

    # The declared hash still describes the parameters it governs, and the
    # values still read as ordinary JSON-compatible values.
    assert parameter_hash_for(version.parameters) == before
    assert version.parameters["custom"] == (
        {"items": [1, 2], "nested": {"value": ["a"]}},
    )
    assert list(version.parameters["custom"][0]["items"]) == [1, 2]

    # And the hash is unchanged from the tuple's pre-freeze form: the fix is
    # representation-only, so already-governed versions keep their identity.
    assert parameter_hash_for(parameters) == before


def test_fa21_register_refuses_a_callers_own_gate_attestation() -> None:
    """FA21: nobody self-attests the research gate.

    There is no independent gate evaluator yet, so a caller able to set
    ``gate_passed=True`` could move its own strategy into ``PAPER_SHADOW``.
    """

    from us_quant.trading.application.strategies import (
        StrategyApplication,
        StrategyApplicationError,
    )

    class _Repository:
        def __init__(self) -> None:
            self.written: list = []

        def list_versions(self) -> tuple:
            return ()

        def get_version(self, version_id: str):  # pragma: no cover
            raise AssertionError("nothing was written")

        def insert_version(self, version, *, audit) -> None:
            self.written.append(version)

        def update_deployment(self, **kwargs) -> None:  # pragma: no cover
            raise AssertionError("not reached")

    repository = _Repository()
    application = StrategyApplication(repository)

    with pytest.raises(StrategyApplicationError):
        application.register(
            strategy_id="dual-ma-trend",
            name="双均线",
            description="fixture",
            semver="1.0.0",
            parameters=dict(_VALID_PARAMETERS),
            universe_hash="u",
            code_hash="c",
            risk_budget_pct=Decimal("0.05"),
            gate_passed=True,
        )

    assert repository.written == []


def test_fa22_cloning_a_version_resets_its_evidence_state() -> None:
    """FA22: a parameter change invalidates the evidence it was gathered under."""

    from us_quant.trading.application.strategies import StrategyApplication

    class _Repository:
        def __init__(self, versions: tuple) -> None:
            self.versions = list(versions)

        def list_versions(self) -> tuple:
            return tuple(self.versions)

        def get_version(self, version_id: str):
            for version in self.versions:
                if version.version_id == version_id:
                    return version
            from us_quant.trading.ports.strategy_repository import (
                StrategyRepositoryNotFound,
            )

            raise StrategyRepositoryNotFound(version_id)

        def insert_version(self, version, *, audit) -> None:
            self.versions.append(version)

        def update_deployment(self, **kwargs) -> None:  # pragma: no cover
            raise AssertionError("not reached")

    source = _governed_version(dict(_VALID_PARAMETERS))
    application = StrategyApplication(_Repository((source,)))

    clone = application.clone_version(
        source.version_id,
        semver="2.0.0",
        parameters={"short_window": "8", "long_window": "30"},
    )

    assert clone.version_id != source.version_id
    assert clone.semver == "2.0.0"
    assert clone.status is StrategyStatus.RESEARCH
    assert clone.mode is StrategyMode.RESEARCH
    assert clone.gate_passed is False
    assert clone.gate_reason


def test_fa23_stopped_and_legacy_invalidated_are_terminal() -> None:
    """FA23: the two terminal statuses have no outgoing transition."""

    from us_quant.trading.domain.strategy import ALLOWED_TRANSITIONS

    assert ALLOWED_TRANSITIONS[StrategyStatus.STOPPED] == frozenset()
    assert ALLOWED_TRANSITIONS[StrategyStatus.LEGACY_INVALIDATED] == frozenset()


def test_fa23b_entering_paper_shadow_requires_the_gate() -> None:
    """FA23b: the transition table is only the structural half of the rule."""

    from us_quant.trading.application.strategies import (
        StrategyApplication,
        StrategyApplicationError,
    )

    class _Repository:
        def __init__(self, versions: tuple) -> None:
            self.versions = list(versions)

        def list_versions(self) -> tuple:
            return tuple(self.versions)

        def get_version(self, version_id: str):
            return self.versions[0]

        def insert_version(self, version, *, audit) -> None:  # pragma: no cover
            raise AssertionError("not reached")

        def update_deployment(self, **kwargs) -> None:  # pragma: no cover
            raise AssertionError("a blocked transition must not be written")

    ungated = _governed_version({"short_window": "5", "lookbacks": ["63"]})
    application = StrategyApplication(_Repository((ungated,)))

    with pytest.raises(StrategyApplicationError):
        application.transition(
            ungated.version_id,
            StrategyStatus.PAPER_SHADOW,
            reason="fac fixture",
        )


def test_fa23c_a_retired_version_cannot_be_cloned() -> None:
    """FA23c: a falsified result is not resurrected by making a copy of it."""

    from us_quant.trading.application.strategies import (
        StrategyApplication,
        StrategyApplicationError,
    )
    from dataclasses import replace

    class _Repository:
        def __init__(self, versions: tuple) -> None:
            self.versions = list(versions)

        def list_versions(self) -> tuple:
            return tuple(self.versions)

        def get_version(self, version_id: str):
            return self.versions[0]

        def insert_version(self, version, *, audit) -> None:  # pragma: no cover
            raise AssertionError("not reached")

        def update_deployment(self, **kwargs) -> None:  # pragma: no cover
            raise AssertionError("not reached")

    retired = replace(
        _governed_version({"short_window": "5", "lookbacks": ["63"]}),
        status=StrategyStatus.LEGACY_INVALIDATED,
    )
    application = StrategyApplication(_Repository((retired,)))

    with pytest.raises(StrategyApplicationError):
        application.clone_version(
            retired.version_id,
            semver="2.0.0",
            parameters={"short_window": "8", "lookbacks": ["126"]},
        )


def test_fa24_governance_selection_cannot_write_the_runtime_selection() -> None:
    """FA24: catalogue truth and runtime-selection truth stay two things.

    ``StrategySelectionService`` reads the catalogue through
    ``StrategyApplication`` and keeps its own per-purpose choice.  What must not
    exist is a path from a governance row selection into that choice, or a future
    strategy evolution could promote itself by editing a governance view.
    """

    from us_quant.trading.application.strategy_selection import (
        StrategySelectionPurpose,
        StrategySelectionService,
    )

    class _Application:
        """A catalogue whose only write surface is the governance one."""

        def __init__(self, versions: tuple) -> None:
            self._versions = versions
            self.writes: list = []

        def list_versions(self) -> tuple:
            return self._versions

        def get_version(self, version_id: str):
            for version in self._versions:
                if version.version_id == version_id:
                    return version
            raise AssertionError(version_id)

        def register(self, **kwargs):  # pragma: no cover
            self.writes.append(kwargs)
            raise AssertionError("selection must not register")

        def transition(self, *args, **kwargs):  # pragma: no cover
            self.writes.append((args, kwargs))
            raise AssertionError("selection must not transition")

        def clone_version(self, *args, **kwargs):  # pragma: no cover
            self.writes.append((args, kwargs))
            raise AssertionError("selection must not clone")

    # One version per family, so each purpose has something it accepts.
    versions = tuple(
        replace(
            _governed_version(dict(_VALID_PARAMETERS)),
            definition=StrategyDefinition(
                strategy_id=strategy_id, name=strategy_id, description="fixture"
            ),
            identity=StrategyIdentity(
                strategy_id=strategy_id,
                version_id=f"v-{strategy_id}",
                parameter_hash=parameter_hash_for(_VALID_PARAMETERS),
            ),
        )
        for strategy_id in (
            "buy-hold",
            "intraday-targeted-t",
            "intraday-auto-rotation",
        )
    )
    application = _Application(versions)
    service = StrategySelectionService(application)  # type: ignore[arg-type]

    for purpose in StrategySelectionPurpose:
        options = service.options(purpose)
        assert options, f"{purpose} must accept one of the fixture versions"
        service.select(purpose, options[0].version_id)
        assert service.selected(purpose) is not None

    # Selection is its own state; it never wrote through the catalogue.
    assert application.writes == []

    # And the governance surface itself has no way to set a runtime selection:
    # the write methods the selection service could reach are catalogue writes,
    # not selection writes.
    assert not hasattr(StrategySelectionService, "transition")
    assert not hasattr(StrategySelectionService, "register")


# =====================================================================
# 1. Import graph
# =====================================================================


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(_SRC.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(path: pathlib.Path) -> str:
    """The package a module's relative imports are resolved against."""

    name = _module_name(path)
    if path.name == "__init__.py":
        return name
    return name.rsplit(".", 1)[0]


def _resolved_import_from_module(
    path: pathlib.Path,
    node: ast.ImportFrom,
) -> str | None:
    """The absolute module an ``ImportFrom`` names, relative spelling included.

    This is the **single** relative-import resolver in this file.  Both the
    general layer guards (through :func:`_imported_modules`) and the
    symbol-level exception guards (FA4c / FA4d) go through it, because two
    resolvers is exactly how the two would drift: the layer guards resolved
    ``from ...ibkr import X`` to ``us_quant.ibkr`` while FA4c compared the raw
    ``node.module`` (``"ibkr"``) against its ``us_quant.ibkr`` key, so a
    relative spelling walked straight past the symbol rule.

    Returns ``None`` for a relative import that climbs above the package root,
    which is not a module this tree can name.
    """

    if not node.level:
        return node.module

    parts = _package_of(path).split(".")
    up = node.level - 1
    if up > len(parts):
        return None
    base = ".".join(parts[: len(parts) - up]) if up else _package_of(path)
    if not node.module:
        return base
    return f"{base}.{node.module}"


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module this file imports, resolved through ``ast``.

    Relative imports are resolved against the file's own package, so a
    ``from ..runtime import x`` cannot slip past a prefix rule.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from_module(path, node)
            if resolved:
                found.add(resolved)
    return found


def _layer_modules(relative: str) -> list[pathlib.Path]:
    root = _SRC / relative
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _violations(relative: str, forbidden: tuple[str, ...]) -> list[str]:
    """Every ``source -> target`` edge under one path that breaks a prefix rule.

    ``relative`` may be a package (``trading/runtime``) or a single module path
    (``trading/application/risk.py``).
    """

    root = _SRC / relative
    paths = [root] if root.is_file() else _layer_modules(relative)
    out: list[str] = []
    for path in paths:
        source = _module_name(path)
        for target in sorted(_imported_modules(path)):
            if target == source:
                continue
            for prefix in forbidden:
                if target == prefix or target.startswith(prefix + "."):
                    out.append(f"{source} -> {target} (forbids {prefix})")
                    break
    return out


#: What each inward layer is *allowed* to import, as package prefixes.
#:
#: An allowlist rather than a denylist, and that choice is the point.  A
#: denylist has to enumerate every forbidden name, so a module nobody thought of
#: -- ``us_quant.desktop_workers``, say, which is neither ``us_quant.desktop``
#: nor ``us_quant.desktop_v2`` -- slips straight through, and every new file has
#: to be added to a list somewhere.  With an allowlist a new file is covered the
#: moment it is written, and the failure names the module it imported.
#:
#: Only ``us_quant.*`` targets are allowlisted here.  The standard library is
#: always permitted; the third-party set below is not, in any of these layers.
ALLOWED_INWARD_IMPORTS = {
    "trading/domain": ("us_quant.trading.domain",),
    "trading/ports": ("us_quant.trading.domain",),
    "trading/application": (
        "us_quant.trading.domain",
        "us_quant.trading.ports",
        "us_quant.trading.application",
        # Deliberate, and the only reverse edge in this layer: the Paper
        # workflow phase vocabulary is owned by the runtime (it moved there with
        # Runtime v2B and its transition table is frozen), and the Paper
        # application service reads that vocabulary to describe its own
        # lifecycle.  It names an enum and a port-shaped protocol -- no broker,
        # no store, no execution surface -- so it cannot become a bypass.
        "us_quant.trading.runtime.workflow_state",
        # Deliberate, and pre-existing: the account application owns the
        # connection *config value* (``IBKRConnectionConfig``).  The vendor
        # module is the contract for the config type; what must never appear is
        # an adapter, a stream or ``ibapi``.
        "us_quant.ibkr",
    ),
    "trading/runtime": (
        "us_quant.trading.domain",
        "us_quant.trading.ports",
        "us_quant.trading.application",
        "us_quant.trading.runtime",
        # Both are dependency-free value modules (dataclasses + Decimal only):
        # ``auto_launch`` is the immutable launch-plan fingerprint the Paper
        # workflow binds, and ``paper_order_models`` is the broker-state value
        # its health evaluator reads.
        "us_quant.auto_launch",
        "us_quant.paper_order_models",
    ),
}

#: Third-party modules that must not appear in any inward layer.  Each is a
#: concrete provider, a storage engine or a widget toolkit -- the three things
#: a port exists to keep out.
FORBIDDEN_THIRD_PARTY = (
    "PySide6",
    "ibapi",
    "sqlite3",
    "alpaca",
    "finnhub",
)


def _inward_violations(layer: str) -> list[str]:
    """Every import in ``layer`` that its allowlist does not cover."""

    allowed = ALLOWED_INWARD_IMPORTS[layer]
    out: list[str] = []
    for path in _layer_modules(layer):
        source = _module_name(path)
        for target in sorted(_imported_modules(path)):
            if target == source:
                continue
            root = target.split(".")[0]
            if root in sys.stdlib_module_names:
                continue
            if any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in allowed
            ):
                continue
            out.append(f"{source} -> {target}")
    return out


@pytest.mark.parametrize("layer", sorted(ALLOWED_INWARD_IMPORTS))
def test_fa1_to_fa4_layers_reach_nothing_they_must_not(layer: str) -> None:
    """FA1-FA4: the inward layers import only what their allowlist covers.

    Asserted per *edge*, through the AST, so the report names the source module
    and the target it should not have reached -- and a file added later is
    covered without anyone extending a whitelist.
    """

    assert _inward_violations(layer) == []


@pytest.mark.parametrize("layer", sorted(ALLOWED_INWARD_IMPORTS))
def test_fa1_to_fa4b_no_inward_layer_names_a_provider_or_a_toolkit(
    layer: str,
) -> None:
    """The provider/toolkit half, stated separately so it cannot be absorbed.

    The allowlist above is about *which* ``us_quant`` package is reachable; this
    is about the third-party names, which no allowlist entry should ever make
    reachable by accident.
    """

    out: list[str] = []
    for path in _layer_modules(layer):
        source = _module_name(path)
        for target in sorted(_imported_modules(path)):
            root = target.split(".")[0]
            if root in FORBIDDEN_THIRD_PARTY:
                out.append(f"{source} -> {target}")
    assert out == []


#: The two package-level allowlist exceptions for ``trading/application``, each
#: narrowed to the exact **symbols** production actually imports.
#:
#: The allowlist above is a package-prefix rule: it decides *direction*.  On its
#: own it would also permit ``from us_quant.ibkr import connect_ibkr_client`` and
#: ``from ...workflow_state import ExecutionLeaseManager``, because those live in
#: modules the application layer is allowed to name.  Both modules carry far more
#: than the one value each seam needs:
#:
#: * ``us_quant.ibkr`` also defines ``connect_ibkr_client`` / ``probe_ibkr_socket``
#:   (socket/thread I/O) and ``IBKRClientConnectError``;
#: * ``runtime/workflow_state.py`` also defines ``ExecutionLeaseManager`` /
#:   ``validate_paper_transition`` / ``WorkflowSnapshot`` / ``WorkflowStateError``
#:   / ``ExecutionLease``.
#:
#: So the direction rule is paired with a symbol rule: the package allowlist says
#: which module may be reached, and this table says which names inside it are
#: actually part of the seam.  Anything else is a violation, which is what keeps
#: an "exception" from becoming a whole open door.
#:
#: Keyed ``(module, symbol) -> the exact importers allowed to use it``.  The
#: importer set is enumerated rather than wildcarded for the same reason: a new
#: application module reaching for ``IBKRConnectionConfig`` should be a decision
#: someone makes on purpose, not one that arrives with a copy-paste.
NARROW_APPLICATION_EXCEPTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("us_quant.ibkr", "IBKRConnectionConfig"): frozenset(
        {"us_quant.trading.application.accounts"}
    ),
    ("us_quant.trading.runtime.workflow_state", "PaperWorkflowPhase"): frozenset(
        {
            "us_quant.trading.application.paper.contracts",
            "us_quant.trading.application.paper.service",
        }
    ),
}

#: Modules whose *whole* surface the application layer may reach, if any.  Empty
#: on purpose: every application-side exception is symbol-scoped.
APPLICATION_MODULES_WITH_NO_SYMBOL_SCOPE: frozenset[str] = frozenset()


def test_fa4c_the_application_layer_exceptions_are_symbol_scoped() -> None:
    """FA4c: the two historical seams are locked to exact symbols, not modules.

    FA1-FA4 answer "may this layer name that package".  This answers the
    narrower and more useful question: given that it may, *which names* may it
    take?  Without it, an allowlisted package is an open door -- the application
    layer could reach ``connect_ibkr_client`` (socket I/O) or
    ``ExecutionLeaseManager`` (the lease machinery the window and runtime own)
    and every structural guard would stay green.

    Reported per edge with the offending symbol, so the failure says what was
    reached for rather than just that a rule broke.

    The target is resolved through :func:`_resolved_import_from_module`, so
    ``from us_quant.ibkr import ...`` and ``from ...ibkr import ...`` are the
    same edge.  Comparing the raw ``node.module`` would have made the relative
    spelling invisible to this rule -- and would have let it *appear* caught by
    tripping the bookkeeping assertion below instead of the symbol check, which
    is a false pass rather than a detection.
    """

    application = _SRC / "trading" / "application"
    allowed = NARROW_APPLICATION_EXCEPTIONS

    offending: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in sorted(application.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolved_import_from_module(path, node)
            if target is None:
                continue
            # Only the two exception packages are symbol-scoped here; the
            # direction rule already covers everything else.
            if not any(target == pkg for pkg, _ in allowed):
                continue
            for alias in node.names:
                seen.add((target, alias.name))
                permitted = allowed.get((target, alias.name))
                if permitted is None:
                    offending.append(
                        f"{module}:{node.lineno}: {target}.{alias.name} "
                        "(no symbol-level exception)"
                    )
                elif module not in permitted:
                    offending.append(
                        f"{module}:{node.lineno}: {target}.{alias.name} "
                        f"(only {sorted(permitted)} may use it)"
                    )

    assert offending == [], offending

    # The guard must be checking something real: both exceptions are in use, and
    # no exception package was granted whole-module access.  This is a *coverage*
    # check, deliberately after the violation check so a violation is always
    # reported as a violation rather than as missing coverage.
    assert seen == set(allowed), seen
    for module, _symbol in allowed:
        assert module not in APPLICATION_MODULES_WITH_NO_SYMBOL_SCOPE

    # And the names this exists to keep out are genuinely present in those
    # modules, so the rule is not vacuous.
    ibkr_symbols = {
        node.name
        for node in ast.walk(
            ast.parse((_SRC / "ibkr.py").read_text(encoding="utf-8"))
        )
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert {"connect_ibkr_client", "probe_ibkr_socket"} <= ibkr_symbols

    workflow_symbols = {
        node.name
        for node in ast.walk(
            ast.parse(
                (
                    _SRC / "trading" / "runtime" / "workflow_state.py"
                ).read_text(encoding="utf-8")
            )
        )
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert {
        "ExecutionLeaseManager",
        "validate_paper_transition",
    } <= workflow_symbols


def test_fa4d_no_application_module_imports_a_bare_provider_module() -> None:
    """FA4d: the whole-module forms are the same open door as a bad symbol.

    ``from us_quant.ibkr import IBKRConnectionConfig`` is symbol-scoped;
    ``import us_quant.ibkr`` hands the whole module over and lets the caller
    reach ``connect_ibkr_client`` through attribute access, which no symbol
    check on the import statement can see.  A star import is the same thing
    spelled differently.

    Both the ``import`` form and the ``ImportFrom`` form are resolved through
    :func:`_resolved_import_from_module`, so ``from ...ibkr import *`` cannot
    slip past on a relative spelling while ``from us_quant.ibkr import *`` is
    caught.  The bare-``import`` half also covers the relative spelling that
    Python actually allows there (``from . import x`` never names the provider,
    and a plain ``import`` statement is always absolute in Python 3).
    """

    application = _SRC / "trading" / "application"
    provider_modules = ("us_quant.ibkr",)
    offending: list[str] = []

    for path in sorted(application.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in provider_modules:
                        offending.append(f"{module}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                target = _resolved_import_from_module(path, node)
                if target not in provider_modules:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        offending.append(
                            f"{module}:{node.lineno}: star import of {target}"
                        )
    assert offending == [], offending

    # The resolver really does fold the relative spelling onto the absolute one,
    # so this guard is not passing because a relative star import is invisible.
    accounts = application / "accounts.py"
    synthetic = ast.ImportFrom(
        module="ibkr",
        names=[ast.alias(name="*")],
        level=3,
    )
    assert _resolved_import_from_module(accounts, synthetic) == "us_quant.ibkr"


def test_fa1b_the_domain_layer_is_not_empty() -> None:
    """A guard that passes because the layer vanished is not a guard."""

    names = {_module_name(path) for path in _layer_modules("trading/domain")}
    assert "us_quant.trading.domain.strategy" in names
    assert "us_quant.trading.domain.orders" in names


def test_fa5_composition_is_where_the_concrete_adapters_are_assembled() -> None:
    """FA5: exactly one layer knows both an application and its concrete adapter.

    Stated as a property of the composition layer rather than as "IBKR appears
    somewhere": the concrete names belong in ``adapters`` and ``composition``,
    so a text scan over the tree would be both over- and under-sensitive.
    """

    composition = _layer_modules("trading/composition")
    assert composition, "the composition layer must exist"

    adapters = "us_quant.trading.adapters"
    assembled = [
        path.name
        for path in composition
        if any(
            target == adapters or target.startswith(adapters + ".")
            for target in _imported_modules(path)
        )
    ]
    assert assembled, "composition must assemble at least one concrete adapter"

    # And the layers below it must not, which is the half that matters.
    for layer in (
        "trading/domain",
        "trading/ports",
        "trading/application",
        "trading/runtime",
    ):
        assert _violations(layer, (adapters,)) == [], layer


def test_fa6_capability_orchestrators_do_not_import_each_other() -> None:
    """FA6: cross-capability composition happens only in the composition root.

    An ``ExecutionOrchestrator`` that imported ``PaperOrchestrator`` would have
    taken a job that belongs to the window, and the capability boundary would
    stop being checkable.
    """

    orchestration = _SRC / "desktop_v2" / "orchestration"
    capabilities = sorted(
        path.name
        for path in orchestration.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    )
    assert capabilities, "the orchestration package must have capabilities"

    prefix = "us_quant.desktop_v2.orchestration."
    offending: list[str] = []
    for capability in capabilities:
        for path in _layer_modules(f"desktop_v2/orchestration/{capability}"):
            source = _module_name(path)
            for target in sorted(_imported_modules(path)):
                if not target.startswith(prefix):
                    continue
                other = target[len(prefix):].split(".")[0]
                if other in capabilities and other != capability:
                    offending.append(f"{source} -> {target}")
    assert offending == []


# =====================================================================
# 2. Risk -> Execution path
# =====================================================================


def test_fa7_trade_proposal_carries_nothing_submittable() -> None:
    """FA7: a proposal is an intent, so it must have no order identity.

    Read off ``dataclasses.fields`` rather than the source text: the class
    docstring already says which fields are forbidden, and a comment is not a
    guard.
    """

    from us_quant.trading.domain.strategy import TradeProposal

    assert is_dataclass(TradeProposal)
    present = {field.name for field in fields(TradeProposal)}

    for forbidden in (
        "order_id",
        "broker_order_id",
        "client_order_id",
        "tif",
        "outsideRth",
        "transmit",
        "risk_approved",
    ):
        assert forbidden not in present, forbidden

    # And it really is the proposal type, so the guard cannot pass by the class
    # having been emptied.
    assert {"symbol", "action", "desired_quantity", "reference_price"} <= present


def test_fa8_dispatch_is_the_only_runtime_risk_execution_crossing_point() -> None:
    """FA8: the runtime reaches risk and execution from exactly one place.

    Two facts, both structural.  Only ``dispatch.py`` may call
    ``RiskApplication.evaluate`` and ``ExecutionApplication.submit_approved``;
    and the session must submit *through* its dispatch rather than calling the
    execution service itself.  The session is allowed to hold the references --
    it is the composition point for the two authorities -- but the trade path
    goes through ``self.dispatch``.
    """

    runtime = _SRC / "trading" / "runtime"
    dispatch = runtime / "dispatch.py"

    callers: list[str] = []
    for path in _layer_modules("trading/runtime"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver = node.func.value
            name = (
                receiver.id
                if isinstance(receiver, ast.Name)
                else ast.unparse(receiver)
            )
            if node.func.attr == "submit_approved" or (
                node.func.attr == "evaluate" and name.endswith("risk")
            ):
                callers.append(f"{path.name}:{node.lineno}:{name}.{node.func.attr}")

    assert callers, "the crossing point must still exist"
    assert {entry.split(":")[0] for entry in callers} == {dispatch.name}, callers

    # The session submits through the dispatch, never the service directly.
    #
    # Checked over the whole runtime package rather than just ``trading.py``: a
    # call on the injected execution service is the thing being forbidden, and
    # it does not matter which runtime module makes it.
    direct: list[str] = []
    for path in _layer_modules("trading/runtime"):
        if path.name == dispatch.name:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = ast.unparse(node.func.value)
                if node.func.attr == "submit_approved" or (
                    receiver.endswith("execution") and node.func.attr == "submit"
                ):
                    direct.append(f"{path.name}:{node.lineno}:{receiver}.{node.func.attr}")
    assert direct == [], direct


def test_fa8b_the_dispatch_never_submits_an_unapproved_verdict() -> None:
    """FA8b: the dispatch's own gate, asserted as behaviour.

    FA8 proves *where* the crossing point is.  This proves what it does there:
    a verdict risk did not approve cannot reach the execution service, and a
    proposal risk approved is submitted with the approved quantity rather than
    the requested one.  A dispatch that skipped its own verdict check and passed
    the decision straight through would still satisfy every structural guard.
    """

    from us_quant.trading.application.risk import RiskApplication
    from us_quant.trading.domain.risk import (
        LayeredRiskLimits,
        RiskDecision,
        RiskLimits,
    )
    from us_quant.trading.domain.strategy import TradeAction, TradeProposal
    from us_quant.trading.runtime.config import TradingSessionConfig
    from us_quant.trading.runtime.dispatch import OrderDispatch
    from us_quant.trading.runtime.portfolio import SessionBook

    submitted: list = []

    class _Execution:
        def submit_approved(self, *, proposal, decision, **kwargs):
            from us_quant.trading.domain.orders import OrderIntent, Side

            submitted.append(decision)
            intent = OrderIntent.create(
                session_id=kwargs.get("session_id", "s-1"),
                strategy_version_id=proposal.strategy.version_id,
                signal_symbol=proposal.symbol,
                execution_symbol=kwargs.get("execution_symbol", proposal.symbol),
                side=Side.BUY,
                quantity=decision.approved_quantity,
                limit_price=proposal.reference_price,
                reason=kwargs.get("reason", ""),
            )

            class _Result:
                pass

            result = _Result()
            result.intent = intent
            result.broker_order_id = 1
            return result

    class _Risk:
        """A risk authority whose verdict is fixed for the test."""

        def __init__(self, decision: RiskDecision) -> None:
            self._decision = decision
            self.exposure_multiplier = Decimal("1")
            self.session_overrides = type(
                "_Overrides",
                (),
                {
                    "entry_start": None,
                    "last_entry": None,
                    "maximum_trades_per_day": None,
                    "daily_loss_limit": None,
                },
            )()

        def evaluate(self, **kwargs) -> RiskDecision:
            return self._decision

    proposal = TradeProposal(
        strategy=StrategyIdentity(
            strategy_id="intraday-auto-rotation",
            version_id="v-1",
            parameter_hash="h",
        ),
        symbol="AAPL",
        action=TradeAction.BUY,
        desired_quantity=10,
        reference_price=Decimal("100"),
        reason="fac fixture",
        generated_at=datetime.now(timezone.utc),
    )
    book = SessionBook(initial_cash=Decimal("10000"), commission=Decimal("0.35"))
    config = TradingSessionConfig(
        initial_cash=Decimal("10000"), capital_source="fac fixture"
    )

    def _dispatch(decision: RiskDecision) -> tuple:
        submitted.clear()
        dispatch = OrderDispatch(
            config=config,
            risk=_Risk(decision),  # type: ignore[arg-type]
            execution=_Execution(),  # type: ignore[arg-type]
        )
        outcome = dispatch.enter(
            now=datetime.now(timezone.utc),
            proposal=proposal,
            book=book,
            session_id="s-1",
            allowed_symbols=frozenset({"AAPL"}),
        )
        return outcome, list(submitted)

    # A refusal is reported as a blockage and never reaches execution.
    rejected, reached = _dispatch(RiskDecision.reject("fac fixture"))
    assert reached == []
    assert rejected.submitted is False
    assert rejected.blockage

    # A *forged* rejection -- ``approved=False`` carrying a positive quantity --
    # must be blocked too.  The public API cannot build this one (a rejection
    # approves zero shares), so it can only arrive from a caller that bypassed
    # the domain; the notional check below would not catch it either, which is
    # exactly why the approved flag has to be the gate.
    forged = object.__new__(RiskDecision)
    for name, value in {
        "approved": False,
        "requested_quantity": 10,
        "approved_quantity": 10,
        "reasons": ("fac fixture",),
        "adjustments": (),
    }.items():
        object.__setattr__(forged, name, value)
    forged_outcome, reached = _dispatch(forged)
    assert reached == []
    assert forged_outcome.submitted is False
    assert forged_outcome.blockage

    # An approval is submitted at the *approved* quantity, not the requested one.
    approved, reached = _dispatch(
        RiskDecision.approve(
            requested_quantity=10,
            approved_quantity=4,
            adjustments=("cash cap",),
        )
    )
    assert len(reached) == 1
    assert reached[0].approved is True
    assert reached[0].approved_quantity == 4
    assert approved.submitted is True

    # A trimmed approval still goes through, which is what makes the guard above
    # meaningful rather than a blanket "nothing is ever submitted".
    full, reached = _dispatch(RiskDecision.approve(requested_quantity=10))
    assert len(reached) == 1 and full.submitted is True


def test_fa9_execution_application_fails_closed_on_a_bad_verdict() -> None:
    """FA9: the execution service re-checks the verdict; it does not trust it.

    This is the second line of defence.  It must survive even though the
    dispatch already checks ``approved``, because the dispatch is a caller and a
    caller can be wrong.

    The cases are built with ``object.__new__`` + ``object.__setattr__`` on
    purpose.  ``RiskDecision.__post_init__`` already refuses two of them at
    construction -- an approved-but-zero verdict and an oversized one -- so the
    only way to reach ``ExecutionApplication`` with them is to bypass the
    domain's own validation.  That is exactly the threat this layer guards
    against: a caller that constructs, mocks or deserialises a verdict without
    going through ``RiskDecision.approve``.
    """

    from us_quant.trading.application.execution import ExecutionApplication
    from us_quant.trading.domain.risk import RiskDecision
    from us_quant.trading.domain.strategy import TradeAction, TradeProposal
    from us_quant.trading.ports.broker_execution import ExecutionRefused

    def _forge(**overrides) -> RiskDecision:
        """A verdict that never passed the domain's own consistency check."""

        decision = object.__new__(RiskDecision)
        for name, value in {
            "approved": True,
            "requested_quantity": 10,
            "approved_quantity": 10,
            "reasons": (),
            "adjustments": (),
            **overrides,
        }.items():
            object.__setattr__(decision, name, value)
        return decision

    class _Repository:
        def intent(self, order_id: str):  # pragma: no cover
            return None

        def broker_order_id(self, order_id: str):  # pragma: no cover
            return None

        def intent_for_idempotency_key(self, key: str):  # pragma: no cover
            return None

        def record_intent(self, *args, **kwargs) -> None:  # pragma: no cover
            raise AssertionError("a refused verdict must not be made durable")

    class _Broker:
        def reserve(self, intent):  # pragma: no cover
            raise AssertionError("a refused verdict must not reach the broker")

        def submit(self, reservation):  # pragma: no cover
            raise AssertionError("a refused verdict must not reach the broker")

    application = ExecutionApplication(
        repository=_Repository(),  # type: ignore[arg-type]
        broker=_Broker(),  # type: ignore[arg-type]
    )
    proposal = TradeProposal(
        strategy=StrategyIdentity(
            strategy_id="intraday-auto-rotation",
            version_id="v-1",
            parameter_hash="h",
        ),
        symbol="AAPL",
        action=TradeAction.BUY,
        desired_quantity=10,
        reference_price=Decimal("100"),
        reason="fac fixture",
        generated_at=datetime.now(timezone.utc),
    )

    cases = {
        # ``approved=False`` with a positive quantity: the domain cannot build
        # this (a rejection approves zero shares) and the quantity checks below
        # would not catch it, so the approved flag is the only gate that can.
        "not approved": _forge(approved=False, approved_quantity=10),
        "zero approved quantity": _forge(approved_quantity=0),
        "oversized approval": _forge(approved_quantity=99),
        "verdict from another request": _forge(requested_quantity=7),
    }
    for label, decision in cases.items():
        with pytest.raises(ExecutionRefused):
            application.submit_approved(
                proposal=proposal,
                decision=decision,
                execution_symbol="AAPL",
                session_id="s-1",
                reason="fac fixture",
            )

    # The two forged verdicts the domain itself refuses really are unconstructible
    # through the public API, so the guard above is the *only* thing standing
    # between them and the broker.
    with pytest.raises(ValueError):
        RiskDecision.approve(requested_quantity=10, approved_quantity=0)
    with pytest.raises(ValueError):
        RiskDecision.approve(requested_quantity=10, approved_quantity=99)


def test_fa10_the_durable_write_precedes_the_broker_submit() -> None:
    """FA10: reserve -> record_intent -> submit, asserted as an ordered trace.

    A behaviour test rather than a string-order check: the fakes share one log,
    so the sequence is observed rather than inferred from line numbers.
    """

    from us_quant.trading.application.execution import ExecutionApplication
    from us_quant.trading.domain.orders import OrderStatus
    from us_quant.trading.domain.risk import RiskDecision
    from us_quant.trading.domain.strategy import TradeAction, TradeProposal

    trace: list[str] = []

    class _Repository:
        def __init__(self) -> None:
            self.intents: dict = {}

        def intent(self, order_id: str):
            return self.intents.get(order_id)

        def broker_order_id(self, order_id: str):
            return 1

        def intent_for_idempotency_key(self, key: str):
            return None

        def record_intent(self, intent, *, broker_order_id, account_alias) -> None:
            trace.append("record_intent")
            self.intents[intent.order_id] = intent

        def status(self, order_id: str):
            return OrderStatus.SUBMITTING

    class _Broker:
        def reserve(self, intent):
            trace.append("reserve")

            class _Reservation:
                broker_order_id = 1
                account_alias = "paper"

            return _Reservation()

        def submit(self, reservation) -> None:
            trace.append("submit")

    repository = _Repository()
    application = ExecutionApplication(
        repository=repository,  # type: ignore[arg-type]
        broker=_Broker(),  # type: ignore[arg-type]
    )
    application.submit_approved(
        proposal=TradeProposal(
            strategy=StrategyIdentity(
                strategy_id="intraday-auto-rotation",
                version_id="v-1",
                parameter_hash="h",
            ),
            symbol="AAPL",
            action=TradeAction.BUY,
            desired_quantity=10,
            reference_price=Decimal("100"),
            reason="fac fixture",
            generated_at=datetime.now(timezone.utc),
        ),
        decision=RiskDecision.approve(requested_quantity=10),
        execution_symbol="AAPL",
        session_id="s-1",
        reason="fac fixture",
    )

    assert trace == ["reserve", "record_intent", "submit"]


# =====================================================================
# 3. Workflow exclusivity
# =====================================================================


def test_fa11_paper_and_shadow_share_one_lease_manager() -> None:
    """FA11: SHADOW XOR PAPER is structural, not a UI convention.

    ``WorkflowController`` builds exactly one ``ExecutionLeaseManager`` and hands
    the *same* instance to both controllers.  Two managers would make the mutual
    exclusion a coincidence of two independent booleans.
    """

    from us_quant.desktop_v2.workflows import WorkflowController

    controller = WorkflowController()

    assert controller.shadow._leases is controller.paper._leases

    # And the controller constructs exactly one, so the sharing cannot be an
    # accident of two managers happening to agree.
    tree = ast.parse(
        (_SRC / "desktop_v2" / "workflows.py").read_text(encoding="utf-8")
    )
    constructions = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ExecutionLeaseManager"
    ]
    assert len(constructions) == 1, constructions


def test_fa12_and_fa13_the_two_workflows_exclude_each_other() -> None:
    """FA12/FA13: whichever workflow holds the lease refuses the other.

    Shadow's ``start`` acquires the SHADOW lease; Paper acquires PAPER through
    ``begin_connecting``, which is the step that happens *before* the broker
    connect begins.  Both are driven through their real public surface rather
    than by poking the shared manager, because the point is that the two
    workflows cannot both be live.
    """

    from us_quant.auto_launch import build_auto_launch_plan
    from us_quant.desktop_v2.workflows import WorkflowController
    from us_quant.trading.runtime.workflow_state import (
        ExecutionLease,
        WorkflowStateError,
    )

    plan = build_auto_launch_plan(
        attempt_id=1,
        strategy_version_id="v-1",
        parameter_hash="h",
        candidate_symbols=("AAPL",),
        requested_capital_limit=Decimal("10000"),
    )

    shadow_first = WorkflowController()
    shadow_first.shadow.start()
    assert shadow_first.paper.lease is ExecutionLease.SHADOW

    shadow_first.paper.begin_preparing()
    shadow_first.paper.mark_ready()
    with pytest.raises(WorkflowStateError):
        shadow_first.paper.begin_connecting(plan)
    shadow_first.shadow.stop()

    paper_first = WorkflowController()
    paper_first.paper.begin_preparing()
    paper_first.paper.mark_ready()
    paper_first.paper.begin_connecting(plan)
    assert paper_first.paper.lease is ExecutionLease.PAPER

    with pytest.raises(WorkflowStateError):
        paper_first.shadow.start()


def test_fa14_the_paper_lease_cannot_be_released_before_finalization() -> None:
    """FA14: release must be proved safe before the ownership is handed back."""

    from us_quant.trading.runtime.workflow_state import (
        ExecutionLease,
        ExecutionLeaseManager,
        WorkflowStateError,
    )

    manager = ExecutionLeaseManager()
    manager.acquire_paper()

    with pytest.raises(WorkflowStateError):
        manager.release_paper(finalized=False)

    # The refusal leaves the lease exactly where it was.
    assert manager.lease is ExecutionLease.PAPER

    manager.release_paper(finalized=True)
    assert manager.lease is ExecutionLease.NONE


# =====================================================================
# 4. Paper lifecycle / recovery
# =====================================================================


def test_fa15_a_halted_session_has_no_automatic_route_back_to_running() -> None:
    """FA15: HALTED is sticky; every exit is an explicit human action."""

    from us_quant.trading.runtime.workflow_state import (
        PaperWorkflowPhase,
        WorkflowStateError,
        validate_paper_transition,
    )

    for target in PaperWorkflowPhase:
        if target in {
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.FINALIZED,
        }:
            continue
        with pytest.raises(WorkflowStateError):
            validate_paper_transition(PaperWorkflowPhase.HALTED, target)

    # The two legal exits still need the explicit flag, so "HALTED -> RECONCILING"
    # cannot be taken by an ordinary transition either.
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.HALTED, PaperWorkflowPhase.RECONCILING
        )


def test_fa16_manual_reconciliation_is_an_explicit_two_step_route() -> None:
    """FA16: HALTED -> RECONCILING -> RECONCILING_READY -> RUNNING, confirmed.

    The recovery half of this is exercised on a real controller by
    ``tests/test_desktop_paper_recovery_finalization_orchestrator.py``.  What is
    asserted here is the *transition* half that makes that route the only one:
    each step requires ``explicit_reconciliation``, and RECONCILING_READY cannot
    return to RUNNING without it.
    """

    from us_quant.trading.runtime.workflow_state import (
        PaperWorkflowPhase,
        WorkflowStateError,
        validate_paper_transition,
    )

    validate_paper_transition(
        PaperWorkflowPhase.HALTED,
        PaperWorkflowPhase.RECONCILING,
        explicit_reconciliation=True,
    )
    validate_paper_transition(
        PaperWorkflowPhase.RECONCILING_READY,
        PaperWorkflowPhase.RUNNING,
        explicit_reconciliation=True,
    )

    # Without the flag the same two steps are refused.
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.RECONCILING_READY, PaperWorkflowPhase.RUNNING
        )

    # And RECONCILING cannot jump straight back to RUNNING even with the flag:
    # evidence has to be published first.
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.RUNNING,
            explicit_reconciliation=True,
        )


def test_fa17_and_fa18_only_the_paper_capability_reads_the_workflow_phase() -> None:
    """FA17/FA18: neither the window nor the execution package reads the phase.

    The phase is the Paper capability's own lifecycle fact.  A window or an
    execution route branching on it is the route-specific orchestration the
    Composition Closure removed.

    Imports are checked as well as uses: ``from ... import PaperWorkflowPhase``
    is an ``ast.alias``, not a ``Name``, so a guard that only looked for the
    name would miss the import that makes the branch possible.
    """

    for label, root in (
        ("window", _SRC / "desktop.py"),
        ("execution package", _SRC / "desktop_v2" / "orchestration" / "execution"),
    ):
        paths = [root] if root.is_file() else _layer_modules(
            root.relative_to(_SRC).as_posix()
        )
        offending: list[str] = []
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    # Resolved, not a substring of the raw ``node.module``: the
                    # shared resolver is the one place relative spelling is
                    # folded onto the absolute module, so this guard cannot end
                    # up with its own third reading of what an import names.
                    target = _resolved_import_from_module(path, node) or ""
                    if "workflow_state" in target:
                        offending.append(f"{path.name}:{node.lineno}:import")
                    for alias in node.names:
                        if alias.name == "PaperWorkflowPhase":
                            offending.append(
                                f"{path.name}:{node.lineno}:import {alias.name}"
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "workflow_state" in alias.name:
                            offending.append(f"{path.name}:{node.lineno}:import")
                elif isinstance(node, ast.Name) and node.id == "PaperWorkflowPhase":
                    offending.append(f"{path.name}:{node.lineno}:use")
                elif isinstance(node, ast.Attribute) and node.attr == "phase":
                    receiver = ast.unparse(node.value)
                    if "workflow" in receiver:
                        offending.append(
                            f"{path.name}:{node.lineno}:{receiver}.phase"
                        )
        assert offending == [], (label, offending)


# =====================================================================
# 5. Broker abstraction
# =====================================================================


def test_fa19_risk_and_execution_applications_name_no_concrete_broker() -> None:
    """FA19: the business path depends on the port, never the adapter."""

    for relative in ("trading/application/risk.py", "trading/application/execution.py"):
        offending = _violations(
            relative,
            (
                "us_quant.trading.adapters",
                "us_quant.trading.composition",
                "ibapi",
                "sqlite3",
            ),
        )
        assert not offending, (relative, offending)


def test_fa20_the_execution_builder_accepts_the_port_abstraction() -> None:
    """FA20: the seam a future Live adapter reuses is the port, not a branch.

    ``build_execution_application`` takes a ``BrokerExecutionPort`` and an
    ``OrderRepositoryPort``.  That is what lets a Live adapter be a different
    adapter behind the same seam instead of a second risk/execution stack.

    The "no mode branch" half of this property lives in FA19c, which checks the
    constructors' **AST**.  An earlier version of this test scanned their source
    *text* for the words "paper"/"live"/"IBKR" -- which a docstring could trip
    (``RiskApplication.__init__`` contains "lives here") and which could never
    have caught a branch that used a variable.  The AST check replaced it.
    """

    import inspect

    from us_quant.trading.composition.execution import (
        build_execution_application,
    )
    from us_quant.trading.ports.broker_execution import BrokerExecutionPort
    from us_quant.trading.ports.order_repository import OrderRepositoryPort

    # ``from __future__ import annotations`` makes the annotations strings, so
    # they are resolved rather than compared as text.
    hints = inspect.get_annotations(build_execution_application, eval_str=True)
    assert hints.get("broker") is BrokerExecutionPort
    assert hints.get("repository") is OrderRepositoryPort


# =====================================================================
# 7. Desktop residual closure
# =====================================================================


def test_fa25_the_window_still_holds_no_capability_truth() -> None:
    """FA25: the Composition Closure residuals are still closed.

    A re-check of the *high-level* invariant, not a re-run of G1/G2.  The
    window is the composition root: it may hold orchestrators and bridges, and
    it must not hold a second copy of a capability's own state.
    """

    source = (_SRC / "desktop.py").read_text(encoding="utf-8")
    names = {
        node.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Name)
    }

    for forbidden in (
        "PaperWorkflowPhase",
        "ExecutionLease",
        "PaperSessionCoordinator",
        "StrategyApplication",
        "SQLiteOrderRepository",
        "IBKRExecutionAdapter",
    ):
        assert forbidden not in names, forbidden

    # The worker collection is the controller's, not a second alias on the window.
    window_attributes = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert "workers" not in window_attributes
    assert "task_controller" in window_attributes

    # And it really is the composition root, so the guard cannot pass by the
    # window having stopped composing anything.
    for required in (
        "ExecutionOrchestrator",
        "PaperOrchestrator",
        "ShadowOrchestrator",
        "MarketOrchestrator",
    ):
        assert required in names, required


def test_fa25b_no_aggregate_manager_or_event_bus_was_introduced() -> None:
    """FA25b: the closure must not be undone by a new global coordinator."""

    banned = (
        "DesktopCoordinator",
        "DesktopManager",
        "GlobalOrchestrator",
        "EventBus",
    )
    offending: list[str] = []
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        names = {
            node.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ClassDef)
        }
        for name in banned:
            if name in names:
                offending.append(f"{_module_name(path)}::{name}")
    assert offending == []


def test_fa25c_the_execution_package_owns_no_workflow_phase_branch() -> None:
    """FA25c: the execution route sequences; it does not own Paper's lifecycle."""

    execution = _SRC / "desktop_v2" / "orchestration" / "execution"
    offending: list[str] = []
    for path in _layer_modules("desktop_v2/orchestration/execution"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _resolved_import_from_module(path, node) or ""
                if "workflow_state" in target:
                    offending.append(f"{path.name}:{node.lineno}")
    assert offending == []


# =====================================================================
# Future boundaries -- recorded, not implemented
# =====================================================================


def test_fa19b_a_future_live_path_must_reuse_the_single_authority_stack() -> None:
    """FA19b: the durable Live invariant is *no second authority*, not "no Live".

    An earlier version of this guard asserted that no ``live*.py`` file existed.
    That was the wrong shape twice over: it is **future-hostile** (a Live adapter
    is a route goal, and naming it ``trading/adapters/ibkr/live_execution.py``
    would have turned this suite red for doing the right thing) and it is
    **unreliable** (naming the same file ``ibkr_production.py`` would walk
    straight past it).  A guard on a filename is not an architecture guard.

    What is durable is the invariant the Live work actually has to respect:
    there is exactly **one** risk authority, **one** execution authority and
    **one** trading runtime, and a future Live path adds an *adapter* behind
    ``BrokerExecutionPort`` -- it does not fork the deterministic
    ``Risk → Execution`` stack into a parallel one.

    So this asserts class names, not file names: any class whose name *contains*
    an authority name must be the canonical one.  ``IBKRExecutionAdapter`` and a
    future ``IBKRLiveExecutionAdapter`` are untouched by that rule -- they
    implement the port, which is exactly the extension point this phase locked
    (FA20).  Adding a Live adapter keeps this green; adding a
    ``LiveRiskApplication`` -- or a ``RiskApplicationV2`` that forks the same
    job under a different spelling -- does not.
    """

    canonical = {
        "RiskApplication": "us_quant.trading.application.risk",
        "ExecutionApplication": "us_quant.trading.application.execution",
        "TradingRuntime": "us_quant.trading.runtime.trading",
    }

    definitions: dict[str, list[str]] = {name: [] for name in canonical}
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for authority in canonical:
                # ``in`` rather than ``endswith``: a fork that renames itself
                # ``RiskApplicationV2`` or wraps itself as
                # ``LiveRiskApplication`` is the same second authority.  No
                # non-canonical class in the tree contains an authority name
                # today, so this is strictly stronger with no false positive.
                if authority in node.name:
                    definitions[authority].append(f"{module}::{node.name}")

    for authority, owner in canonical.items():
        found = definitions[authority]
        # Exactly one definition in the whole tree -- the canonical owner.
        assert found == [f"{owner}::{authority}"], (authority, found)

    # The extension point is the port, and it is the only one: the Live work
    # reuses this seam rather than inventing a parallel authority surface.
    from us_quant.trading.ports.broker_execution import BrokerExecutionPort

    assert isinstance(BrokerExecutionPort, type)

    # `Environment.LIVE` stays a domain vocabulary value, so a future Live path
    # is described by configuration rather than by a second set of authorities.
    from us_quant.trading.domain.common import Environment

    assert Environment.LIVE == "live"


def test_fa19c_paper_and_live_differences_are_not_a_mode_branch() -> None:
    """FA19c: Paper/Live must differ by adapter / config / permission.

    The counterpart to FA19b.  If the difference were expressed inside the
    authority stack, a Live path would need a second stack -- so the authority
    constructors must take ports and policy, and must not branch on a mode.

    Checked over the constructor's **AST**, not its source text: a docstring that
    happens to contain the word "lives" is not a mode branch, and a text scan
    cannot tell the two apart.  What is forbidden is a *comparison or attribute
    access* on a mode word, and a branch whose body differs by mode.
    """

    import inspect
    import textwrap

    from us_quant.trading.application.execution import ExecutionApplication
    from us_quant.trading.application.risk import RiskApplication

    mode_words = {"paper", "live", "ibkr", "alpaca", "production"}

    for authority in (RiskApplication, ExecutionApplication):
        # ``getsource`` returns the method at its class indentation, and the
        # docstring is dropped so prose cannot trip the guard.
        body = ast.parse(
            textwrap.dedent(inspect.getsource(authority.__init__))
        ).body[0]
        statements = body.body
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]

        offending: list[str] = []
        for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
            if isinstance(node, ast.Attribute) and node.attr.lower() in mode_words:
                offending.append(f"{authority.__name__}: attr {node.attr}")
            elif isinstance(node, ast.Name) and node.id.lower() in mode_words:
                offending.append(f"{authority.__name__}: name {node.id}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.lower() in mode_words:
                    offending.append(f"{authority.__name__}: literal {node.value!r}")
            elif isinstance(node, ast.Compare):
                for comparator in [node.left, *node.comparators]:
                    if (
                        isinstance(comparator, ast.Attribute)
                        and comparator.attr.lower() in mode_words
                    ):
                        offending.append(
                            f"{authority.__name__}: compare on {comparator.attr}"
                        )
        assert offending == [], offending

    # And the execution authority really does take exactly the two ports, so the
    # substitution point is the adapter rather than a mode flag.
    hints = inspect.get_annotations(
        ExecutionApplication.__init__, eval_str=True
    )
    from us_quant.trading.ports.broker_execution import BrokerExecutionPort
    from us_quant.trading.ports.order_repository import OrderRepositoryPort

    assert hints.get("broker") is BrokerExecutionPort
    assert hints.get("repository") is OrderRepositoryPort

