"""Coverage for the desktop settings transaction, without a window.

``MainWindow._save_user_preferences`` used to be the only place the
settings transaction existed, so the only way to test it was to build a
window, poke widgets and assert on the aftermath.  The transaction now
lives in :mod:`us_quant.desktop_settings`, and these tests exercise it
directly: fakes for the store and the market data service, no
``QApplication``, no widgets, no thread.

The order is the contract, not an implementation detail::

    validate  ->  derive IBKR config  ->  preflight  ->  persist
                                                      ->  apply to runtime

Two failure modes have to stay inert and stay *distinguishable*:

* a live stream refusing the connection change must not have written the
  file (otherwise the operator is told "not saved" while the disk holds
  the new client id);
* a failed write must not have moved the runtime (otherwise the next
  stream connects with values the settings file does not contain).

So the tests assert on the call *log*, not only on the return value -- a
service that saved first and checked afterwards would still return a
correct-looking commit.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from us_quant.config import load_config
from us_quant.desktop_settings import (
    DesktopSettingsCommit,
    DesktopSettingsService,
    MarketDataConfigPort,
    ibkr_config_from_preferences,
)
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.market_data_service import MarketDataStreamActive
from us_quant.user_settings import (
    PAPER_GATEWAY_PORT,
    UserPreferences,
    UserSettingsError,
)


# The service tests above need no Qt at all -- that is the point of the
# extraction.  The wiring tests at the bottom do construct a ``MainWindow``,
# and constructing a widget without a ``QApplication`` aborts the process
# rather than raising, so the instance is created once here.
_APP = QApplication.instance() or QApplication([])


_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop_settings.py"
_DESKTOP_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop.py"


def _preferences(**overrides) -> UserPreferences:
    """Valid preferences, with ``overrides`` applied.

    Derived from the real defaults rather than hard-coded so the fixture
    cannot drift away from what ``UserPreferences`` actually accepts.
    """

    values = {
        "theme": "dark",
        "market_provider": "finnhub_trades",
        "ibkr_host": "localhost",
        "ibkr_port": PAPER_GATEWAY_PORT,
        "ibkr_client_id": 88,
        "connection_timeout_seconds": 27.0,
        "paper_order_capability_enabled": False,
        "extended_hours_paper_enabled": False,
    }
    values.update(overrides)
    return UserPreferences(**values)


def _app_config():
    """A real ``AppConfig`` -- the transaction must not need a fake one."""

    return load_config(_REPO_ROOT / "configs" / "paper.toml")


class _FakeMarketData:
    """Records what the service asked of it, in order.

    ``refuse=True`` makes the preflight raise the way a live stream does.
    """

    def __init__(self, config, *, refuse: bool = False, log: list) -> None:
        self.config = config
        self._refuse = refuse
        self._log = log
        self.checked: list[IBKRConnectionConfig] = []
        self.updated: list[IBKRConnectionConfig] = []

    def ensure_config_update_allowed(self, config) -> None:
        self._log.append("ensure")
        self.checked.append(config)
        if self._refuse:
            raise MarketDataStreamActive(
                "cannot change the IBKR connection config while a market "
                "data stream is active: stop it first"
            )

    def update_config(self, config) -> None:
        self._log.append("update")
        self.updated.append(config)
        self.config = config


class _FakeStore:
    """A store that records the write and hands back its own copy.

    ``returned`` is a *different object* from the caller's argument --
    exactly what the real store does, since ``save`` re-validates.  That
    is what lets the tests prove ``commit`` reports the store's value
    rather than echoing back the argument it was given.
    """

    def __init__(self, *, fail: bool = False, log: list) -> None:
        self._fail = fail
        self._log = log
        self.returned: UserPreferences | None = None

    def save(self, preferences: UserPreferences) -> UserPreferences:
        self._log.append("save")
        if self._fail:
            raise UserSettingsError("unable to save preferences: disk full")
        self.returned = preferences.validated()
        return self.returned


def _service(
    log: list | None = None, **store_kwargs
) -> tuple[DesktopSettingsService, _FakeStore]:
    """A service over a fake store.

    ``log`` must be the *same* list the market data fake records into, or
    the two halves of the transaction land in different logs and the
    ordering assertions read a half-empty sequence.
    """

    store = _FakeStore(log=log if log is not None else [], **store_kwargs)
    return DesktopSettingsService(store), store


# -- the IBKR mapping --------------------------------------------------


def test_ibkr_config_is_derived_from_preferences() -> None:
    """The attachment's worked example, asserted field by field.

    Compared as a whole config *and* per field: a whole-object equality
    alone would pass if both sides were wrong in the same way, and a
    field-by-field check alone would miss an extra field appearing.
    """

    preferences = _preferences(
        ibkr_host="localhost",
        ibkr_port=4002,
        ibkr_client_id=88,
        connection_timeout_seconds=27.0,
        paper_order_capability_enabled=True,
    )

    config = ibkr_config_from_preferences(preferences)

    assert config == IBKRConnectionConfig(
        host="localhost",
        port=4002,
        client_id=88,
        api_read_only=True,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=27.0,
    )
    assert config.host == "localhost"
    assert config.port == 4002
    assert config.client_id == 88
    assert config.connection_timeout_seconds == 27.0


def test_paper_order_capability_never_enables_broker_submission() -> None:
    """Two different safety layers, and the weaker one must not imply the
    stronger.

    ``paper_order_capability_enabled`` says the operator may be *offered*
    Paper order controls.  Broker submission is turned on by the dedicated
    order-channel path with its own config, never by this mapping.
    """

    for capability in (True, False):
        for extended in (True, False):
            config = ibkr_config_from_preferences(
                _preferences(
                    paper_order_capability_enabled=capability,
                    extended_hours_paper_enabled=extended,
                )
            )
            assert config.api_read_only is True
            assert config.paper_order_submission_enabled is False


def test_the_derived_config_is_always_read_only_and_loopback() -> None:
    """The two invariants the desktop config must never lose."""

    config = ibkr_config_from_preferences(_preferences())
    assert config.api_read_only is True
    assert config.paper_order_submission_enabled is False
    assert config.host in {"127.0.0.1", "localhost", "::1"}
    assert config.port == PAPER_GATEWAY_PORT


def test_the_mapping_normalises_the_host() -> None:
    """The store pins the host, so the mapping sees a normalised one.

    ``UserPreferences.validated()`` lowercases and strips; the mapping
    itself must not re-derive that, or start-up and a later save would
    disagree about what ``LocalHost `` means.
    """

    config = ibkr_config_from_preferences(
        _preferences(ibkr_host="LOCALHOST").validated()
    )
    assert config.host == "localhost"


def test_the_mapping_copies_every_connection_field_verbatim() -> None:
    """The mapping transports; it does not re-pin.

    Validation belongs to ``UserPreferences.validated()`` -- the store runs
    it, and the service runs it again before deriving.  If the mapping
    substituted its own constants the two call sites could silently
    disagree, and a test written against the *validated* defaults would
    never notice, because ``PAPER_GATEWAY_PORT`` is what ``validated()``
    pins the port to anyway.  So the unvalidated values go in directly and
    must come out unchanged.
    """

    preferences = UserPreferences(
        ibkr_host="127.0.0.1",
        ibkr_port=4003,
        ibkr_client_id=123_456,
        connection_timeout_seconds=99.0,
    )

    config = ibkr_config_from_preferences(preferences)

    assert config.port == 4003
    assert config.client_id == 123_456
    assert config.connection_timeout_seconds == 99.0
    assert config.host == "127.0.0.1"


# -- the successful transaction ----------------------------------------


def test_a_successful_commit_runs_preflight_then_save_then_apply() -> None:
    """The order is the invariant this whole extraction exists to keep.

    Saving before the preflight would leave the file written while the
    runtime refused the change; applying before the save would leave the
    runtime moved while the write failed.
    """

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)
    preferences = _preferences(ibkr_client_id=88)

    commit = service.commit(
        preferences,
        current_config=current,
        market_data=market_data,
    )

    assert log == ["ensure", "save", "update"]


def test_a_successful_commit_reports_the_stores_own_copy() -> None:
    """``commit.preferences`` is what was written, not the argument.

    The store re-validates and returns its normalised copy; reporting the
    caller's object instead would hide a store that rewrote the values.
    """

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)
    preferences = _preferences(ibkr_client_id=88)

    commit = service.commit(
        preferences,
        current_config=current,
        market_data=market_data,
    )

    assert store.returned is not None
    assert commit.preferences is store.returned
    assert commit.preferences is not preferences
    assert commit.preferences == preferences.validated()


def test_a_successful_commit_returns_the_applied_config() -> None:
    """The returned config and the service must describe the same thing."""

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)
    preferences = _preferences(ibkr_client_id=88)

    commit = service.commit(
        preferences,
        current_config=current,
        market_data=market_data,
    )

    assert commit.config.ibkr == ibkr_config_from_preferences(preferences)
    assert commit.config.ibkr == market_data.config
    assert market_data.updated == [commit.config.ibkr]


def test_a_successful_commit_leaves_the_other_config_fields_alone() -> None:
    """Only ``ibkr`` moves; the rest of ``AppConfig`` is carried over."""

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    commit = service.commit(
        _preferences(ibkr_client_id=88),
        current_config=current,
        market_data=market_data,
    )

    assert commit.config is not current
    assert dataclasses.replace(commit.config, ibkr=current.ibkr) == current


def test_the_passed_in_config_is_not_mutated() -> None:
    """``AppConfig`` is frozen and the transaction must not need that
    relaxed.

    Asserted on the *original object* rather than on a copy: a caller
    holding a reference to the previous config must not see it change.
    """

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    before = current.ibkr
    market_data = _FakeMarketData(current.ibkr, log=log)

    commit = service.commit(
        _preferences(ibkr_client_id=88),
        current_config=current,
        market_data=market_data,
    )

    assert current.ibkr is before
    assert current.ibkr.client_id == before.client_id
    assert commit.config.ibkr.client_id != before.client_id


def test_the_config_passed_to_the_preflight_is_the_one_applied() -> None:
    """The check and the apply must be about the same config.

    Checking one config and applying another is exactly the bug that
    would let a refused change through.
    """

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    commit = service.commit(
        _preferences(ibkr_client_id=88),
        current_config=current,
        market_data=market_data,
    )

    assert market_data.checked == market_data.updated
    assert market_data.checked == [commit.config.ibkr]


# -- a live stream refuses the connection change -----------------------


def test_a_refused_change_never_writes_the_file() -> None:
    """The whole reason the preflight comes first.

    The store is built to fail loudly if it is reached, so "zero saves"
    is proven by the absence of an exception rather than by a counter
    that could be forgotten.
    """

    log: list = []
    service, store = _service()
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, refuse=True, log=log)

    with pytest.raises(MarketDataStreamActive) as excinfo:
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert excinfo.type is MarketDataStreamActive
    assert log == ["ensure"]
    assert store.returned is None


def test_a_refused_change_leaves_the_runtime_untouched() -> None:
    log: list = []
    service, _ = _service()
    current = _app_config()
    before = current.ibkr
    market_data = _FakeMarketData(current.ibkr, refuse=True, log=log)

    with pytest.raises(MarketDataStreamActive):
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert market_data.updated == []
    assert market_data.config is before
    assert current.ibkr is before


def test_a_refused_change_is_not_translated_into_a_settings_error() -> None:
    """The two failure modes must stay distinguishable to the caller.

    ``MainWindow`` catches both and shows a different message for each; a
    service that converted one into the other would still pass a
    ``pytest.raises(RuntimeError)`` check while making the operator's
    warning wrong.
    """

    log: list = []
    service, _ = _service()
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, refuse=True, log=log)

    with pytest.raises(MarketDataStreamActive) as excinfo:
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert not isinstance(excinfo.value, UserSettingsError)


def test_an_unchanged_config_is_not_refused_while_streaming() -> None:
    """Re-saving unrelated settings must survive a live stream.

    Otherwise the operator could not save the theme purely because a
    stream happened to be running.  The refusal only applies to a
    *change*.
    """

    preferences = _preferences()
    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(
        ibkr_config_from_preferences(preferences), log=log
    )

    commit = service.commit(
        preferences,
        current_config=current,
        market_data=market_data,
    )

    # The check and the write still happen; only the apply is skipped.
    assert log == ["ensure", "save"]
    assert market_data.updated == []
    assert market_data.checked == [
        ibkr_config_from_preferences(preferences)
    ]
    assert commit.config.ibkr == market_data.config


def test_an_unchanged_config_still_reports_the_stores_copy() -> None:
    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    market_data = _FakeMarketData(
        ibkr_config_from_preferences(_preferences()), log=log
    )

    commit = service.commit(
        _preferences(theme="light"),
        current_config=current,
        market_data=market_data,
    )

    assert commit.preferences is store.returned
    assert commit.preferences.theme == "light"


# -- the disk write fails ----------------------------------------------


def test_a_failed_write_never_moves_the_runtime() -> None:
    """The write is the commit point.

    The preflight succeeded, so the only thing standing between the
    operator and a silent divergence is that the apply comes after the
    write.
    """

    log: list = []
    service, _ = _service(log=log, fail=True)
    current = _app_config()
    before = current.ibkr
    market_data = _FakeMarketData(current.ibkr, log=log)

    with pytest.raises(UserSettingsError) as excinfo:
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert excinfo.type is UserSettingsError
    assert log == ["ensure", "save"]
    assert market_data.updated == []
    assert market_data.config is before
    assert current.ibkr is before


def test_a_failed_write_is_reported_not_swallowed() -> None:
    """The caller has to be able to tell the operator."""

    log: list = []
    service, _ = _service(log=log, fail=True)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    with pytest.raises(UserSettingsError) as excinfo:
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert "disk full" in str(excinfo.value)


def test_a_failed_write_is_the_write_and_not_the_preflight() -> None:
    """Separates the two failure modes.

    The previous test would also pass if the refusal had come from a live
    stream; this one has no refusal at all, so the write is provably what
    rejected it.
    """

    log: list = []
    service, _ = _service(log=log, fail=True)
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    with pytest.raises(UserSettingsError):
        service.commit(
            _preferences(ibkr_client_id=88),
            current_config=current,
            market_data=market_data,
        )

    assert market_data.checked != []
    assert log.index("ensure") < log.index("save")


# -- no market data service (start-up and pure service use) ------------


def test_committing_without_a_market_data_service_still_works() -> None:
    """``market_data=None`` is the start-up case, not an error.

    The settings transaction must not require a service to exist: the
    window builds one only later, and the pure-service path has none.
    """

    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    current = _app_config()
    preferences = _preferences(ibkr_client_id=88)

    commit = service.commit(
        preferences,
        current_config=current,
        market_data=None,
    )

    assert log == ["save"]
    assert commit.preferences is store.returned
    assert commit.config.ibkr == ibkr_config_from_preferences(preferences)
    assert commit.config.ibkr.api_read_only is True
    assert commit.config.ibkr.paper_order_submission_enabled is False


def test_committing_without_a_service_still_validates() -> None:
    """Skipping the service must not skip the validation."""

    log: list = []
    service, _ = _service()
    current = _app_config()

    with pytest.raises(UserSettingsError):
        service.commit(
            _preferences(ibkr_port=4001),
            current_config=current,
            market_data=None,
        )

    assert log == []


def test_committing_without_a_service_still_reports_write_failures() -> None:
    log: list = []
    service, _ = _service(log=log, fail=True)
    current = _app_config()

    with pytest.raises(UserSettingsError):
        service.commit(
            _preferences(),
            current_config=current,
            market_data=None,
        )

    assert log == ["save"]


# -- invalid preferences -----------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"ibkr_port": 4001}, id="port-not-4002"),
        pytest.param({"ibkr_host": "10.0.0.7"}, id="remote-host"),
        pytest.param({"ibkr_client_id": 0}, id="client-id-zero"),
    ],
)
def test_invalid_preferences_are_rejected_before_anything_happens(
    overrides: dict,
) -> None:
    """Validation is first, and it is the service that does it.

    The port and the client id pass ``IBKRConnectionConfig``'s own checks,
    so the rejection has to come from the preferences -- which is what
    keeps the persisted schema the single source of truth.
    """

    log: list = []
    service, store = _service()
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    with pytest.raises(UserSettingsError):
        service.commit(
            _preferences(**overrides),
            current_config=current,
            market_data=market_data,
        )

    assert log == []
    assert store.returned is None
    assert market_data.updated == []


def test_invalid_preferences_are_not_a_value_error() -> None:
    """A remote host would trip ``IBKRConnectionConfig``'s own guard with a
    ``ValueError``.  The preferences check runs first, so the caller sees
    the error type it is documented to catch.
    """

    log: list = []
    service, _ = _service()
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    with pytest.raises(UserSettingsError) as excinfo:
        service.commit(
            _preferences(ibkr_host="10.0.0.7"),
            current_config=current,
            market_data=market_data,
        )

    assert not isinstance(excinfo.value, ValueError)


def test_a_valid_but_unnormalised_host_is_normalised_before_use() -> None:
    """``validate`` comes before the mapping, so the config is built from
    the normalised values rather than the raw ones.
    """

    log: list = []
    service, _ = _service()
    current = _app_config()
    market_data = _FakeMarketData(current.ibkr, log=log)

    commit = service.commit(
        _preferences(ibkr_host="  LocalHost  "),
        current_config=current,
        market_data=market_data,
    )

    assert commit.config.ibkr.host == "localhost"
    assert market_data.checked == [commit.config.ibkr]


# -- the module's own boundary ----------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifier_names(path: Path) -> set[str]:
    """Every name and attribute the module mentions.

    Read from the AST rather than the text, so a docstring that *names* a
    forbidden symbol does not trip the guard.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_the_module_dependency_surface_is_exactly_what_was_agreed() -> None:
    """Pinned as an equality, not as a blacklist.

    A blacklist would let the module grow a new dependency quietly; this
    makes any addition a deliberate edit to the test.
    """

    assert _imported_modules(_MODULE_PATH) == {
        "__future__",
        "dataclasses",
        "typing",
        "us_quant.config",
        "us_quant.ibkr",
        "us_quant.user_settings",
    }


