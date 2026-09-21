"""MainWindow strategy wiring tests.

These pin the Desktop half of the migration:

* the ``strategy`` route is the native ``StrategyPage``, and the legacy
  ``_strategy_manager_tab`` builder plus its five controller handlers are gone;
* the window owns a ``StrategyApplication`` and a ``StrategySelectionService``,
  and never a concrete repository;
* the runtime-selection combos are filled from the service, which is also why
  the auto-rotation combo is populated at start-up at all -- the retired page
  handler ran before that tab existed, so its ``hasattr`` guard skipped it and
  the combo started empty.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from us_quant.backtest_workspace import STRATEGY_SPECS  # noqa: E402
from us_quant.desktop import MainWindow  # noqa: E402
from us_quant.desktop_v2.pages.strategy import StrategyPage  # noqa: E402
from us_quant.paths import STATE_ROOT_ENV  # noqa: E402
from us_quant.trading.application.strategies import (  # noqa: E402
    StrategyApplication,
)
from us_quant.trading.application.strategy_selection import (  # noqa: E402
    StrategySelectionPurpose,
    StrategySelectionService,
)
from us_quant.trading.composition.strategies import (  # noqa: E402
    build_strategy_application,
)
from us_quant.trading.domain.strategy import StrategyStatus  # noqa: E402

_APP = None
_DESKTOP_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop.py"
)

RETIRED_METHODS = (
    "_strategy_manager_tab",
    "_populate_strategy_registry",
    "_selected_strategy_record",
    "_strategy_registry_selection_changed",
    "_clone_strategy_version",
    "_transition_selected_strategy",
)


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def dialogs(monkeypatch) -> list:
    """Capture message boxes instead of opening them."""

    seen: list = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: seen.append(("warning", args)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: seen.append(("information", args)),
    )
    return seen


@pytest.fixture()
def window(monkeypatch, tmp_path, dialogs):
    _qapp()
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.deleteLater()


def _row_for(page: StrategyPage, version_id: str) -> int:
    for row in range(page.version_table.rowCount()):
        item = page.version_table.item(row, 0)
        if (
            item is not None
            and item.data(Qt.ItemDataRole.UserRole) == version_id
        ):
            return row
    raise AssertionError(f"{version_id} is not on the page")


def _version_of(window: MainWindow, strategy_id: str, semver: str):
    return next(
        version
        for version in window.strategies.list_versions()
        if version.strategy_id == strategy_id and version.semver == semver
    )


# -- the route is a native v2 page -------------------------------------


def test_the_strategy_route_is_the_native_strategy_page(window) -> None:
    assert isinstance(window.strategy_page, StrategyPage)
    assert window.shell.page("strategy") is window.strategy_page


def test_the_legacy_strategy_builders_are_gone() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    methods = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for removed in RETIRED_METHODS:
        assert removed not in methods, removed
    for still_there in (
        "_strategy_version_selected",
        "_strategy_clone_requested",
        "_strategy_transition_requested",
        "_refresh_strategy_page",
    ):
        assert still_there in methods


def test_the_window_does_not_name_the_concrete_repository() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "SQLiteStrategyRepository" not in source
    assert "StrategyRegistry" not in source
    assert "build_strategy_application" in source


# -- the window owns the application and the selection -------------------


def test_the_window_owns_a_strategy_application(window) -> None:
    assert isinstance(window.strategies, StrategyApplication)


def test_the_window_owns_a_selection_service(window) -> None:
    assert isinstance(window.strategy_selection, StrategySelectionService)


def test_the_catalogue_is_seeded_on_startup(window) -> None:
    strategy_ids = {
        version.strategy_id for version in window.strategies.list_versions()
    }
    assert {
        "sector-momentum",
        "intraday-targeted-t",
        "intraday-auto-rotation",
        "buy-hold",
        "dual-ma-trend",
        "donchian-breakout",
        "rsi-mean-reversion",
        "legacy-sector-momentum",
    } <= strategy_ids


def test_starting_again_over_the_same_store_does_not_duplicate(
    window,
) -> None:
    """A second start-up on the existing file must add nothing."""

    before = len(window.strategies.list_versions())
    second = build_strategy_application(
        window.paths.runtime_root / "strategies.sqlite3"
    )
    assert len(second.list_versions()) == before


# -- the combos are views of the service ---------------------------------


def _auto_combo(window):
    """The auto-rotation combo, which now lives on the execution page."""

    return window.execution_page.controls.strategy_combo



def test_the_auto_rotation_combo_is_populated_at_startup(window) -> None:
    """The retired handler could not do this, so the combo started empty.

    The combo now lives on the execution page; the window points it at the
    service through the page rather than reaching for the widget.
    """

    assert _auto_combo(window).count() >= 1
    selected = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert selected is not None
    assert selected.strategy_id == "intraday-auto-rotation"


def test_the_targeted_shadow_combo_is_populated_at_startup(window) -> None:
    assert window.targeted_validation_page.session_panel.controls.strategy_combo.count() >= 1
    selected = window.strategy_selection.selected(
        StrategySelectionPurpose.TARGETED_SHADOW
    )
    assert selected is not None
    assert selected.strategy_id == "intraday-targeted-t"


def test_the_auto_combo_only_offers_rotation_versions(window) -> None:
    offered = {
        window.strategy_selection.selected(
            StrategySelectionPurpose.AUTO_ROTATION
        ).strategy_id
    }
    assert offered == {"intraday-auto-rotation"}
    combo = _auto_combo(window)
    version_ids = {
        combo.itemData(index) for index in range(combo.count())
    }
    rotation_ids = {
        version.version_id
        for version in window.strategies.list_versions()
        if version.strategy_id == "intraday-auto-rotation"
        and version.status is StrategyStatus.RESEARCH
    }
    assert version_ids == rotation_ids


def test_choosing_a_combo_entry_records_it_as_the_runtime_selection(
    window,
) -> None:
    combo = _auto_combo(window)
    assert combo.count() >= 2
    target = combo.itemData(combo.count() - 1)

    combo.setCurrentIndex(combo.count() - 1)
    _APP.processEvents()

    selected = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert selected is not None
    assert selected.version_id == target


def test_a_stopped_version_drops_out_of_the_combo(window) -> None:
    selected = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    combo = _auto_combo(window)
    before = combo.count()

    window.strategies.transition(
        selected.version_id, StrategyStatus.STOPPED, reason="test"
    )
    window._refresh_strategy_page()

    combo = _auto_combo(window)
    assert combo.count() == before - 1
    version_ids = {
        combo.itemData(index) for index in range(combo.count())
    }
    assert selected.version_id not in version_ids
    replacement = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert replacement is not None
    assert replacement.version_id != selected.version_id


def test_the_selection_accessors_agree_with_the_service(window) -> None:
    """Equality, not identity: each read builds a fresh domain object.

    The repository decodes a row every time, so the accessor and the service
    return equal-but-distinct instances.  What matters is that they agree.
    """

    for method, purpose in (
        ("_selected_auto_strategy_record", StrategySelectionPurpose.AUTO_ROTATION),
        (
            "_selected_shadow_strategy_record",
            StrategySelectionPurpose.TARGETED_SHADOW,
        ),
    ):
        from_accessor = getattr(window, method)()
        from_service = window.strategy_selection.selected(purpose)
        assert from_accessor is not None
        assert from_accessor == from_service
        assert from_accessor.version_id == from_service.version_id


# -- governance actions through the page ---------------------------------


def test_showing_a_version_sets_the_account_notice(window) -> None:
    target = _version_of(window, "intraday-targeted-t", "1.3.0-research")
    window.strategy_page.version_table.selectRow(
        _row_for(window.strategy_page, target.version_id)
    )
    _APP.processEvents()

    notice = window.account_page.notice_label.text()
    assert "探索性影子模式" in notice
    assert "intraday-targeted-t" in notice


def test_a_clone_request_creates_a_version(window) -> None:
    source = _version_of(window, "buy-hold", "1.0.0-research")
    before = len(window.strategies.list_versions())

    window._strategy_clone_requested(
        source.version_id,
        "1.0.1-research",
        '{"whole_shares": true}',
    )

    versions = window.strategies.list_versions()
    assert len(versions) == before + 1
    clone = next(
        version
        for version in versions
        if version.semver == "1.0.1-research"
        and version.strategy_id == "buy-hold"
    )
    assert clone.status is StrategyStatus.RESEARCH
    assert clone.gate_passed is False


def test_invalid_clone_json_is_reported_and_changes_nothing(
    window, dialogs
) -> None:
    source = _version_of(window, "buy-hold", "1.0.0-research")
    before = len(window.strategies.list_versions())

    window._strategy_clone_requested(
        source.version_id, "1.0.2-research", "{not json"
    )

    assert len(window.strategies.list_versions()) == before
    assert dialogs and dialogs[-1][0] == "warning"


def test_a_parameter_error_on_clone_is_reported(window, dialogs) -> None:
    source = _version_of(window, "dual-ma-trend", "1.0.0-research")
    before = len(window.strategies.list_versions())

    window._strategy_clone_requested(
        source.version_id,
        "9.9.9-research",
        '{"short_window": 200, "long_window": 20, "whole_shares": true}',
    )

    assert len(window.strategies.list_versions()) == before
    assert dialogs and dialogs[-1][0] == "warning"


def test_a_blocked_gate_transition_is_reported(window, dialogs) -> None:
    source = _version_of(window, "buy-hold", "1.0.0-research")

    window._strategy_transition_requested(source.version_id, "paper_shadow")

    assert (
        window.strategies.get_version(source.version_id).status
        is StrategyStatus.RESEARCH
    )
    assert dialogs and dialogs[-1][0] == "warning"


def test_a_stop_transition_is_applied(window) -> None:
    source = _version_of(window, "buy-hold", "1.0.0-research")

    window._strategy_transition_requested(source.version_id, "stopped")

    assert (
        window.strategies.get_version(source.version_id).status
        is StrategyStatus.STOPPED
    )
    assert window.strategy_page.selected_version_id() is not None


def test_a_governance_action_does_not_retarget_the_runtime(window) -> None:
    """Spec 75, on the real window: viewing is not running."""

    runtime_before = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    target = _version_of(window, "buy-hold", "1.0.0-research")
    window.strategy_page.version_table.selectRow(
        _row_for(window.strategy_page, target.version_id)
    )
    _APP.processEvents()

    runtime_after = window.strategy_selection.selected(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert runtime_after.version_id == runtime_before.version_id


def test_the_backtest_combo_only_offers_research_versions(window) -> None:
    version_ids = {
        window.backtest_page.controls.strategy_combo.itemData(index)
        for index in range(window.backtest_page.controls.strategy_combo.count())
    }
    assert version_ids
    for version_id in version_ids:
        assert (
            window.strategies.get_version(version_id).status
            is StrategyStatus.RESEARCH
        )


def test_the_backtest_combo_excludes_the_other_families(window) -> None:
    """The BACKTEST policy is the executable factory's four ids, no more."""

    offered = {
        version.strategy_id
        for version in (
            window.strategies.get_version(
                window.backtest_page.controls.strategy_combo.itemData(index)
            )
            for index in range(window.backtest_page.controls.strategy_combo.count())
        )
    }
    assert offered == {
        spec.strategy_id for spec in STRATEGY_SPECS
    }
    assert "sector-momentum" not in offered


