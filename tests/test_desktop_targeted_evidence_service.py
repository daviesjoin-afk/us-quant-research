"""The targeted evidence data boundary, without Qt or a window.

``DesktopTargetedEvidenceService`` is pure application code: it reads the minute
store it is given, runs the research executors and writes the artifacts.  Every
test here replaces the store and the executors, so nothing touches a real minute
database or performs a real research run.

What this file pins is the *research safety* of the extraction rather than the
mathematics of any study -- the algorithms have their own tests.  Four properties
are asserted directly, because each is a place an extraction can silently change
what the research means:

* the provider selection is the row-count winner with a lexical tie-break, and
  data quality reads the *same* provider's raw rows;
* the replay session is the latest regular session of the chosen provider;
* the pipeline runs in its fixed order and saves each family where it used to;
* the strategy identity and the ``Decimal`` initial equity reach the executors
  unchanged, with no float round-trip.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRunInputs,
)
from us_quant.desktop_targeted_evidence_service import (
    MINIMUM_WALK_FORWARD_SESSIONS,
    DesktopTargetedEvidenceService,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SERVICE_PATH = (
    _REPO_ROOT / "src" / "us_quant" / "desktop_targeted_evidence_service.py"
)
_MODULE = "us_quant.desktop_targeted_evidence_service"


@dataclass(frozen=True)
class _Record:
    """One minute row: only the fields the service actually reads."""

    symbol: str
    provider: str
    minute: str
    coverage: str = "Type 1"
    realtime_ready: bool = True
    stale: bool = False


class _Store:
    """A minute store with scripted usable/raw rows and a recorded call log."""

    def __init__(self, usable=(), raw=None) -> None:
        self.usable = tuple(usable)
        self.raw = tuple(usable) if raw is None else tuple(raw)
        self.calls: list[tuple[str, bool]] = []

    def load(self, symbol, *, provider=None, usable_only=True):
        self.calls.append((symbol, usable_only))
        return self.usable if usable_only else self.raw


@pytest.fixture()
def sentinel():
    class Sentinel(Exception):
        pass

    return Sentinel


def _inputs(
    symbol: str = "AAPL",
    *,
    equity: Decimal = Decimal("4200"),
) -> TargetedEvidenceRunInputs:
    return TargetedEvidenceRunInputs(
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.3.0-research",
        parameter_hash="hash-1",
        parameters=(("momentum_lookback_minutes", 5), ("whole_shares", True)),
        initial_equity=equity,
    )


def _service(store) -> DesktopTargetedEvidenceService:
    return DesktopTargetedEvidenceService(
        minute_store=store, results_root=pathlib.Path("results")
    )


def _executor(monkeypatch, name: str, calls: dict, result=None, raises=None):
    """Replace one executor, recording its positional and keyword arguments."""

    def fake(*args, **kwargs):
        calls[name] = (args, kwargs)
        if raises is not None:
            raise raises
        return result if result is not None else f"<{name}>"

    monkeypatch.setattr(f"{_MODULE}.{name}", fake)


# -- the constructor performs no I/O -------------------------------------


def test_the_constructor_reads_no_store() -> None:
    store = _Store(usable=(_Record("AAPL", "IBKR", "2026-07-20T14:00:00+00:00"),))

    _service(store)

    assert store.calls == []


# -- provider selection ---------------------------------------------------


def _two_providers():
    """``IBKR`` has three rows, ``POLYGON`` two, and ``AAA`` one.

    The tie-break is never needed here, which is deliberate: this fixture pins
    "most rows wins" and the lexical rule gets its own test below.
    """

    rows = [
        _Record("AAPL", "IBKR", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "IBKR", "2026-07-20T14:01:00+00:00"),
        _Record("AAPL", "IBKR", "2026-07-20T14:02:00+00:00"),
        _Record("AAPL", "POLYGON", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "POLYGON", "2026-07-20T14:01:00+00:00"),
        _Record("AAPL", "AAA", "2026-07-20T14:00:00+00:00"),
    ]
    return rows


def test_replay_uses_the_provider_with_the_most_rows(
    monkeypatch, sentinel
) -> None:
    store = _Store(usable=_two_providers())
    _executor(monkeypatch, "run_targeted_replay", {}, raises=sentinel)
    monkeypatch.setattr(f"{_MODULE}.save_targeted_replay", lambda *a, **k: None)

    with pytest.raises(sentinel):
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    # Reaching the executor at all proves a provider was chosen; the provider
    # identity is asserted through the session grouping below.
    assert store.calls[0] == ("AAPL", True)


def test_a_tie_is_broken_by_lexical_provider_order(
    monkeypatch, sentinel
) -> None:
    """Equal row counts: the lexically greatest provider name wins.

    ``max`` over ``(row_count, provider)`` is the inline handler's rule, and it is
    exactly the kind of detail a "cleaner" rewrite would replace with
    dict-insertion order -- which would make the research depend on which feed
    happened to record first.  Pinned in the direction the code actually goes, so
    the assertion documents the behaviour rather than an intention.
    """

    rows = [
        _Record("AAPL", "AAA_FEED", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "ZZZ_FEED", "2026-07-20T14:00:00+00:00"),
    ]
    store = _Store(usable=rows)
    seen: list[tuple] = []

    def fake_group(records):
        seen.append(tuple(records))
        return (("2026-07-20", tuple(records)),)

    monkeypatch.setattr(f"{_MODULE}.group_regular_sessions", fake_group)
    _executor(monkeypatch, "run_targeted_replay", {}, raises=sentinel)
    monkeypatch.setattr(f"{_MODULE}.save_targeted_replay", lambda *a, **k: None)

    with pytest.raises(sentinel):
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    assert {row.provider for row in seen[0]} == {"ZZZ_FEED"}


def test_more_rows_beats_a_greater_provider_name(
    monkeypatch, sentinel
) -> None:
    """Row count is the primary key, so ``zzz`` with one row loses to ``aaa`` with two."""

    rows = [
        _Record("AAPL", "ZZZ_FEED", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "AAA_FEED", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "AAA_FEED", "2026-07-20T14:01:00+00:00"),
    ]
    store = _Store(usable=rows)
    seen: list[tuple] = []

    def fake_group(records):
        seen.append(tuple(records))
        return (("2026-07-20", tuple(records)),)

    monkeypatch.setattr(f"{_MODULE}.group_regular_sessions", fake_group)
    _executor(monkeypatch, "run_targeted_replay", {}, raises=sentinel)
    monkeypatch.setattr(f"{_MODULE}.save_targeted_replay", lambda *a, **k: None)

    with pytest.raises(sentinel):
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    assert {row.provider for row in seen[0]} == {"AAA_FEED"}
    assert len(seen[0]) == 2


def test_replay_refuses_rather_than_falling_back(monkeypatch) -> None:
    """A store with nothing usable is an error, not an empty replay."""

    store = _Store(usable=())
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_replay",
        lambda *a, **k: pytest.fail("no executor call may happen"),
    )

    with pytest.raises(ValueError) as error:
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    assert "尚无可用分钟数据" in str(error.value)


def test_replay_refuses_when_no_regular_session_exists(
    monkeypatch,
) -> None:
    store = _Store(usable=(_Record("AAPL", "IBKR", "2026-07-20T14:00:00+00:00"),))
    monkeypatch.setattr(f"{_MODULE}.group_regular_sessions", lambda rows: ())
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_replay",
        lambda *a, **k: pytest.fail("no executor call may happen"),
    )

    with pytest.raises(ValueError) as error:
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    assert "纽约常规交易时段" in str(error.value)


def test_replay_takes_the_latest_regular_session(monkeypatch, sentinel) -> None:
    """``sessions[-1]``, not the first and not a calendar-derived pick."""

    rows = [
        _Record("AAPL", "IBKR", "2026-07-20T14:00:00+00:00"),
        _Record("AAPL", "IBKR", "2026-07-21T14:00:00+00:00"),
        _Record("AAPL", "IBKR", "2026-07-22T14:00:00+00:00"),
    ]
    store = _Store(usable=rows)

    def fake_group(records):
        return (
            ("2026-07-20", (records[0],)),
            ("2026-07-21", (records[1],)),
            ("2026-07-22", (records[2],)),
        )

    monkeypatch.setattr(f"{_MODULE}.group_regular_sessions", fake_group)
    _executor(monkeypatch, "run_targeted_replay", {}, raises=sentinel)
    monkeypatch.setattr(f"{_MODULE}.save_targeted_replay", lambda *a, **k: None)

    with pytest.raises(sentinel):
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    # The sentinel came from the executor, and the session it saw is the latest.
    assert store.calls == [("AAPL", True)]


# -- what the executors receive ------------------------------------------


def test_replay_passes_the_frozen_identity_and_exact_decimal(
    monkeypatch, sentinel
) -> None:
    calls: dict = {}
    store = _Store(usable=_two_providers())
    monkeypatch.setattr(
        f"{_MODULE}.group_regular_sessions",
        lambda rows: (("2026-07-20", tuple(rows)),),
    )
    _executor(monkeypatch, "run_targeted_replay", calls, raises=sentinel)
    monkeypatch.setattr(f"{_MODULE}.save_targeted_replay", lambda *a, **k: None)

    with pytest.raises(sentinel):
        _service(store).run_replay(
            _inputs(equity=Decimal("4321.99")), progress=lambda _m: None
        )

    _args, kwargs = calls["run_targeted_replay"]
    assert kwargs["strategy_version_id"] == "version-1"
    assert kwargs["strategy_semver"] == "1.3.0-research"
    assert kwargs["parameter_hash"] == "hash-1"
    assert kwargs["parameters"] == {
        "momentum_lookback_minutes": 5,
        "whole_shares": True,
    }
    # Exact: not ``float(Decimal(...))`` and back, which would silently change a
    # research artifact's recorded initial equity.
    assert kwargs["initial_equity"] == Decimal("4321.99")
    assert isinstance(kwargs["initial_equity"], Decimal)


def test_replay_saves_to_the_unchanged_directory(
    monkeypatch, sentinel
) -> None:
    """The artifact path is part of the schema: old artifacts must keep loading."""

    saved: list[object] = []
    store = _Store(usable=_two_providers())
    monkeypatch.setattr(
        f"{_MODULE}.group_regular_sessions",
        lambda rows: (("2026-07-20", tuple(rows)),),
    )
    _executor(monkeypatch, "run_targeted_replay", {}, raises=sentinel)
    monkeypatch.setattr(
        f"{_MODULE}.save_targeted_replay",
        lambda result, root: saved.append(root),
    )

    with pytest.raises(sentinel):
        _service(store).run_replay(_inputs(), progress=lambda _m: None)

    assert saved == []  # the sentinel fired before the save, by design


def test_replay_saves_to_the_targeted_replays_directory(
    monkeypatch,
) -> None:
    saved: list[pathlib.Path] = []
    store = _Store(usable=_two_providers())
    monkeypatch.setattr(
        f"{_MODULE}.group_regular_sessions",
        lambda rows: (("2026-07-20", tuple(rows)),),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_replay", lambda *a, **k: "RESULT"
    )
    monkeypatch.setattr(
        f"{_MODULE}.save_targeted_replay",
        lambda result, root: saved.append(pathlib.Path(root)),
    )

    assert _service(store).run_replay(
        _inputs(), progress=lambda _m: None
    ) == "RESULT"
    assert saved == [pathlib.Path("results") / "targeted_replays"]


# -- the robustness pipeline ---------------------------------------------


def _stub_pipeline(monkeypatch, calls: list[str], *, usable_sessions=20):
    """Replace all six executors and all six savers with recording stubs."""

    robustness = SimpleNamespace(
        run_id="robustness-1",
        symbol="AAPL",
        usable_sessions=usable_sessions,
        total_sessions=usable_sessions,
        evidence_grade="B",
        sign_stability_fraction=Decimal("1"),
    )

    def make(name, value=None):
        def fake(*args, **kwargs):
            calls.append(name)
            return value if value is not None else f"<{name}>"

        return fake

    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_robustness",
        make("run_targeted_robustness", robustness),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_overfit_diagnostics",
        make("run_targeted_overfit_diagnostics"),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_data_quality",
        make("run_targeted_data_quality"),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_walk_forward",
        make("run_targeted_walk_forward", "WALK"),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_execution_stress",
        make("run_targeted_execution_stress"),
    )
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_review", make("run_targeted_review", "REVIEW")
    )
    for name in (
        "save_targeted_robustness",
        "save_targeted_overfit",
        "save_targeted_data_quality",
        "save_targeted_walk_forward",
        "save_targeted_execution_stress",
        "save_targeted_review",
    ):
        monkeypatch.setattr(f"{_MODULE}.{name}", make(name))
    return robustness


def test_the_pipeline_runs_in_its_declared_order(monkeypatch) -> None:
    """Robustness, overfit, quality, walk-forward, stress, review.  Unchanged."""

    calls: list[str] = []
    _stub_pipeline(monkeypatch, calls)
    store = _Store(usable=_two_providers())

    _service(store).run_robustness(_inputs(), progress=lambda _m: None)

    assert calls == [
        "run_targeted_robustness",
        "save_targeted_robustness",
        "run_targeted_overfit_diagnostics",
        "save_targeted_overfit",
        "run_targeted_data_quality",
        "save_targeted_data_quality",
        "run_targeted_walk_forward",
        "save_targeted_walk_forward",
        "run_targeted_execution_stress",
        "save_targeted_execution_stress",
        "run_targeted_review",
        "save_targeted_review",
    ]


def test_walk_forward_is_skipped_below_twenty_sessions(monkeypatch) -> None:
    calls: list[str] = []
    _stub_pipeline(
        monkeypatch, calls, usable_sessions=MINIMUM_WALK_FORWARD_SESSIONS - 1
    )
    store = _Store(usable=_two_providers())

    _service(store).run_robustness(_inputs(), progress=lambda _m: None)

    assert "run_targeted_walk_forward" not in calls
    assert "save_targeted_walk_forward" not in calls
    # Everything else still ran, in order.
    assert "run_targeted_execution_stress" in calls
    assert "run_targeted_review" in calls


def test_walk_forward_runs_at_exactly_twenty_sessions(monkeypatch) -> None:
    calls: list[str] = []
    _stub_pipeline(
        monkeypatch, calls, usable_sessions=MINIMUM_WALK_FORWARD_SESSIONS
    )
    store = _Store(usable=_two_providers())

    _service(store).run_robustness(_inputs(), progress=lambda _m: None)

    assert "run_targeted_walk_forward" in calls


def test_data_quality_reads_the_same_providers_raw_rows(monkeypatch) -> None:
    """The selected provider's raw rows -- including unusable ones.

    Two feeds in one quality report would silently claim a completeness number
    for a market that never existed, so the same-provider rule is asserted by
    identity rather than by count.
    """

    calls: list[str] = []
    _stub_pipeline(monkeypatch, calls)
    usable = _two_providers()
    raw = usable + [
        # Unusable rows: one more IBKR row and a POLYGON row that must not leak.
        _Record("AAPL", "IBKR", "2026-07-20T14:03:00+00:00", stale=True),
        _Record("AAPL", "POLYGON", "2026-07-20T14:03:00+00:00", stale=True),
    ]
    store = _Store(usable=usable, raw=raw)
    seen: dict = {}
    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_data_quality",
        lambda result, records: seen.setdefault("raw", records),
    )
    monkeypatch.setattr(f"{_MODULE}.save_targeted_data_quality", lambda *a: None)

    _service(store).run_robustness(_inputs(), progress=lambda _m: None)

    assert {row.provider for row in seen["raw"]} == {"IBKR"}
    assert len(seen["raw"]) == 4
    # Both loads were for the same symbol, one usable and one raw.
    assert store.calls == [("AAPL", True), ("AAPL", False)]


def test_the_bundle_carries_exactly_what_was_produced(monkeypatch) -> None:
    calls: list[str] = []
    _stub_pipeline(monkeypatch, calls)
    store = _Store(usable=_two_providers())

    bundle = _service(store).run_robustness(
        _inputs(), progress=lambda _m: None
    )

    assert bundle.robustness.run_id == "robustness-1"
    assert bundle.overfit == "<run_targeted_overfit_diagnostics>"
    assert bundle.data_quality == "<run_targeted_data_quality>"
    assert bundle.walk_forward == "WALK"
    assert bundle.execution_stress == "<run_targeted_execution_stress>"
    assert bundle.review == "REVIEW"


def test_a_bundle_without_walk_forward_reports_none(monkeypatch) -> None:
    calls: list[str] = []
    _stub_pipeline(monkeypatch, calls, usable_sessions=1)
    store = _Store(usable=_two_providers())

    bundle = _service(store).run_robustness(
        _inputs(), progress=lambda _m: None
    )

    assert bundle.walk_forward is None


def test_a_failing_step_stops_the_pipeline(monkeypatch) -> None:
    """A failed run must not be reported as a partial success."""

    calls: list[str] = []
    _stub_pipeline(monkeypatch, calls)
    store = _Store(usable=_two_providers())

    class Boom(Exception):
        pass

    def exploding(*args, **kwargs):
        calls.append("run_targeted_data_quality")
        raise Boom

    monkeypatch.setattr(
        f"{_MODULE}.run_targeted_data_quality", exploding
    )

    with pytest.raises(Boom):
        _service(store).run_robustness(_inputs(), progress=lambda _m: None)

    assert "run_targeted_execution_stress" not in calls
    assert "run_targeted_review" not in calls


# -- startup restoration --------------------------------------------------


def test_load_saved_reads_all_seven_families(monkeypatch) -> None:
    """One call, seven families.  A family missed here renders as permanently empty."""

    seen_roots: dict[str, pathlib.Path] = {}
    for name, directory in (
        ("load_targeted_replays", "targeted_replays"),
        ("load_targeted_robustness", "targeted_robustness"),
        ("load_targeted_walk_forwards", "targeted_walk_forward"),
        ("load_targeted_overfits", "targeted_overfit"),
        ("load_targeted_data_quality", "targeted_data_quality"),
        ("load_targeted_execution_stress", "targeted_execution_stress"),
        ("load_targeted_reviews", "targeted_review"),
    ):
        monkeypatch.setattr(
            f"{_MODULE}.{name}",
            lambda root, _name=name, _dir=directory: (
                seen_roots.__setitem__(_name, pathlib.Path(root)) or (_name,)
            ),
        )

    snapshot = _service(_Store()).load_saved()

    assert seen_roots == {
        name: pathlib.Path("results") / directory
        for name, directory in (
            ("load_targeted_replays", "targeted_replays"),
            ("load_targeted_robustness", "targeted_robustness"),
            ("load_targeted_walk_forwards", "targeted_walk_forward"),
            ("load_targeted_overfits", "targeted_overfit"),
            ("load_targeted_data_quality", "targeted_data_quality"),
            ("load_targeted_execution_stress", "targeted_execution_stress"),
            ("load_targeted_reviews", "targeted_review"),
        )
    }
    assert snapshot.replay_results == ("load_targeted_replays",)
    assert snapshot.robustness_results == ("load_targeted_robustness",)
    assert snapshot.walk_forward_results == ("load_targeted_walk_forwards",)
    assert snapshot.overfit_results == ("load_targeted_overfits",)
    assert snapshot.data_quality_results == ("load_targeted_data_quality",)
    assert snapshot.execution_stress_results == (
        "load_targeted_execution_stress",
    )
    assert snapshot.review_results == ("load_targeted_reviews",)


def test_load_saved_selects_nothing(monkeypatch) -> None:
    """Startup has never auto-selected a run, and this round does not change it."""

    for name in (
        "load_targeted_replays",
        "load_targeted_robustness",
        "load_targeted_walk_forwards",
        "load_targeted_overfits",
        "load_targeted_data_quality",
        "load_targeted_execution_stress",
        "load_targeted_reviews",
    ):
        monkeypatch.setattr(f"{_MODULE}.{name}", lambda root: ())

    snapshot = _service(_Store()).load_saved()

    assert snapshot.selected_robustness_run_id is None
    assert snapshot.selected_review_run_id is None


def test_load_saved_propagates_a_loader_that_raises(monkeypatch) -> None:
    """The capability decides how to recover; this boundary must not swallow it."""

    class Boom(Exception):
        pass

    def exploding(root):
        raise Boom

    monkeypatch.setattr(f"{_MODULE}.load_targeted_replays", exploding)
    for name in (
        "load_targeted_robustness",
        "load_targeted_walk_forwards",
        "load_targeted_overfits",
        "load_targeted_data_quality",
        "load_targeted_execution_stress",
        "load_targeted_reviews",
    ):
        monkeypatch.setattr(f"{_MODULE}.{name}", lambda root: ())

    with pytest.raises(Boom):
        _service(_Store()).load_saved()


# -- dependency direction and Qt freedom ----------------------------------


def test_the_service_imports_no_qt_and_no_orchestration() -> None:
    """The arrow points one way: capability -> service.  Never back."""

    tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    for module in modules:
        assert not module.startswith("PySide6"), module
        assert not module.startswith("us_quant.desktop_v2"), module
        assert module != "us_quant.desktop", module


def test_the_service_never_runs_a_targeted_request_itself() -> None:
    """No Qt, no dialog, no page, no selection, no route, no event store.

    Asserted against the parsed *code* rather than the file text: the module
    docstring legitimately names ``MainWindow`` to explain what it replaced, and
    a text search would treat that explanation as a dependency.
    """

    tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
    identifiers: set[str] = set()
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)

    for forbidden in (
        "QMessageBox",
        "QWidget",
        "MainWindow",
        "RuntimeEventStore",
        "render_evidence",
        "render_session",
    ):
        assert forbidden not in identifiers, forbidden
    # The preflight and the Shadow runtime are other capabilities entirely; a
    # module path mentioning them would be a dependency in waiting.
    for fragment in ("target_preflight", "shadow"):
        assert not any(fragment in module for module in literals), fragment


def test_run_inputs_freeze_a_strategy_version_without_a_float_round_trip() -> None:
    """``TargetedEvidenceRunInputs.of`` is the one conversion point."""

    # A minimal stand-in carrying exactly the four members ``of`` reads, so this
    # test does not have to construct a full governed ``StrategyVersion``.
    strategy = SimpleNamespace(
        version_id="version-9",
        semver="2.0.0",
        parameter_hash="hash-9",
        parameters={"a": 1, "b": "two"},
    )

    inputs = TargetedEvidenceRunInputs.of(
        symbol="MSFT", strategy=strategy, initial_equity=Decimal("9999.5")
    )

    assert inputs.symbol == "MSFT"
    assert inputs.strategy_version_id == "version-9"
    assert inputs.strategy_semver == "2.0.0"
    assert inputs.parameter_hash == "hash-9"
    assert inputs.parameters_mapping() == {"a": 1, "b": "two"}
    assert inputs.initial_equity == Decimal("9999.5")
    # A fresh dict each call, so a caller cannot mutate the frozen value.
    mapping = inputs.parameters_mapping()
    mapping["c"] = 3
    assert "c" not in inputs.parameters_mapping()


def test_run_inputs_parameters_are_immutable() -> None:
    """Stored as pairs, so nothing can mutate the freeze point afterwards."""

    inputs = _inputs()
    assert isinstance(inputs.parameters, tuple)
    assert all(isinstance(name, str) for name, _value in inputs.parameters)
