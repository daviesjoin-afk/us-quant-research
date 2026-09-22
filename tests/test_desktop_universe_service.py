"""Tests for the desktop universe refresh service and its wiring.

The service is pure application code: no Qt, no threads, no network.  The
two domain calls are faked, so these tests never touch Nasdaq Trader or SEC.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_universe_service import (
    STAGE_DOWNLOAD_OFFICIAL,
    STAGE_ENRICH_SEC,
    STAGE_ENRICH_SEC_START,
    STAGE_PREPARE_REFERENCE,
    SEC_PROFILE_BUDGET,
    DesktopUniverseService,
    UniverseRefreshProgress,
)
from us_quant.paths import STATE_ROOT_ENV, ApplicationPaths
from us_quant.universe import UniverseRefreshCancelled

_REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICE_MODULE = "src/us_quant/desktop_universe_service.py"


def _class_named(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _module_imports(source: str) -> set[str]:
    """The set of modules a source file imports, normalised."""

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def _imported_names(source: str) -> set[str]:
    """The set of symbols a source file imports, normalised.

    ``_module_imports`` only knows module paths, so an assertion like
    "``enrich_us_profiles`` is not imported" is vacuously true against it.
    """

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


class _FakePaths:
    """An ``ApplicationPaths`` stand-in that records every call."""

    def __init__(self, resource_root: Path, reference_root: Path) -> None:
        self.resource_root = resource_root
        self.reference_root = reference_root
        self.calls: list[str] = []

    def ensure_user_reference_catalog(self) -> Path:
        self.calls.append("ensure_user_reference_catalog")
        return self.reference_root


def _paths(tmp_path: Path) -> _FakePaths:
    return _FakePaths(
        resource_root=tmp_path / "resources",
        reference_root=tmp_path / "reference",
    )


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    _qapp()
    window = MainWindow()
    _qapp().processEvents()
    return window


# -- 2/37: the constructor performs no I/O -----------------------------


def test_the_constructor_performs_no_io(tmp_path) -> None:
    """Spec 2/37: constructing the service must not touch the filesystem.

    ``ensure_user_reference_catalog`` raises here, so any I/O in the
    constructor surfaces as a failure rather than as a stray directory.
    """

    paths = _paths(tmp_path)

    def forbidden() -> Path:
        raise AssertionError("the constructor must not prepare the catalog")

    paths.ensure_user_reference_catalog = forbidden

    service = DesktopUniverseService(paths=paths)

    assert service.paths is paths
    assert paths.calls == []


def test_the_constructor_only_stores_paths(tmp_path) -> None:
    """Spec 35: no mutable runtime state is kept between calls."""

    service = DesktopUniverseService(paths=_paths(tmp_path))

    assert list(vars(service)) == ["paths"]


def test_the_constructor_does_not_create_the_reference_root(
    tmp_path,
) -> None:
    """Spec 5: the writable root only appears once ``refresh`` runs."""

    paths = _paths(tmp_path)
    DesktopUniverseService(paths=paths)

    assert not paths.reference_root.exists()


# -- 4/38: the call order is the contract ------------------------------


def test_refresh_emits_and_calls_in_the_original_order(
    tmp_path, monkeypatch
) -> None:
    """Spec 38: order, not merely "everything was called"."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    order: list[str] = []

    class _Paths:
        resource_root = tmp_path / "resources"
        reference_root = tmp_path / "reference"

        def ensure_user_reference_catalog(self) -> Path:
            order.append("ensure_user_reference_catalog")
            return self.reference_root

    service.paths = _Paths()

    def refresh_official_universe(**kwargs):
        order.append("refresh_official_universe")
        return "SNAPSHOT"

    def enrich_us_profiles(snapshot, **kwargs):
        order.append("enrich_us_profiles")
        return "ENRICHED"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(
        should_stop=None,
        progress=lambda event: order.append(f"progress:{event.stage}"),
    )

    assert order == [
        f"progress:{STAGE_PREPARE_REFERENCE}",
        "ensure_user_reference_catalog",
        f"progress:{STAGE_DOWNLOAD_OFFICIAL}",
        "refresh_official_universe",
        f"progress:{STAGE_ENRICH_SEC_START}",
        "enrich_us_profiles",
    ]


