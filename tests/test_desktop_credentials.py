"""Coverage for credential management, without a window or a real DPAPI.

The behaviour under test is the part of the Settings tab that was never
about widgets: which secret names a provider maps to, that a half-filled
Alpaca pair is refused rather than half-saved, that reporting "saved"
never decrypts a blob, and that an environment variable wins over the
store one credential at a time.

Two of those are easy to get wrong in a way that still *looks* right:

* ``status()`` must answer from ``has_secret`` only.  A store that raises
  on ``load_secret`` is used deliberately -- a status line that decrypted
  would turn "saved" into an error for a blob belonging to another
  Windows user;
* ``save_provider`` for Alpaca must write both halves or neither.  The
  fakes count calls, so a half-write is visible as a call log rather than
  as a state that merely looks unconfigured.

The store is a fake throughout; no DPAPI, no ``QApplication``, no thread.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from us_quant.credential_store import CredentialStoreError
from us_quant.desktop_credentials import (
    API_KEY_PROVIDERS,
    ENVIRONMENT_NAMES,
    PROVIDER_ALPACA_IEX,
    PROVIDER_FINNHUB_TRADES,
    PROVIDER_IBKR,
    PROVIDER_IBKR_EXTENDED,
    STREAM_CREDENTIAL_SOURCES,
    CredentialStatus,
    DesktopCredentialService,
    StreamCredentials,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_credentials.py"
)
_STORE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "credential_store.py"
)


class _FakeStore:
    """Records every call; can be told to fail a specific operation."""

    def __init__(
        self,
        saved: dict[str, str] | None = None,
        *,
        load_error: Exception | None = None,
    ) -> None:
        self.saved = dict(saved or {})
        self.load_error = load_error
        self.calls: list[tuple[str, str]] = []
        self.loads: list[str] = []

    def save_secret(self, name: str, secret: str) -> object:
        self.calls.append(("save_secret", name))
        self.saved[name] = secret
        return object()

    def load_secret(self, name: str) -> str | None:
        self.calls.append(("load_secret", name))
        self.loads.append(name)
        if self.load_error is not None:
            raise self.load_error
        return self.saved.get(name)

    def delete_secret(self, name: str) -> None:
        self.calls.append(("delete_secret", name))
        self.saved.pop(name, None)

    def has_secret(self, name: str) -> bool:
        self.calls.append(("has_secret", name))
        return name in self.saved


class _NoDecryptStore(_FakeStore):
    """Proves ``status()`` does not decrypt: loading is an error."""

    def load_secret(self, name: str) -> str | None:
        raise AssertionError(
            f"status() must not decrypt {name!r}"
        )


def _service(store: _FakeStore) -> DesktopCredentialService:
    return DesktopCredentialService(store)


# -- save: the provider to secret-name mapping -------------------------


def test_saving_finnhub_writes_only_the_finnhub_key() -> None:
    store = _FakeStore()
    _service(store).save_provider(
        PROVIDER_FINNHUB_TRADES, api_key="FIN-123"
    )

    assert store.calls == [("save_secret", "finnhub_api_key")]
    assert store.saved == {"finnhub_api_key": "FIN-123"}


def test_saving_alpaca_writes_both_halves_in_a_fixed_order() -> None:
    """Order is pinned: key first, then secret, exactly as the UI did."""

    store = _FakeStore()
    _service(store).save_provider(
        PROVIDER_ALPACA_IEX, api_key="AK", api_secret="AS"
    )

    assert store.calls == [
        ("save_secret", "alpaca_api_key"),
        ("save_secret", "alpaca_api_secret"),
    ]
    assert store.saved == {
        "alpaca_api_key": "AK",
        "alpaca_api_secret": "AS",
    }


def test_saving_alpaca_never_touches_the_finnhub_key() -> None:
    store = _FakeStore({"finnhub_api_key": "keep-me"})
    _service(store).save_provider(
        PROVIDER_ALPACA_IEX, api_key="AK", api_secret="AS"
    )

    assert store.saved["finnhub_api_key"] == "keep-me"


@pytest.mark.parametrize(
    ("api_key", "api_secret"),
    [("AK", ""), ("", "AS"), ("", "")],
)
def test_a_half_filled_alpaca_pair_is_refused_without_writing(
    api_key: str, api_secret: str
) -> None:
    """Defence in depth for a non-UI caller.

    The window already refuses this, but a service that trusted its
    caller would let anything else half-configure Alpaca -- which looks
    configured in the status line and then fails at stream start.
    """

    store = _FakeStore()
    with pytest.raises(ValueError):
        _service(store).save_provider(
            PROVIDER_ALPACA_IEX,
            api_key=api_key,
            api_secret=api_secret,
        )

    assert store.calls == []
    assert store.saved == {}


def test_saving_an_empty_finnhub_key_is_refused_without_writing() -> None:
    store = _FakeStore()
    with pytest.raises(ValueError):
        _service(store).save_provider(
            PROVIDER_FINNHUB_TRADES, api_key=""
        )

    assert store.calls == []


@pytest.mark.parametrize(
    "provider", [PROVIDER_IBKR, PROVIDER_IBKR_EXTENDED]
)
def test_ibkr_writes_no_credential_file(provider: str) -> None:
    """IBKR uses host/port/client id.  Fail closed, do not guess."""

    store = _FakeStore()
    with pytest.raises(ValueError):
        _service(store).save_provider(
            provider, api_key="AK", api_secret="AS"
        )

    assert store.calls == []


# -- clear -------------------------------------------------------------


def test_clearing_finnhub_deletes_only_the_finnhub_key() -> None:
    store = _FakeStore(
        {"finnhub_api_key": "old", "alpaca_api_key": "keep"}
    )
    _service(store).clear_provider(PROVIDER_FINNHUB_TRADES)

    assert store.calls == [("delete_secret", "finnhub_api_key")]
    assert store.saved == {"alpaca_api_key": "keep"}


def test_clearing_alpaca_deletes_both_halves() -> None:
    store = _FakeStore(
        {
            "alpaca_api_key": "AK",
            "alpaca_api_secret": "AS",
            "finnhub_api_key": "keep",
        }
    )
    _service(store).clear_provider(PROVIDER_ALPACA_IEX)

    assert store.calls == [
        ("delete_secret", "alpaca_api_key"),
        ("delete_secret", "alpaca_api_secret"),
    ]
    assert store.saved == {"finnhub_api_key": "keep"}


@pytest.mark.parametrize(
    "provider", [PROVIDER_IBKR, PROVIDER_IBKR_EXTENDED]
)
def test_clearing_ibkr_deletes_nothing(provider: str) -> None:
    """Not "delete whatever exists" -- it deletes nothing, and says so."""

    store = _FakeStore({"finnhub_api_key": "keep"})
    with pytest.raises(ValueError):
        _service(store).clear_provider(provider)

    assert store.calls == []
    assert store.saved == {"finnhub_api_key": "keep"}


# -- status: presence only, never a value ------------------------------


def test_status_reports_presence_for_finnhub() -> None:
    saved = _service(_FakeStore({"finnhub_api_key": "x"})).status(
        PROVIDER_FINNHUB_TRADES
    )
    missing = _service(_FakeStore()).status(PROVIDER_FINNHUB_TRADES)

    assert saved == CredentialStatus(
        provider=PROVIDER_FINNHUB_TRADES,
        requires_api_key=True,
        api_key_saved=True,
        api_secret_saved=False,
    )
    assert missing.api_key_saved is False


def test_status_reports_each_alpaca_half_separately() -> None:
    """A half-saved Alpaca pair must be visible as half-saved."""

    half = _service(
        _FakeStore({"alpaca_api_key": "AK"})
    ).status(PROVIDER_ALPACA_IEX)

    assert half.api_key_saved is True
    assert half.api_secret_saved is False


@pytest.mark.parametrize(
    "provider", [PROVIDER_IBKR, PROVIDER_IBKR_EXTENDED]
)
def test_status_says_ibkr_needs_no_api_key(provider: str) -> None:
    status = _service(_FakeStore({"finnhub_api_key": "x"})).status(
        provider
    )

    assert status.requires_api_key is False
    assert status.api_key_saved is False
    assert status.api_secret_saved is False


def test_status_never_decrypts_a_blob() -> None:
    """The store raises on ``load_secret``; a status line must still work.

    This is the regression the old UI never had: it read ``Path.exists()``
    directly, so a corrupt blob or one owned by another Windows user could
    not break the status line.
    """

    store = _NoDecryptStore({"finnhub_api_key": "x"})
    status = _service(store).status(PROVIDER_FINNHUB_TRADES)

    assert status.api_key_saved is True
    assert store.loads == []


def test_status_returns_only_booleans_and_an_identifier() -> None:
    """No field may carry a secret.  Pinned by type, not by value."""

    for field in dataclasses.fields(CredentialStatus):
        if field.name == "provider":
            continue
        assert field.type in {"bool", bool}, field.name


def test_credential_status_has_no_secret_valued_field() -> None:
    assert {field.name for field in dataclasses.fields(CredentialStatus)} == {
        "provider",
        "requires_api_key",
        "api_key_saved",
        "api_secret_saved",
    }


# -- resolve: environment first, then the store ------------------------


def test_environment_wins_over_the_store_for_every_credential() -> None:
    store = _FakeStore(
        {
            "finnhub_api_key": "stored-finnhub",
            "alpaca_api_key": "stored-alpaca-key",
            "alpaca_api_secret": "stored-alpaca-secret",
        }
    )
    resolved = _service(store).resolve_stream_credentials(
        environment={
            "FINNHUB_API_KEY": "env-finnhub",
            "APCA_API_KEY_ID": "env-alpaca-key",
            "APCA_API_SECRET_KEY": "env-alpaca-secret",
        }
    )

    assert resolved == StreamCredentials(
        finnhub_api_key="env-finnhub",
        alpaca_api_key="env-alpaca-key",
        alpaca_api_secret="env-alpaca-secret",
    )
    # Not merely overridden: the blob was never read at all.
    assert store.loads == []


def test_the_store_is_the_fallback_when_environment_is_unset() -> None:
    store = _FakeStore(
        {
            "finnhub_api_key": "stored-finnhub",
            "alpaca_api_key": "stored-alpaca-key",
            "alpaca_api_secret": "stored-alpaca-secret",
        }
    )
    resolved = _service(store).resolve_stream_credentials(environment={})

    assert resolved == StreamCredentials(
        finnhub_api_key="stored-finnhub",
        alpaca_api_key="stored-alpaca-key",
        alpaca_api_secret="stored-alpaca-secret",
    )
    assert store.loads == [
        "finnhub_api_key",
        "alpaca_api_key",
        "alpaca_api_secret",
    ]


def test_each_credential_falls_back_independently() -> None:
    """One override must not change how the other two resolve."""

    store = _FakeStore(
        {
            "alpaca_api_key": "stored-alpaca-key",
            "alpaca_api_secret": "stored-alpaca-secret",
        }
    )
    resolved = _service(store).resolve_stream_credentials(
        environment={
            "FINNHUB_API_KEY": "env-finnhub",
            "APCA_API_KEY_ID": "",
            "APCA_API_SECRET_KEY": "env-alpaca-secret",
        }
    )

    assert resolved.finnhub_api_key == "env-finnhub"
    assert resolved.alpaca_api_key == "stored-alpaca-key"
    assert resolved.alpaca_api_secret == "env-alpaca-secret"
    # The overridden keys were never read; the unset one was.
    assert store.loads == ["alpaca_api_key"]


@pytest.mark.parametrize(
    "blank", ["   ", "\t", "\n", "  \t "]
)
def test_a_whitespace_only_variable_counts_as_unset(blank: str) -> None:
    """Whitespace is not a credential: fall back to the store."""

    store = _FakeStore({"finnhub_api_key": "stored-finnhub"})
    resolved = _service(store).resolve_stream_credentials(
        environment={"FINNHUB_API_KEY": blank}
    )

    assert resolved.finnhub_api_key == "stored-finnhub"
    # The whitespace value did not short-circuit the read: the blob was
    # consulted for finnhub, exactly as if the variable were unset.
    assert "finnhub_api_key" in store.loads


def test_an_environment_value_is_stripped_of_surrounding_space() -> None:
    store = _FakeStore()
    resolved = _service(store).resolve_stream_credentials(
        environment={"FINNHUB_API_KEY": "  env-finnhub  "}
    )

    assert resolved.finnhub_api_key == "env-finnhub"


def test_a_missing_credential_resolves_to_an_empty_string() -> None:
    """``None`` from the store becomes ``""``, the documented contract."""

    store = _FakeStore()
    resolved = _service(store).resolve_stream_credentials(environment={})

    assert resolved == StreamCredentials(
        finnhub_api_key="",
        alpaca_api_key="",
        alpaca_api_secret="",
    )


def test_a_store_read_failure_surfaces_as_a_value_error() -> None:
    """``_start_stream`` catches ``ValueError``, not the store's error."""

    store = _FakeStore(load_error=CredentialStoreError("broken blob"))
    with pytest.raises(ValueError, match="broken blob") as caught:
        _service(store).resolve_stream_credentials(environment={})

    assert isinstance(caught.value.__cause__, CredentialStoreError)