@pytest.mark.parametrize(
    "module",
    [
        "PySide6",
        "us_quant.desktop",
        "us_quant.desktop_widgets",
        "us_quant.desktop_workers",
        "us_quant.credential_store",
        "us_quant.runtime_supervisor",
        "us_quant.paper_trading_service",
        "us_quant.paper_session",
        "us_quant.paper_workflow",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr_paper_gateway",
        "us_quant.workflow_state",
        "us_quant.auto_quant",
        "us_quant.ibkr_stream",
        "us_quant.alpaca_stream",
        "us_quant.finnhub_stream",
    ],
)
def test_the_module_imports_nothing_it_was_told_not_to(module: str) -> None:
    imported = _imported_modules(_MODULE_PATH)
    assert module not in imported
    assert not any(
        name.startswith(f"{module}.") for name in imported
    ), f"imports {module} through a submodule"


@pytest.mark.parametrize(
    "name",
    [
        "QMessageBox",
        "QWidget",
        "QMainWindow",
        "QApplication",
        "QThread",
        "MainWindow",
        "MarketDataService",
        "RuntimeSupervisor",
        "ExecutionLease",
        "AutoQuantEngine",
        "PaperSession",
        "CredentialStore",
    ],
)
def test_the_module_mentions_no_ui_or_paper_symbol(name: str) -> None:
    """It is an application service, not a UI controller.

    ``MarketDataService`` is in the list deliberately: the module talks to
    the service through :class:`MarketDataConfigPort` instead, so it never
    has to import the concrete class.
    """

    assert name not in _identifier_names(_MODULE_PATH)


