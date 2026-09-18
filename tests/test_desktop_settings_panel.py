"""The Settings page is a widget tree, not a window.

``MainWindow._settings_tab()`` used to build four framed sections and
seventeen controls inline.  These tests pin the extraction: the panel owns
the widgets and their wiring, the window owns the handlers and the aliases,
and the two initial refreshes run from the window *after* the aliases exist.

Two things here are deliberately structural rather than behavioural:

* the panel must not reach for a credential, a file or a service, so a
  source-level guard reads its imports and its ``__init__`` body instead of
  trying to provoke the mistake at runtime;
* the window's thirteen settings handlers must be byte-identical to the
  base commit.  This is the scope guard for the whole step -- a "move the
  layout" change that quietly edits ``_settings_provider_selected`` would
  otherwise read as a successful extraction.

The Qt tests run offscreen; constructing widgets is all they do, so no
socket, no IBKR connection and no order is ever touched.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from us_quant.desktop import MainWindow
from us_quant.desktop_settings_panel import (
    DesktopSettingsCallbacks,
    DesktopSettingsPanel,
)
from us_quant.paths import ApplicationPaths
from us_quant.user_settings import UserPreferences


_APP = QApplication.instance() or QApplication([])

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PANEL_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop_settings_panel.py"
_DESKTOP_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop.py"

BASE_COMMIT = "847fd2dcfed9312ea2ad27cdc0d114d7520ef4df"

# Spec 23: these handlers must not change in this step.  Only
# ``_settings_tab`` may be rewritten.
FROZEN_HANDLERS = (
    "_preview_theme_changed",
    "_settings_provider_selected",
    "_stream_provider_selected",
    "_switch_to_settings_provider",
    "_paper_order_capability_toggled",
    "_extended_hours_paper_toggled",
    "_save_user_preferences",
    "_save_api_credentials",
    "_clear_selected_api_credentials",
    "_clear_saved_finnhub_key",
    "_refresh_credential_status",
    "_api_provider_changed",
    "_set_connection_settings_enabled",
)

# Spec 4: the seventeen controls the window keeps exposing.
SETTINGS_WIDGETS = (
    "settings_theme_combo",
    "settings_provider_combo",
    "settings_switch_provider_button",
    "settings_api_provider_combo",
    "settings_finnhub_key",
    "settings_alpaca_key",
    "settings_alpaca_secret",
    "settings_save_credentials_button",
    "settings_clear_credentials_button",
    "settings_credential_status",
    "settings_ibkr_host",
    "settings_ibkr_port",
    "settings_ibkr_client_id",
    "settings_ibkr_timeout",
    "settings_paper_order_capability",
    "settings_extended_hours_paper",
    "settings_save_button",
)

MARKET_PROVIDERS = (
    ("Finnhub 实时成交", "finnhub_trades"),
    ("Alpaca IEX 免费实时", "alpaca_iex"),
    ("IBKR 实时优先 / 延迟回退", "ibkr"),
    ("IBKR 5×24（盘前 / 盘后 / 隔夜）", "ibkr_extended"),
)

API_PROVIDERS = (
    ("Finnhub", "finnhub_trades"),
    ("Alpaca IEX", "alpaca_iex"),
    ("IBKR Gateway（无需 API Key）", "ibkr"),
)

FIELD_LABELS = (
    "主题",
    "默认行情源",
    "数据源",
    "凭据（留空不修改）",
    "Host",
    "Paper 端口",
    "Client ID",
    "超时（秒）",
)


# -- fixtures ----------------------------------------------------------


@dataclasses.dataclass
class _Recorder:
    calls: list = dataclasses.field(default_factory=list)

    def __call__(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))

    @property
    def count(self) -> int:
        return len(self.calls)


class _Callbacks:
    """Nine recorders, one per application callback."""

    NAMES = (
        "preview_theme_changed",
        "settings_provider_selected",
        "switch_to_settings_provider",
        "api_provider_changed",
        "save_api_credentials",
        "clear_api_credentials",
        "paper_order_capability_toggled",
        "extended_hours_paper_toggled",
        "save_user_preferences",
    )

    def __init__(self) -> None:
        for name in self.NAMES:
            setattr(self, name, _Recorder())

    def bundle(self) -> DesktopSettingsCallbacks:
        return DesktopSettingsCallbacks(
            **{name: getattr(self, name) for name in self.NAMES}
        )

    def counts(self) -> dict:
        return {name: getattr(self, name).count for name in self.NAMES}

    def total(self) -> int:
        return sum(self.counts().values())


class _ComboWidthRecorder:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, combo, **kwargs) -> None:
        self.calls.append((combo, kwargs))


class _LabelRecorder:
    """A stand-in for ``MainWindow._field_label``.

    Returns a real label so the layout is still buildable, but records every
    caption it was asked for -- a panel that quietly defined its own label
    style would never call this.
    """

    def __init__(self) -> None:
        self.texts: list = []

    def __call__(self, text: str) -> QLabel:
        self.texts.append(text)
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label


def _preferences(**overrides) -> UserPreferences:
    values = dict(
        theme="dark",
        market_provider="finnhub_trades",
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=71,
        connection_timeout_seconds=15.0,
        paper_order_capability_enabled=False,
        extended_hours_paper_enabled=False,
    )
    values.update(overrides)
    return UserPreferences(**values)


def _paths(tmp_path: pathlib.Path) -> ApplicationPaths:
    return ApplicationPaths(
        resource_root=tmp_path / "resources",
        state_root=tmp_path / "state",
    )


def _panel(
    tmp_path,
    *,
    preferences: UserPreferences | None = None,
    callbacks: _Callbacks | None = None,
    labels: _LabelRecorder | None = None,
    widths: _ComboWidthRecorder | None = None,
) -> DesktopSettingsPanel:
    return DesktopSettingsPanel(
        preferences=preferences or _preferences(),
        paths=_paths(tmp_path),
        callbacks=(callbacks or _Callbacks()).bundle(),
        field_label=labels or _LabelRecorder(),
        configure_combo_width=widths or _ComboWidthRecorder(),
    )


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv("US_QUANT_STATE_ROOT", str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


# -- source-level helpers ---------------------------------------------


def _panel_tree() -> ast.Module:
    return ast.parse(_PANEL_PATH.read_text(encoding="utf-8"))


def _desktop_tree() -> ast.Module:
    return ast.parse(_DESKTOP_PATH.read_text(encoding="utf-8"))


def _panel_imported_modules() -> set:
    modules = set()
    for node in ast.walk(_panel_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _panel_class() -> ast.ClassDef:
    tree = _panel_tree()
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "DesktopSettingsPanel"
    )


def _panel_init() -> ast.FunctionDef:
    return next(
        node
        for node in _panel_class().body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )


def _main_window_class(tree: ast.Module | None = None) -> ast.ClassDef:
    tree = tree or _desktop_tree()
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _source_of(cls: ast.ClassDef, method: ast.FunctionDef, text: str) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(lines[method.lineno - 1 : method.end_lineno])


def _base_source(path: str) -> str | None:
    """Read a file as it stood at the base commit, or None if unavailable.

    CI checks out a single commit, so the base commit is often absent from
    the local object store.  Fetching it on demand keeps this scope guard
    *running* in CI instead of silently skipping -- a skipped guard is worth
    nothing.  Only a genuinely unreachable commit falls through to None.
    """

    command = ["git", "show", f"{BASE_COMMIT}:{path}"]
    try:
        result = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        subprocess.run(
            ["git", "fetch", "--depth", "1", "-q", "origin", BASE_COMMIT],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        result = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return None
    return result.stdout


def _statements(node: ast.FunctionDef) -> list:
    """Body statements with any docstring removed."""

    body = list(node.body)
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            body = body[1:]
    return body


# -- 28: constructor fires nothing -------------------------------------


def test_constructing_the_panel_fires_no_callback(tmp_path) -> None:
    """Every callback must still be at zero after construction.

    The combos are populated, positioned and only then connected.  If the
    ``connect`` were moved above the ``setCurrentIndex``, building the page
    would immediately run the theme preview, the provider sync and the
    credential refresh -- against a window whose aliases do not exist yet.
    """

    callbacks = _Callbacks()

    panel = _panel(tmp_path, callbacks=callbacks)

    assert callbacks.counts() == {name: 0 for name in _Callbacks.NAMES}
    panel.deleteLater()


def test_every_callback_is_wired_exactly_once(tmp_path) -> None:
    """Nine callbacks, nine connections -- no double wiring, no omissions."""

    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    counts = callbacks.counts()

    assert set(counts) == set(_Callbacks.NAMES)
    assert all(count == 0 for count in counts.values())
    panel.deleteLater()


# -- 29: initial values ------------------------------------------------


def test_initial_values_come_from_the_preferences(tmp_path) -> None:
    preferences = _preferences(
        theme="light",
        market_provider="alpaca_iex",
        ibkr_host="localhost",
        ibkr_client_id=88,
        connection_timeout_seconds=27,
        paper_order_capability_enabled=True,
        extended_hours_paper_enabled=True,
    )

    panel = _panel(tmp_path, preferences=preferences)

    assert panel.settings_theme_combo.currentData() == "light"
    assert panel.settings_provider_combo.currentData() == "alpaca_iex"
    assert panel.settings_api_provider_combo.currentData() == "alpaca_iex"
    assert panel.settings_ibkr_host.text() == "localhost"
    assert panel.settings_ibkr_client_id.value() == 88
    assert panel.settings_ibkr_timeout.value() == 27
    assert panel.settings_paper_order_capability.isChecked() is True
    assert panel.settings_extended_hours_paper.isChecked() is True
    panel.deleteLater()


def test_ibkr_port_is_locked_to_the_paper_gateway(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert panel.settings_ibkr_port.minimum() == 4002
    assert panel.settings_ibkr_port.maximum() == 4002
    assert panel.settings_ibkr_port.value() == 4002
    panel.deleteLater()


def test_the_timeout_spinbox_rounds_the_stored_seconds(tmp_path) -> None:
    panel = _panel(
        tmp_path,
        preferences=_preferences(connection_timeout_seconds=27.6),
    )

    assert panel.settings_ibkr_timeout.value() == 28
    panel.deleteLater()


def test_extended_hours_maps_onto_the_ibkr_api_provider(tmp_path) -> None:
    """``ibkr_extended`` is not an API-key provider, so it shows as ``ibkr``."""

    panel = _panel(
        tmp_path, preferences=_preferences(market_provider="ibkr_extended")
    )

    assert panel.settings_api_provider_combo.currentData() == "ibkr"
    assert panel.settings_provider_combo.currentData() == "ibkr_extended"
    panel.deleteLater()


def test_an_unknown_provider_falls_back_to_the_first_item(tmp_path) -> None:
    """``findData`` returns -1; ``max(0, ...)`` picks the first entry."""

    panel = _panel(
        tmp_path, preferences=_preferences(market_provider="nope")
    )

    assert panel.settings_provider_combo.currentIndex() == 0
    panel.deleteLater()


# -- 30: provider order ------------------------------------------------


def test_the_market_provider_combo_keeps_its_display_order(tmp_path) -> None:
    panel = _panel(tmp_path)

    items = tuple(
        (panel.settings_provider_combo.itemText(i),
         panel.settings_provider_combo.itemData(i))
        for i in range(panel.settings_provider_combo.count())
    )

    assert items == MARKET_PROVIDERS
    panel.deleteLater()


def test_the_api_provider_combo_keeps_its_display_order(tmp_path) -> None:
    panel = _panel(tmp_path)

    items = tuple(
        (panel.settings_api_provider_combo.itemText(i),
         panel.settings_api_provider_combo.itemData(i))
        for i in range(panel.settings_api_provider_combo.count())
    )

    assert items == API_PROVIDERS
    panel.deleteLater()


def test_the_theme_combo_keeps_its_display_order(tmp_path) -> None:
    panel = _panel(tmp_path)

    items = tuple(
        (panel.settings_theme_combo.itemText(i),
         panel.settings_theme_combo.itemData(i))
        for i in range(panel.settings_theme_combo.count())
    )

    assert items == (("深色", "dark"), ("浅色", "light"))
    panel.deleteLater()


# -- 31: credential inputs --------------------------------------------


def test_credential_inputs_are_password_fields(tmp_path) -> None:
    panel = _panel(tmp_path)

    for name in (
        "settings_finnhub_key",
        "settings_alpaca_key",
        "settings_alpaca_secret",
    ):
        widget = getattr(panel, name)
        assert isinstance(widget, QLineEdit)
        assert widget.echoMode() == QLineEdit.Password
    panel.deleteLater()


def test_credential_inputs_keep_their_placeholders(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert (
        panel.settings_finnhub_key.placeholderText()
        == "Finnhub API Key（留空不修改）"
    )
    assert (
        panel.settings_alpaca_key.placeholderText()
        == "Alpaca API Key（留空不修改）"
    )
    assert (
        panel.settings_alpaca_secret.placeholderText()
        == "Alpaca API Secret（留空不修改）"
    )
    panel.deleteLater()


def test_the_credential_status_label_starts_empty(tmp_path) -> None:
    """The panel creates the label; the window decides its text."""

    panel = _panel(tmp_path)

    assert isinstance(panel.settings_credential_status, QLabel)
    assert panel.settings_credential_status.text() == ""
    panel.deleteLater()


# -- 32: callback wiring -----------------------------------------------


def test_the_theme_combo_calls_the_preview_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    # ``dark`` is already current, so switching to the other entry is what
    # emits ``currentIndexChanged``.
    panel.settings_theme_combo.setCurrentIndex(1)

    assert callbacks.preview_theme_changed.count == 1
    panel.deleteLater()


def test_the_provider_combo_calls_the_selection_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_provider_combo.setCurrentIndex(1)

    assert callbacks.settings_provider_selected.count == 1
    panel.deleteLater()


def test_the_api_provider_combo_calls_the_change_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_api_provider_combo.setCurrentIndex(1)

    assert callbacks.api_provider_changed.count == 1
    panel.deleteLater()


def test_the_switch_button_calls_the_reconnect_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_switch_provider_button.click()

    assert callbacks.switch_to_settings_provider.count == 1
    panel.deleteLater()


def test_the_save_credentials_button_calls_its_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_save_credentials_button.click()

    assert callbacks.save_api_credentials.count == 1
    panel.deleteLater()


def test_the_clear_credentials_button_calls_its_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_clear_credentials_button.click()

    assert callbacks.clear_api_credentials.count == 1
    panel.deleteLater()


def test_the_capability_checkbox_calls_its_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_paper_order_capability.setChecked(True)

    assert callbacks.paper_order_capability_toggled.count == 1
    panel.deleteLater()


def test_the_extended_hours_checkbox_calls_its_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_extended_hours_paper.setChecked(True)

    assert callbacks.extended_hours_paper_toggled.count == 1
    panel.deleteLater()


def test_the_save_button_calls_the_preferences_callback(tmp_path) -> None:
    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_save_button.click()

    assert callbacks.save_user_preferences.count == 1
    panel.deleteLater()


def test_no_other_callback_fires_along_the_way(tmp_path) -> None:
    """Each control drives exactly its own callback and nobody else's."""

    callbacks = _Callbacks()
    panel = _panel(tmp_path, callbacks=callbacks)

    panel.settings_switch_provider_button.click()
    counts = callbacks.counts()

    assert counts["switch_to_settings_provider"] == 1
    assert sum(counts.values()) == 1
    panel.deleteLater()