def test_the_compare_all_branch_picks_one_version_per_family(window) -> None:
    records = window._backtest_records(True, "")

    assert records
    assert len({record.strategy_id for record in records}) == len(records)
    assert all(
        record.status is StrategyStatus.RESEARCH for record in records
    )
    assert [record.strategy_id for record in records] == [
        spec.strategy_id
        for spec in STRATEGY_SPECS
        if spec.strategy_id in {row.strategy_id for row in records}
    ]


def test_the_single_branch_resolves_the_combo_choice(window) -> None:
    """The other half of ``_backtest_records``: it reads the combo's id.

    Both branches have to return versions the selection service considers
    eligible; the single branch additionally has to find the id it was given,
    which is the half a combo-only assertion would not exercise.
    """

    window.backtest_page.controls.strategy_combo.setCurrentIndex(0)
    chosen = window.backtest_page.controls.strategy_combo.currentData()

    records = window._backtest_records(False, str(chosen))

    assert len(records) == 1
    assert records[0].version_id == chosen


def test_the_single_branch_returns_nothing_for_a_blank_combo(window) -> None:
    window.backtest_page.controls.strategy_combo.clear()
    assert window._backtest_records(False, "") == []


def test_a_stopped_version_drops_out_of_the_backtest_combo(window) -> None:
    chosen = window.backtest_page.controls.strategy_combo.currentData()
    before = window.backtest_page.controls.strategy_combo.count()

    window.strategies.transition(
        chosen, StrategyStatus.STOPPED, reason="test"
    )
    window._refresh_strategy_page()

    offered = {
        window.backtest_page.controls.strategy_combo.itemData(index)
        for index in range(window.backtest_page.controls.strategy_combo.count())
    }
    assert chosen not in offered
    assert len(offered) == before - 1