def test_the_commit_is_a_frozen_slots_dataclass() -> None:
    """``frozen`` so a commit cannot be edited after the fact; ``slots``
    so a typo'd attribute is an error rather than a silent extra field.
    """

    params = DesktopSettingsCommit.__dataclass_params__
    assert params.frozen is True
    assert DesktopSettingsCommit.__slots__ == (
        "preferences",
        "config",
    )

    commit = DesktopSettingsCommit(
        preferences=_preferences(),
        config=_app_config(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        commit.config = _app_config()


def test_the_market_data_port_is_a_protocol_with_three_members() -> None:
    """The service's surface is the point: config, check, apply.

    ``ensure_config_update_allowed`` and ``update_config`` were moved out
    of ``update_config``'s body precisely so a caller can check without
    applying; the port records that.
    """

    assert getattr(MarketDataConfigPort, "_is_protocol", False) is True
    assert set(MarketDataConfigPort.__annotations__) == {"config"}
    assert callable(MarketDataConfigPort.ensure_config_update_allowed)
    assert callable(MarketDataConfigPort.update_config)


def test_the_service_holds_only_the_store() -> None:
    log: list = []
    store = _FakeStore(log=log)
    service = DesktopSettingsService(store)
    assert service.store is store


def test_commit_takes_its_context_by_keyword() -> None:
    """``current_config`` and ``market_data`` are keyword-only.

    Positional calls would make it easy to pass the service where the
    config belongs -- and both are required context, so neither may be
    defaulted away silently.
    """

    parameters = inspect.signature(DesktopSettingsService.commit).parameters
    assert parameters["current_config"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert parameters["market_data"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert parameters["market_data"].default is inspect.Parameter.empty
    assert parameters["current_config"].default is inspect.Parameter.empty


def test_the_mapping_is_a_plain_function_not_a_method() -> None:
    """Start-up uses it before a service exists, so it cannot be one."""

    assert inspect.isfunction(ibkr_config_from_preferences)
    assert list(
        inspect.signature(ibkr_config_from_preferences).parameters
    ) == ["preferences"]


# -- the desktop wiring ------------------------------------------------


def _window_with_tmp_state(monkeypatch, tmp_path):
    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    from us_quant.desktop import MainWindow

    return MainWindow()


def test_main_window_owns_a_desktop_settings_service(
    monkeypatch,
    tmp_path,
) -> None:
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        assert isinstance(window.settings_service, DesktopSettingsService)
        # It wraps the window's own store -- a second store would mean two
        # views of the same file.
        assert window.settings_service.store is window.preferences_store
    finally:
        window.deleteLater()


def test_main_window_no_longer_carries_the_transaction() -> None:
    """No duplicate implementation is allowed to survive the extraction.

    Checked as attribute *absence* rather than as a delegate, because a
    delegate would still be a place for the logic to grow back.
    """

    from us_quant.desktop import MainWindow

    assert not hasattr(MainWindow, "_ibkr_config_from_preferences")
    assert not hasattr(MainWindow, "_apply_preferences_to_config")


def test_startup_uses_the_same_mapping_as_a_later_save() -> None:
    """Otherwise start-up and a save drift into two sets of rules."""

    from us_quant.desktop import MainWindow

    source = inspect.getsource(MainWindow.__init__)
    assert "ibkr_config_from_preferences(" in source
    assert "IBKRConnectionConfig(" not in source


@pytest.mark.parametrize(
    "call",
    [
        "preferences_store.save(",
        "ensure_config_update_allowed(",
        "market_data_service.update_config(",
    ],
)
def test_the_save_adapter_does_not_run_the_transaction_itself(
    call: str,
) -> None:
    """``_save_user_preferences`` is a UI adapter now.

    It reads widgets and reports failures; the three transaction calls
    live in ``desktop_settings.py`` and nowhere else.  Scoped to this
    method so the module's own import and construction of the store are
    not flagged.
    """

    from us_quant.desktop import MainWindow

    source = inspect.getsource(MainWindow._save_user_preferences)
    assert call not in source, f"_save_user_preferences still calls {call}"


def test_the_save_adapter_does_go_through_the_service() -> None:
    """Belt and braces: the absence above is not achieved by deleting the
    save path."""

    from us_quant.desktop import MainWindow

    source = inspect.getsource(MainWindow._save_user_preferences)
    assert "settings_service.commit(" in source


@pytest.mark.parametrize(
    "call",
    [
        "preferences_store.save(",
        "ensure_config_update_allowed(",
        "market_data_service.update_config(",
    ],
)
def test_the_transaction_calls_are_absent_from_desktop(call: str) -> None:
    """The module boundary, asserted on the file that was refactored.

    ``market_data_service.py`` still *defines* the check and the apply --
    that is the other side of the boundary -- but ``desktop.py`` must no
    longer call them directly.
    """

    assert call not in _DESKTOP_PATH.read_text(encoding="utf-8")


def test_the_transaction_lives_in_the_settings_module() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "ensure_config_update_allowed(" in source
    assert "update_config(" in source
    assert "self.store.save(" in source