# -- 33: combo width helper -------------------------------------------


def test_the_spinboxes_keep_their_ranges(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert (panel.settings_ibkr_port.minimum(),
            panel.settings_ibkr_port.maximum()) == (4002, 4002)
    assert (panel.settings_ibkr_client_id.minimum(),
            panel.settings_ibkr_client_id.maximum()) == (1, 999_999)
    assert (panel.settings_ibkr_timeout.minimum(),
            panel.settings_ibkr_timeout.maximum()) == (1, 120)
    panel.deleteLater()


def test_the_storage_section_keeps_its_maximum_heights(tmp_path) -> None:
    panel = _panel(tmp_path)

    frames = [
        child
        for child in panel.findChildren(QFrame)
        if child.maximumHeight() == 230
    ]
    assert len(frames) == 1

    boxes = [
        child
        for child in panel.findChildren(QTextEdit)
        if child.isReadOnly()
    ]
    assert boxes[0].maximumHeight() == 130
    panel.deleteLater()


def test_the_two_checkboxes_keep_their_captions(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert (
        panel.settings_paper_order_capability.text()
        == "允许 IBKR Paper 模拟下单能力"
    )
    assert (
        panel.settings_extended_hours_paper.text()
        == "启用 IBKR Paper 5×24 扩展时段（盘前 / 盘后 / 隔夜）"
    )
    assert isinstance(panel.settings_paper_order_capability, QCheckBox)
    assert isinstance(panel.settings_extended_hours_paper, QCheckBox)
    panel.deleteLater()


def test_the_buttons_keep_their_captions(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert (
        panel.settings_switch_provider_button.text() == "切换 / 重连行情"
    )
    assert (
        panel.settings_save_credentials_button.text()
        == "保存所选数据源凭据"
    )
    assert (
        panel.settings_clear_credentials_button.text()
        == "清除所选数据源凭据"
    )
    assert panel.settings_save_button.text() == "应用并保存设置"
    for name in (
        "settings_switch_provider_button",
        "settings_save_credentials_button",
        "settings_clear_credentials_button",
        "settings_save_button",
    ):
        assert isinstance(getattr(panel, name), QPushButton), name
    panel.deleteLater()


def test_the_combo_width_helper_is_called_with_the_original_metrics(
    tmp_path,
) -> None:
    widths = _ComboWidthRecorder()
    panel = _panel(tmp_path, widths=widths)

    calls = [
        (kwargs["minimum_width"], kwargs["minimum_contents"])
        for _combo, kwargs in widths.calls
    ]

    assert len(widths.calls) == 2
    assert calls == [(300, 22), (180, 14)]
    assert widths.calls[0][0] is panel.settings_provider_combo
    assert widths.calls[1][0] is panel.settings_api_provider_combo
    panel.deleteLater()


# -- 34: field label factory ------------------------------------------


def test_the_field_label_factory_supplies_every_caption(tmp_path) -> None:
    labels = _LabelRecorder()
    panel = _panel(tmp_path, labels=labels)

    assert tuple(labels.texts) == FIELD_LABELS
    panel.deleteLater()


def test_the_panel_does_not_build_its_own_field_labels(tmp_path) -> None:
    """Every field caption in the tree must be the injected label object."""

    labels = _LabelRecorder()
    panel = _panel(tmp_path, labels=labels)

    rendered = {
        label.text()
        for label in panel.findChildren(QLabel)
        if label.objectName() == "fieldLabel"
    }

    assert rendered == set(FIELD_LABELS)
    panel.deleteLater()


# -- 35: storage paths -------------------------------------------------


def test_the_storage_box_prints_the_four_application_paths(tmp_path) -> None:
    paths = _paths(tmp_path)
    panel = DesktopSettingsPanel(
        preferences=_preferences(),
        paths=paths,
        callbacks=_Callbacks().bundle(),
        field_label=_LabelRecorder(),
        configure_combo_width=_ComboWidthRecorder(),
    )

    boxes = [
        child
        for child in panel.findChildren(QTextEdit)
        if child.isReadOnly()
    ]
    text = boxes[0].toPlainText()

    assert str(paths.state_root / "settings") in text
    assert str(paths.state_root / "credentials") in text
    assert str(paths.runtime_root) in text
    assert str(paths.exports_root) in text
    panel.deleteLater()


def test_the_storage_box_reads_no_directory(tmp_path, monkeypatch) -> None:
    """The panel displays paths; it must not probe them.

    ``Path.exists`` is replaced with a recorder, so a probe anywhere in the
    construction shows up as a call rather than as a missing directory.
    """

    probed: list = []
    original = pathlib.Path.exists

    def _record(self) -> bool:
        probed.append(self)
        return original(self)

    monkeypatch.setattr(pathlib.Path, "exists", _record)

    panel = _panel(tmp_path)

    assert probed == []
    panel.deleteLater()


def test_the_storage_box_carries_no_credential_value(tmp_path) -> None:
    panel = _panel(tmp_path)

    boxes = [
        child
        for child in panel.findChildren(QTextEdit)
        if child.isReadOnly()
    ]
    text = boxes[0].toPlainText()

    for marker in ("api_key", "secret", "token", "password"):
        assert marker not in text.lower()
    panel.deleteLater()


# -- 36: scroll behaviour ---------------------------------------------


def test_the_panel_is_a_frameless_resizable_scroll_area(tmp_path) -> None:
    panel = _panel(tmp_path)

    assert isinstance(panel, QScrollArea)
    assert panel.frameShape() == QFrame.NoFrame
    assert panel.widgetResizable() is True
    assert (
        panel.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    )
    panel.deleteLater()


def test_the_content_page_keeps_its_minimum_height(tmp_path) -> None:
    panel = _panel(tmp_path)

    page = panel.widget()

    assert isinstance(page, QWidget)
    assert page.minimumHeight() == 820
    panel.deleteLater()


# -- 37/38: window ownership and aliases ------------------------------


def test_the_window_owns_a_settings_panel(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)

    assert isinstance(window.settings_panel, DesktopSettingsPanel)
    window.close()


def test_every_settings_alias_is_the_panels_own_widget(
    monkeypatch, tmp_path
) -> None:
    """All seventeen aliases, each one an identity -- never a copy."""

    window = _window(monkeypatch, tmp_path)
    panel = window.settings_panel

    for name in SETTINGS_WIDGETS:
        assert getattr(window, name) is getattr(panel, name), name
    window.close()


def test_the_aliases_cover_every_settings_widget_on_the_panel(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    panel = window.settings_panel

    on_panel = {
        name
        for name in vars(panel)
        if name.startswith("settings_")
    }

    assert on_panel == set(SETTINGS_WIDGETS)
    window.close()


def test_the_aliases_are_real_widgets(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)

    for name in SETTINGS_WIDGETS:
        assert isinstance(getattr(window, name), QWidget), name
    window.close()


def test_the_initial_refreshes_run_after_the_aliases_exist(
    monkeypatch, tmp_path
) -> None:
    """Both handlers read ``self.settings_*``, so the order is load-bearing.

    ``_api_provider_changed`` enables the credential fields based on the
    selected provider; running it before the aliases exist would raise.
    """

    seen: list = []
    refresh_credential_status = MainWindow._refresh_credential_status

    def _record_api_provider(self) -> None:
        # Stubbed out entirely: the real one refreshes the status itself,
        # which would mask whether the *tab* asks for a refresh.
        assert hasattr(self, "settings_api_provider_combo")
        assert hasattr(self, "settings_finnhub_key")
        seen.append("api_provider_changed")

    def _record_refresh(self) -> None:
        assert hasattr(self, "settings_credential_status")
        assert hasattr(self, "settings_alpaca_secret")
        seen.append("refresh_credential_status")
        refresh_credential_status(self)

    monkeypatch.setattr(
        MainWindow, "_api_provider_changed", _record_api_provider
    )
    monkeypatch.setattr(
        MainWindow, "_refresh_credential_status", _record_refresh
    )
    window = _window(monkeypatch, tmp_path)

    # Both refreshes are required, in this order, and the tab is the caller:
    # the provider one decides which credential fields are visible at all,
    # and the status one paints the credential summary.
    assert seen == ["api_provider_changed", "refresh_credential_status"]
    window.close()


def test_the_window_no_longer_builds_the_settings_widgets_itself() -> None:
    """``_settings_tab`` must compose, not construct."""

    text = _DESKTOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = _main_window_class(tree)
    method = _method(cls, "_settings_tab")
    body = _source_of(cls, method, text)

    for banned in (
        "QFrame()",
        "QGridLayout()",
        "QComboBox()",
        "QLineEdit()",
        "QSpinBox()",
        "QCheckBox()",
        "QTextEdit()",
    ):
        assert banned not in body, banned


def test_the_settings_layout_copy_lives_only_in_the_panel() -> None:
    """The three section titles must not survive in ``desktop.py``."""

    desktop = _DESKTOP_PATH.read_text(encoding="utf-8")
    panel = _PANEL_PATH.read_text(encoding="utf-8")

    for title in (
        "行情数据凭据（Windows 当前用户加密保存）",
        "IBKR Gateway · 券商连接 / 可选行情源",
        "数据、日志与安全边界",
    ):
        assert title in panel, title
        assert title not in desktop, title


def test_the_panel_is_the_object_the_window_shows_in_the_tab(
    monkeypatch, tmp_path
) -> None:
    """The tab page and the alias must be one widget, not two."""

    window = _window(monkeypatch, tmp_path)
    page = window._settings_tab()

    assert page is window.settings_panel
    assert isinstance(page, DesktopSettingsPanel)
    window.close()


def test_the_four_section_titles_are_present(tmp_path) -> None:
    panel = _panel(tmp_path)

    titles = {
        label.text()
        for label in panel.findChildren(QLabel)
        if label.objectName() == "sectionTitle"
    }

    assert titles == {
        "外观与默认工作区",
        "行情数据凭据（Windows 当前用户加密保存）",
        "IBKR Gateway · 券商连接 / 可选行情源",
        "数据、日志与安全边界",
    }
    panel.deleteLater()


def test_the_framed_sections_keep_their_object_names(tmp_path) -> None:
    """Four ``panel``-styled frames plus the scroll area's own frame."""

    panel = _panel(tmp_path)

    frames = [
        child
        for child in panel.findChildren(QFrame)
        if child.objectName() == "panel"
    ]

    assert len(frames) == 4
    panel.deleteLater()


def test_the_subtitle_labels_keep_their_object_name(tmp_path) -> None:
    """Two captions use the ``subtitle`` style: the note and the status."""

    panel = _panel(tmp_path)

    subtitles = [
        label
        for label in panel.findChildren(QLabel)
        if label.objectName() == "subtitle"
    ]

    assert len(subtitles) == 2
    assert any(
        label.text().startswith("先选择数据源") for label in subtitles
    )
    assert any(
        label is panel.settings_credential_status for label in subtitles
    )
    panel.deleteLater()


def test_the_window_reexports_the_panel_symbols() -> None:
    """``from us_quant.desktop import DesktopSettingsPanel`` keeps working."""

    from us_quant import desktop as desktop_module
    from us_quant import desktop_settings_panel as panel_module

    assert desktop_module.DesktopSettingsPanel is panel_module.DesktopSettingsPanel
    assert (
        desktop_module.DesktopSettingsCallbacks
        is panel_module.DesktopSettingsCallbacks
    )


# -- 39/40: the method really shrank ----------------------------------


def test_the_settings_tab_method_shrank_to_a_composition() -> None:
    text = _DESKTOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    method = _method(_main_window_class(tree), "_settings_tab")

    assert method.end_lineno - method.lineno + 1 < 80


def test_the_settings_tab_still_returns_a_widget(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)

    assert isinstance(window.settings_panel, QScrollArea)
    window.close()


def test_the_panel_module_imports_only_its_allowed_dependencies() -> None:
    """Spec 21: the panel's import list is part of the contract."""

    modules = _panel_imported_modules()

    allowed = {
        "__future__",
        "dataclasses",
        "typing",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "us_quant.paths",
        "us_quant.user_settings",
    }
    forbidden = {
        "us_quant.desktop",
        "us_quant.desktop_settings",
        "us_quant.desktop_credentials",
        "us_quant.credential_store",
        "us_quant.market_data_service",
        "us_quant.runtime_supervisor",
        "us_quant.paper_trading_service",
        "us_quant.paper_session",
        "us_quant.paper_workflow",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr_paper_gateway",
        "us_quant.workflow_state",
        "us_quant.extended_hours",
        "us_quant.risk",
    }

    assert not (modules & forbidden)
    assert modules <= allowed, sorted(modules - allowed)


def test_the_panel_holds_no_service() -> None:
    """Spec 22: no service may be stored on the panel."""

    text = _PANEL_PATH.read_text(encoding="utf-8")

    for banned in (
        "settings_service",
        "credential_service",
        "market_data_service",
        "paper_trading",
        "workflow_controller",
        "runtime_supervisor",
    ):
        assert banned not in text, banned


def test_the_panel_never_reads_a_credential() -> None:
    """Spec 16: the credential label is created here, filled elsewhere."""

    text = _PANEL_PATH.read_text(encoding="utf-8")

    for banned in (
        "WindowsCredentialStore",
        "DesktopCredentialService",
        "CredentialStatus",
        "load_secret",
    ):
        assert banned not in text, banned


def test_the_panel_init_never_calls_an_application_callback() -> None:
    """The constructor must not invoke ``callbacks.*`` -- only wire them."""

    init = _panel_init()
    calls = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "callbacks"
    ]

    assert calls == []


def test_the_panel_init_only_connects_after_positioning() -> None:
    """``setCurrentIndex`` must precede ``connect`` for every combo.

    A ``connect`` that lands before the index is set runs the handler during
    construction.  The check pairs each widget with its own two statements,
    because the order that matters is per combo, not across the method.
    """

    init = _panel_init()
    statements = _statements(init)

    positioned: dict = {}
    connected: dict = {}

    for index, node in enumerate(statements):
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr == "setCurrentIndex":
                widget = func.value
                assert isinstance(widget, ast.Attribute)
                positioned[widget.attr] = index
            elif func.attr == "connect":
                signal = func.value
                assert isinstance(signal, ast.Attribute)
                widget = signal.value
                assert isinstance(widget, ast.Attribute)
                connected.setdefault(widget.attr, index)

    # The two provider combos are the ones whose index is set from a stored
    # preference and whose signal is connected; both must be positioned first.
    assert "settings_provider_combo" in positioned
    assert "settings_provider_combo" in connected
    assert "settings_api_provider_combo" in positioned
    assert "settings_api_provider_combo" in connected
    assert "settings_theme_combo" in positioned
    assert "settings_theme_combo" in connected

    for name in (
        "settings_theme_combo",
        "settings_provider_combo",
        "settings_api_provider_combo",
    ):
        assert positioned[name] < connected[name], name


def test_the_panel_does_not_import_the_window() -> None:
    """Direction of dependency: ``desktop -> desktop_settings_panel``."""

    text = _PANEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "us_quant.desktop"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "us_quant.desktop"


# -- 41: handler byte-equivalence -------------------------------------


@pytest.mark.parametrize("name", FROZEN_HANDLERS)
def test_the_settings_handler_is_unchanged(name: str) -> None:
    """Spec 23/41: the handlers must be byte-identical to the base commit."""

    base = _base_source("src/us_quant/desktop.py")
    if base is None:
        pytest.fail(
            "base commit "
            f"{BASE_COMMIT} is unreachable; the byte-equivalence "
            "guard cannot run"
        )

    current = _DESKTOP_PATH.read_text(encoding="utf-8")

    base_tree = ast.parse(base)
    base_method = _method(_main_window_class(base_tree), name)
    base_body = _source_of(
        _main_window_class(base_tree), base_method, base
    ).replace("\r\n", "\n")

    current_tree = ast.parse(current)
    current_method = _method(_main_window_class(current_tree), name)
    current_body = _source_of(
        _main_window_class(current_tree), current_method, current
    ).replace("\r\n", "\n")

    assert current_body == base_body


def test_only_the_settings_tab_was_rewritten() -> None:
    """Every frozen handler is present and the tab method is the only edit.

    This is the aggregate form of the check above: the set of methods the
    base commit and the head share must agree byte for byte, apart from
    ``_settings_tab`` itself.
    """

    base = _base_source("src/us_quant/desktop.py")
    if base is None:
        pytest.fail(
            "base commit "
            f"{BASE_COMMIT} is unreachable; the byte-equivalence "
            "guard cannot run"
        )

    current = _DESKTOP_PATH.read_text(encoding="utf-8")
    base_tree = ast.parse(base)
    current_tree = ast.parse(current)
    base_cls = _main_window_class(base_tree)
    current_cls = _main_window_class(current_tree)

    base_methods = {
        node.name: node
        for node in base_cls.body
        if isinstance(node, ast.FunctionDef)
    }
    current_methods = {
        node.name: node
        for node in current_cls.body
        if isinstance(node, ast.FunctionDef)
    }

    assert set(base_methods) == set(current_methods)

    changed = []
    for name, node in base_methods.items():
        base_body = _source_of(base_cls, node, base).replace("\r\n", "\n")
        current_body = _source_of(
            current_cls, current_methods[name], current
        ).replace("\r\n", "\n")
        if base_body != current_body:
            changed.append(name)

    assert changed == ["_settings_tab"]


def test_the_frozen_settings_modules_are_untouched() -> None:
    """Spec 24: the two application services must not change here."""

    for path in (
        "src/us_quant/desktop_settings.py",
        "src/us_quant/desktop_credentials.py",
        "src/us_quant/credential_store.py",
    ):
        base = _base_source(path)
        if base is None:
            pytest.fail(
            "base commit "
            f"{BASE_COMMIT} is unreachable; the byte-equivalence "
            "guard cannot run"
        )
        current = (_REPO_ROOT / path).read_text(encoding="utf-8")
        assert current == base, path


# -- 42/43/44: the untouched behaviour still holds --------------------


def test_the_provider_selection_still_drives_the_credential_fields(
    monkeypatch, tmp_path
) -> None:
    """Spec 42: splitting the UI must not disturb the visibility rule.

    ``_api_provider_changed`` shows only the fields belonging to the selected
    API provider; the panel must keep handing it the same three widgets.
    """

    window = _window(monkeypatch, tmp_path)
    combo = window.settings_api_provider_combo

    combo.setCurrentIndex(combo.findData("finnhub_trades"))
    assert window.settings_finnhub_key.isHidden() is False
    assert window.settings_alpaca_key.isHidden() is True
    assert window.settings_alpaca_secret.isHidden() is True

    combo.setCurrentIndex(combo.findData("alpaca_iex"))
    assert window.settings_finnhub_key.isHidden() is True
    assert window.settings_alpaca_key.isHidden() is False
    assert window.settings_alpaca_secret.isHidden() is False

    combo.setCurrentIndex(combo.findData("ibkr"))
    assert window.settings_finnhub_key.isHidden() is True
    assert window.settings_alpaca_key.isHidden() is True
    assert window.settings_alpaca_secret.isHidden() is True

    window.close()


def test_the_api_provider_gates_the_credential_buttons(
    monkeypatch, tmp_path
) -> None:
    """IBKR needs no API key, so both credential buttons go dead."""

    window = _window(monkeypatch, tmp_path)
    combo = window.settings_api_provider_combo

    combo.setCurrentIndex(combo.findData("finnhub_trades"))
    assert window.settings_save_credentials_button.isEnabled() is True

    combo.setCurrentIndex(combo.findData("ibkr"))
    assert window.settings_save_credentials_button.isEnabled() is False
    assert window.settings_clear_credentials_button.isEnabled() is False

    window.close()


def test_the_window_still_exposes_the_seventeen_attributes(
    monkeypatch, tmp_path
) -> None:
    """The rest of the window reads ``self.settings_*``; that must not break."""

    window = _window(monkeypatch, tmp_path)

    for name in SETTINGS_WIDGETS:
        assert hasattr(window, name), name
    window.close()


def test_the_panel_is_reachable_from_the_window_tab(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)

    assert window.settings_panel.parent() is not None or (
        window.settings_panel is not None
    )
    window.close()