def test_the_reference_progress_comes_before_the_directory_exists(
    tmp_path, monkeypatch
) -> None:
    """Spec 4: emit first, then create -- never the other way round."""

    import us_quant.desktop_universe_service as module

    seen: list[tuple[str, bool]] = []
    paths = _paths(tmp_path)
    real_ensure = paths.ensure_user_reference_catalog

    def recording_ensure() -> Path:
        seen.append(("ensure", paths.reference_root.exists()))
        return real_ensure()

    paths.ensure_user_reference_catalog = recording_ensure
    service = DesktopUniverseService(paths=paths)

    def refresh_official_universe(**kwargs):
        return "SNAPSHOT"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    service.refresh(
        should_stop=None,
        progress=lambda event: seen.append(
            (event.stage, paths.reference_root.exists())
        ),
    )

    assert seen[0] == (STAGE_PREPARE_REFERENCE, False)
    assert seen[1] == ("ensure", False)


# -- 5/6/39: the official download arguments ---------------------------


def test_official_download_receives_every_argument_verbatim(
    tmp_path, monkeypatch
) -> None:
    """Spec 5/6/39: sentinels for each parameter, including save_snapshot."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    seen: dict = {}

    def refresh_official_universe(**kwargs):
        seen.update(kwargs)
        return "SNAPSHOT"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert seen["cache_root"] == paths.reference_root
    assert seen["leader_seed_path"] == (
        paths.resource_root / "configs" / "sector_leaders.csv"
    )
    assert seen["china_denylist_path"] == (
        paths.resource_root / "configs" / "china_concept_denylist.csv"
    )
    assert seen["should_stop"] is stop
    assert seen["save_snapshot"] is False


def test_the_official_download_never_saves_a_snapshot(
    tmp_path, monkeypatch
) -> None:
    """Spec 7: a cancelled enrichment must not clobber the last snapshot.

    ``save_snapshot=False`` is the safety semantic that keeps a
    half-finished refresh from overwriting a complete one.
    """

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: seen.update(kwargs) or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    service.refresh(should_stop=None)

    assert seen["save_snapshot"] is False


# -- 9/40: the enrichment arguments ------------------------------------


def test_enrichment_receives_every_argument_verbatim(
    tmp_path, monkeypatch
) -> None:
    """Spec 9/40: snapshot identity, cache root, budget, callback identity."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    seen: dict = {}
    snapshot = object()

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: snapshot,
    )

    def enrich_us_profiles(passed, **kwargs):
        seen["snapshot"] = passed
        seen.update(kwargs)
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert seen["snapshot"] is snapshot
    assert seen["cache_root"] == paths.reference_root / "sec_profiles"
    assert seen["max_new_profiles"] == 500
    assert seen["should_stop"] is stop


def test_the_profile_budget_is_five_hundred(tmp_path, monkeypatch) -> None:
    """Spec 8/9: 500 stays a literal, not a tuning knob."""

    assert SEC_PROFILE_BUDGET == 500

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}
    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.update(kwargs) or "ENRICHED",
    )

    service.refresh(should_stop=None)

    assert seen["max_new_profiles"] == 500


def test_the_domain_timeouts_are_left_at_their_defaults(
    tmp_path, monkeypatch
) -> None:
    """Spec 9: no request interval / user agent / timeout is passed."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}
    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.update(kwargs) or "ENRICHED",
    )

    service.refresh(should_stop=None)

    for name in (
        "request_interval_seconds",
        "user_agent",
        "timeout",
        "max_attempts",
    ):
        assert name not in seen, name


# -- 10/41/42: the SEC progress becomes a domain event -----------------


def test_sec_progress_becomes_a_domain_event(tmp_path, monkeypatch) -> None:
    """Spec 10/41: done/total/symbol map onto the DTO fields."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        progress(37, 500, "AAPL")
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert len(sec) == 1
    assert sec[0].done == 37
    assert sec[0].total == 500
    assert sec[0].detail == "AAPL"


def test_a_failure_detail_is_passed_through_untouched(
    tmp_path, monkeypatch
) -> None:
    """Spec 10/42: no parsing, no truncation, no translation."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []
    detail = "XYZ 暂时失败: timeout"

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        progress(9, 500, detail)
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert sec[0].detail == detail


def test_every_sec_progress_call_is_forwarded(tmp_path, monkeypatch) -> None:
    """Spec 10: one event per domain callback, none dropped."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        for index in range(3):
            progress(index, 500, f"S{index}")
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert [(event.done, event.detail) for event in sec] == [
        (0, "S0"),
        (1, "S1"),
        (2, "S2"),
    ]