def test_the_read_failure_conversion_keeps_the_original_as_cause() -> None:
    original = CredentialStoreError("broken blob")
    store = _FakeStore(load_error=original)

    with pytest.raises(ValueError) as caught:
        _service(store).resolve_stream_credentials(environment={})

    assert caught.value.__cause__ is original
    assert str(caught.value) == str(original)


def test_resolve_defaults_to_the_process_environment(
    monkeypatch,
) -> None:
    """Called with no argument it must read ``os.environ``, not nothing."""

    monkeypatch.setenv("FINNHUB_API_KEY", "env-finnhub")
    store = _FakeStore({"finnhub_api_key": "stored-finnhub"})

    resolved = _service(store).resolve_stream_credentials()

    assert resolved.finnhub_api_key == "env-finnhub"
    # The process environment supplied it, so the blob was not read for it.
    assert "finnhub_api_key" not in store.loads


# -- the shape of the thing -------------------------------------------


def test_the_mapping_is_defined_exactly_once() -> None:
    """One table, so the UI cannot keep a second secret-name list."""

    assert STREAM_CREDENTIAL_SOURCES == {
        PROVIDER_FINNHUB_TRADES: (
            ("finnhub_api_key", "FINNHUB_API_KEY"),
        ),
        PROVIDER_ALPACA_IEX: (
            ("alpaca_api_key", "APCA_API_KEY_ID"),
            ("alpaca_api_secret", "APCA_API_SECRET_KEY"),
        ),
        PROVIDER_IBKR: (),
        PROVIDER_IBKR_EXTENDED: (),
    }
    assert API_KEY_PROVIDERS == frozenset(
        {PROVIDER_FINNHUB_TRADES, PROVIDER_ALPACA_IEX}
    )


