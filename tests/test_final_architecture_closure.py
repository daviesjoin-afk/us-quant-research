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
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import pathlib
import sys
from types import MappingProxyType

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


def test_fa26b_a_copy_of_a_governed_version_is_still_frozen() -> None:
    """FA26b: immutability must survive a copy, or it lasts until someone copies.

    ``strategy_launch_fact`` takes a ``copy.deepcopy`` of the parameters before
    freezing them into a launch request.  If deepcopy handed back a plain dict,
    the "frozen" request would be mutable again and the split-identity failure
    the copy exists to prevent would come straight back.
    """

    import copy
    import pickle

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


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module this file imports, resolved through ``ast``.

    Relative imports are resolved against the file's own package, so a
    ``from ..runtime import x`` cannot slip past a prefix rule.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path)
    if path.name != "__init__.py":
        package = package.rsplit(".", 1)[0]

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                up = node.level - 1
                base = ".".join(parts[: len(parts) - up]) if up else package
                found.add(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                found.add(node.module)
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
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "workflow_state" in node.module:
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

    # The application itself is neutral: it takes the ports and has no
    # paper/live branch, because the difference belongs to the adapter.
    from us_quant.trading.application.execution import ExecutionApplication

    source = inspect.getsource(ExecutionApplication.__init__)
    for forbidden in ("paper", "live", "IBKR", "ibkr"):
        assert forbidden not in source, forbidden


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
            if isinstance(node, ast.ImportFrom) and node.module:
                if "workflow_state" in node.module:
                    offending.append(f"{path.name}:{node.lineno}")
    assert offending == []


# =====================================================================
# Future boundaries -- recorded, not implemented
# =====================================================================


def test_fa19b_no_live_adapter_exists_yet() -> None:
    """The Future Live seam is a seam, not an implementation.

    This phase proves the current architecture *permits* a Live adapter behind
    the existing business path.  It does not add one, and a guard that stopped
    holding the moment someone added it would be the wrong guard -- so this test
    asserts the seam (FA20) and records that no Live adapter is wired into the
    trading core yet.
    """

    live_like = [
        path.name
        for path in _SRC.rglob("*.py")
        if "__pycache__" not in path.parts
        and path.stem.lower() in {"live", "live_execution", "live_broker"}
    ]
    assert live_like == [], live_like

    # The live environment exists only as a domain vocabulary value.
    from us_quant.trading.domain.common import Environment

    assert Environment.LIVE == "live"

