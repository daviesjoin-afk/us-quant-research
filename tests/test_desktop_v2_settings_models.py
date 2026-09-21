"""The Settings DTOs are frozen, slotted and Qt-free."""

from __future__ import annotations

import dataclasses

import pytest

from us_quant.desktop_v2.pages.system.settings import models


_DTOS = (
    models.SettingsDraft,
    models.CredentialDraft,
    models.SettingsStorageView,
    models.SettingsPageView,
)


@pytest.mark.parametrize("dto", _DTOS)
def test_the_dto_is_frozen_and_slotted(dto) -> None:
    assert dataclasses.is_dataclass(dto)
    params = dto.__dataclass_params__
    assert params.frozen is True
    assert "__slots__" in vars(dto)


def test_settings_draft_is_a_pure_data_bag() -> None:
    draft = models.SettingsDraft(
        theme="dark",
        market_provider="finnhub_trades",
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=71,
        connection_timeout_seconds=15.0,
        paper_order_capability_enabled=False,
        extended_hours_paper_enabled=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        draft.theme = "light"  # type: ignore[misc]


def test_credential_draft_carries_provider_and_secrets() -> None:
    draft = models.CredentialDraft(
        provider="alpaca_iex", api_key="k", api_secret="s"
    )
    assert (draft.provider, draft.api_key, draft.api_secret) == (
        "alpaca_iex",
        "k",
        "s",
    )


def test_models_module_imports_no_qt() -> None:
    import ast
    import pathlib

    source = pathlib.Path(models.__file__).read_text(encoding="utf-8")
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    )