def test_the_reverse_table_matches_the_forward_one() -> None:
    """One source of truth, read both ways.

    ``resolve_stream_credentials`` looks a secret name up here; if this
    table were hand-written beside the forward mapping the two could
    disagree and a credential would resolve from the wrong variable.
    """

    assert ENVIRONMENT_NAMES == {
        "finnhub_api_key": "FINNHUB_API_KEY",
        "alpaca_api_key": "APCA_API_KEY_ID",
        "alpaca_api_secret": "APCA_API_SECRET_KEY",
    }
    assert set(ENVIRONMENT_NAMES) == {
        name
        for sources in STREAM_CREDENTIAL_SOURCES.values()
        for name, _ in sources
    }


def test_the_service_holds_only_the_store() -> None:
    store = _FakeStore()
    service = _service(store)

    assert service.store is store
    assert not hasattr(service, "window")
    assert not hasattr(service, "widgets")


def test_the_service_takes_its_store_positionally() -> None:
    """``MainWindow`` passes the store it already owns; no second store."""

    import inspect

    parameters = list(
        inspect.signature(
            DesktopCredentialService.__init__
        ).parameters
    )

    assert parameters == ["self", "store"]


def test_save_provider_takes_its_secrets_by_keyword() -> None:
    import inspect

    parameters = inspect.signature(
        DesktopCredentialService.save_provider
    ).parameters

    assert list(parameters) == [
        "self",
        "provider",
        "api_key",
        "api_secret",
    ]
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("api_key", "api_secret")
    )