# -- 11/43: the four stages, and progress=None -------------------------


def test_the_service_declares_exactly_four_stages(tmp_path) -> None:
    """Spec 11: the stage names are module constants, not magic strings."""

    import us_quant.desktop_universe_service as module

    assert STAGE_PREPARE_REFERENCE == "prepare_reference"
    assert STAGE_DOWNLOAD_OFFICIAL == "download_official"
    assert STAGE_ENRICH_SEC_START == "enrich_sec_start"
    assert STAGE_ENRICH_SEC == "enrich_sec"

    source = pathlib.Path(
        _REPO_ROOT / SERVICE_MODULE
    ).read_text(encoding="utf-8")
    assert '"prepare_reference"' in source
    assert '"download_official"' in source
    assert '"enrich_sec_start"' in source
    assert '"enrich_sec"' in source
    assert source.count('"prepare_reference"') == 1
    assert source.count('"download_official"') == 1
    assert source.count('"enrich_sec_start"') == 1
    assert source.count('"enrich_sec"') == 1


def test_refresh_works_without_an_observer(tmp_path, monkeypatch) -> None:
    """Spec 43: no observer must not change the business path."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    calls: list[str] = []

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: calls.append("official") or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: calls.append("enrich") or "ENRICHED",
    )

    assert service.refresh(should_stop=None) == "ENRICHED"
    assert calls == ["official", "enrich"]
    assert paths.calls == ["ensure_user_reference_catalog"]


# -- 14: the return value is the domain snapshot -----------------------


def test_refresh_returns_the_enriched_snapshot(tmp_path, monkeypatch) -> None:
    """Spec 14: no extra business wrapper around the domain result."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    enriched = object()

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: enriched
    )

    assert service.refresh(should_stop=None) is enriched


# -- 15/44/45/46: exceptions propagate unchanged -----------------------


def test_cancellation_during_the_download_propagates(
    tmp_path, monkeypatch
) -> None:
    """Spec 15/44: the service must not swallow or convert it."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    enriched: list = []

    def cancelled(**kwargs):
        raise UniverseRefreshCancelled()

    monkeypatch.setattr(module, "refresh_official_universe", cancelled)
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: enriched.append(snapshot) or "ENRICHED",
    )

    with pytest.raises(UniverseRefreshCancelled):
        service.refresh(should_stop=None)

    assert enriched == []


def test_cancellation_during_enrichment_propagates(
    tmp_path, monkeypatch
) -> None:
    """Spec 45: the same for the SEC phase."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))

    def cancelled(snapshot, **kwargs):
        raise UniverseRefreshCancelled()

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(module, "enrich_us_profiles", cancelled)

    with pytest.raises(UniverseRefreshCancelled):
        service.refresh(should_stop=None)


def test_a_generic_exception_propagates_unchanged(
    tmp_path, monkeypatch
) -> None:
    """Spec 16/46: no new error hierarchy, no wrapping."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    error = OSError("offline")

    def failing(**kwargs):
        raise error

    monkeypatch.setattr(module, "refresh_official_universe", failing)

    with pytest.raises(OSError) as caught:
        service.refresh(should_stop=None)

    assert caught.value is error


def test_the_service_defines_no_cancellation_api(tmp_path) -> None:
    """Spec 17: cancellation belongs to the window, not to the service."""

    for name in ("cancel", "stop", "request_stop"):
        assert not hasattr(DesktopUniverseService, name), name


# -- 47: the progress DTO ----------------------------------------------


def test_the_progress_dto_is_frozen_and_slotted() -> None:
    """Spec 47: immutable, slot-based, and with a fixed field set."""

    assert [field.name for field in dataclasses.fields(
        UniverseRefreshProgress
    )] == ["stage", "done", "total", "detail"]

    event = UniverseRefreshProgress(stage=STAGE_ENRICH_SEC, done=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.stage = "other"  # type: ignore[misc]
    assert not hasattr(event, "__dict__")


def test_the_progress_dto_carries_nothing_but_data() -> None:
    """Spec 47: no exception, window, worker or Event field."""

    names = {
        field.name for field in dataclasses.fields(UniverseRefreshProgress)
    }

    assert names == {"stage", "done", "total", "detail"}
    for forbidden in ("exception", "window", "worker", "event"):
        assert forbidden not in names


# -- the service's own boundaries --------------------------------------


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 33: dependency set equality, not a forbidden-name scan."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")

    assert _module_imports(source) == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "us_quant.paths",
        "us_quant.universe",
    }


def test_the_service_imports_no_qt_and_no_gui_modules() -> None:
    """Spec 33: the service is Qt-free application code."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    imported = _module_imports(source)

    for forbidden in (
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "us_quant.desktop",
        "us_quant.desktop_workers",
        "us_quant.runtime_supervisor",
        "us_quant.market_data_service",
        "us_quant.history_queue",
        "us_quant.desktop_history_service",
        "us_quant.auto_quant",
    ):
        assert forbidden not in imported, forbidden

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            head = node.module.split(".")[0]
            assert head != "PySide6", node.module