def test_credential_status_is_a_frozen_slots_dataclass() -> None:
    params = CredentialStatus.__dataclass_params__
    assert params.frozen is True
    assert CredentialStatus.__slots__ == (
        "provider",
        "requires_api_key",
        "api_key_saved",
        "api_secret_saved",
    )


def test_stream_credentials_is_a_frozen_slots_dataclass() -> None:
    params = StreamCredentials.__dataclass_params__
    assert params.frozen is True
    assert StreamCredentials.__slots__ == (
        "finnhub_api_key",
        "alpaca_api_key",
        "alpaca_api_secret",
    )


def test_the_store_port_is_a_protocol_with_four_members() -> None:
    from us_quant.desktop_credentials import CredentialStorePort

    assert getattr(CredentialStorePort, "_is_protocol", False) is True
    for member in (
        "save_secret",
        "load_secret",
        "delete_secret",
        "has_secret",
    ):
        assert callable(getattr(CredentialStorePort, member))


# -- structural guards -------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
    return modules


def _identifier_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_the_module_dependency_surface_is_exactly_what_was_agreed() -> None:
    """Pinned as an equality: a new import must be a deliberate edit."""

    assert _imported_modules(_MODULE_PATH) == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "os",
        "typing",
        "us_quant.credential_store",
    }


@pytest.mark.parametrize(
    "module",
    [
        "PySide6",
        "us_quant.desktop",
        "us_quant.desktop_widgets",
        "us_quant.desktop_workers",
        "us_quant.desktop_settings",
        "us_quant.market_data_service",
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
def test_the_module_imports_nothing_it_was_told_not_to(
    module: str,
) -> None:
    imported = _imported_modules(_MODULE_PATH)
    assert module not in imported
    assert not any(
        name.startswith(f"{module}.") for name in imported
    ), f"imports {module} through a submodule"


@pytest.mark.parametrize(
    "name",
    [
        "QApplication",
        "QMessageBox",
        "QWidget",
        "QMainWindow",
        "QThread",
        "MainWindow",
        "MarketDataService",
        "IBKRReadOnlyStream",
        "AlpacaIEXStream",
        "FinnhubTradeStream",
        "ExecutionLease",
        "AutoQuantEngine",
        "PaperSession",
    ],
)
def test_the_module_mentions_no_ui_provider_or_paper_symbol(
    name: str,
) -> None:
    """It is a leaf application service, not a UI or an adapter factory.

    ``IBKRReadOnlyStream`` and friends are in the list deliberately: the
    module needs three provider *ids*, not the market-data dependency
    graph that produces those adapters.
    """

    assert name not in _identifier_names(_MODULE_PATH)


def test_the_dpapi_implementation_is_untouched() -> None:
    """The store change is additive: ``has_secret`` and nothing else.

    Re-deriving the frozen helpers from source is the point -- a future
    edit that swaps entropy or the file format would fail here rather
    than silently orphan every existing ``.dpapi`` file.
    """

    source = _STORE_PATH.read_text(encoding="utf-8")

    assert '_ENTROPY = b"USQuantResearch:finnhub:v1"' in source
    assert "_CRYPTPROTECT_UI_FORBIDDEN = 0x01" in source
    assert "b64encode" in source and "b64decode" in source
    for helper in ("_protect", "_unprotect", "_crypt", "_blob"):
        assert f"def {helper}(" in source