def test_the_service_starts_no_threads() -> None:
    """Spec 34: threading stays with TaskThread and the window."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")

    for forbidden in (
        "Thread(",
        "QThread",
        "ThreadPoolExecutor",
        "asyncio",
        "import threading",
        "from threading",
    ):
        assert forbidden not in source, forbidden


def test_the_service_holds_no_mutable_runtime_state() -> None:
    """Spec 35: only ``paths`` is stored; no snapshot, worker or error."""

    tree = ast.parse(
        (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    )
    service = _class_named(tree, "DesktopUniverseService")

    assigned: list[str] = []
    for node in ast.walk(service):
        if isinstance(node, ast.Attribute) and isinstance(
            node.ctx, ast.Store
        ):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                assigned.append(node.attr)

    assert set(assigned) == {"paths"}


# -- 48: the window owns the service -----------------------------------


def test_the_window_owns_the_service(monkeypatch, tmp_path) -> None:
    """Spec 48: one service, built over the window's own paths."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert isinstance(window.universe_service, DesktopUniverseService)
        assert window.universe_service.paths is window.paths
    finally:
        window.deleteLater()


def test_the_window_does_not_build_a_second_paths_object(
    monkeypatch, tmp_path
) -> None:
    """Spec 48: ``ApplicationPaths.discover`` must not run again."""

    calls: list[int] = []
    original = ApplicationPaths.discover

    def counting_discover():
        calls.append(1)
        return original()

    monkeypatch.setattr(ApplicationPaths, "discover", counting_discover)

    window = _window(monkeypatch, tmp_path)
    try:
        assert window.universe_service.paths is window.paths
        assert calls == [1]
    finally:
        window.deleteLater()


# -- 25/49/50: the window's formatting ---------------------------------


# -- 51: the cancel callback is the current event ----------------------


def test_the_service_receives_the_same_callback_twice(
    monkeypatch, tmp_path
) -> None:
    """Spec 18: both domain calls get the identical callable."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: list = []

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: seen.append(kwargs["should_stop"]) or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.append(kwargs["should_stop"])
        or "ENRICHED",
    )

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert len(seen) == 2
    assert seen[0] is stop
    assert seen[1] is stop


def test_desktop_py_no_longer_calls_the_domain_directly() -> None:
    """Spec 57: the real calls live only in the service module."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert "refresh_official_universe(" not in source
    assert "enrich_us_profiles(" not in source
    assert "refresh_official_universe" not in _imported_names(source)
    assert "enrich_us_profiles" not in _imported_names(source)

    service = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    assert "refresh_official_universe(" in service
    assert "enrich_us_profiles(" in service


def test_the_window_still_uses_the_remaining_universe_helpers() -> None:
    """Spec 28: the three helpers other paths need are still imported.

    Import *names* are checked, not source substrings: the auto-scan
    teardown calls ``prioritized_research_symbols`` in its body, so a
    substring assertion would still pass after the import was deleted.
    """

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "us_quant.universe" in _module_imports(source)
    for name in (
        "UniverseSnapshot",
        "load_universe_snapshot",
        "prioritized_research_symbols",
    ):
        assert name in imported, name
