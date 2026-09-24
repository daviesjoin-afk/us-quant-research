"""Behaviour tests for ``SettingsOrchestrator`` (v2O-F2).

No window and no real service: the page is a recorder, the settings service and
the credential service are fakes, and the two facts the capability must read live
(the market source and the current config) are counting callables.  Every claim
about *sequencing* -- what is published, in what order, only after what
succeeded -- is decided by the code under test rather than by a Qt event loop.

The properties covered, in the round's order: the render reads its facts every
time, the provider selections sync silently and publish once, the credential
save/clear paths keep their frozen semantics and never fake success, the
preference transaction is the service's and a refusal publishes nothing, the
provider switch happens only after a successful commit, and the two capability
toggles stay preference drafts.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from us_quant.credential_store import CredentialStoreError
from us_quant.desktop_credentials import CredentialStatus
from us_quant.desktop_settings import DesktopSettingsCommit
from us_quant.desktop_v2.orchestration.system.settings import orchestrator as mod
from us_quant.desktop_v2.orchestration.system.settings.orchestrator import (
    SettingsOrchestrator,
)
from us_quant.desktop_v2.pages.system.settings.models import (
    CredentialDraft,
    SettingsDraft,
)
from us_quant.trading.ports.broker_account import BrokerAccountError
from us_quant.trading.ports.market_data import MarketDataActiveError
from us_quant.user_settings import UserPreferences, UserSettingsError


# -- fakes ---------------------------------------------------------------


class FakePage:
    """The render/intent seam: records every programmatic call it was given."""

    def __init__(self) -> None:
        self.views: list = []
        self.api_provider_sets: list[tuple] = []
        self.market_provider_sets: list[tuple] = []
        self.paper_sets: list[tuple] = []
        self.extended_sets: list[tuple] = []
        self.cleared_inputs = 0

    def render(self, view) -> None:
        self.views.append(view)

    def set_api_provider(self, provider, *, emit_change=False) -> None:
        self.api_provider_sets.append((provider, emit_change))

    def set_market_provider(self, provider, *, emit_change=False) -> None:
        self.market_provider_sets.append((provider, emit_change))

    def set_paper_order_capability(self, enabled, *, emit_change=False) -> None:
        self.paper_sets.append((enabled, emit_change))

    def set_extended_hours_paper(self, enabled, *, emit_change=False) -> None:
        self.extended_sets.append((enabled, emit_change))

    def clear_credential_inputs(self) -> None:
        self.cleared_inputs += 1


class FakeCredentialService:
    """The credential service's contract, with every call recorded."""

    def __init__(self) -> None:
        self.saved: list[tuple] = []
        self.cleared: list[str] = []
        self.status_calls: list[str] = []
        self.status_value = CredentialStatus(
            provider="finnhub_trades",
            requires_api_key=True,
            api_key_saved=False,
            api_secret_saved=False,
        )
        self.save_error: Exception | None = None
        self.clear_error: Exception | None = None

    def status(self, provider: str) -> CredentialStatus:
        self.status_calls.append(provider)
        return CredentialStatus(
            provider=provider,
            requires_api_key=self.status_value.requires_api_key,
            api_key_saved=self.status_value.api_key_saved,
            api_secret_saved=self.status_value.api_secret_saved,
        )

    def save_provider(
        self,
        provider: str,
        *,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((provider, api_key, api_secret))

    def clear_provider(self, provider: str) -> None:
        if self.clear_error is not None:
            raise self.clear_error
        self.cleared.append(provider)


class FakeSettingsService:
    """The transaction owner's contract: one commit, or one refusal."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []
        self.config = SimpleNamespace(ibkr="new-config")

    def commit(
        self,
        preferences,
        *,
        current_config,
        broker_config,
        runtime_guards=(),
    ) -> DesktopSettingsCommit:
        self.calls.append(
            {
                "preferences": preferences,
                "current_config": current_config,
                "broker_config": broker_config,
                "runtime_guards": tuple(runtime_guards),
            }
        )
        if self.error is not None:
            raise self.error
        return DesktopSettingsCommit(
            preferences=preferences, config=self.config
        )


class Live:
    """A fact the capability must re-read on every use."""

    def __init__(self, value=None) -> None:
        self.value = value
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.value


@dataclass
class Harness:
    orchestrator: SettingsOrchestrator
    page: FakePage
    credentials: FakeCredentialService
    settings: FakeSettingsService
    market_source: Live
    current_config: Live
    broker_config: Live
    guards: Live
    messages: list
    logs: list
    themes: list
    selections: list
    switches: list
    commits: list
    paper_confirmations: list
    extended_confirmations: list
    events: list


def _harness(
    *,
    initial_api_provider: str = "finnhub_trades",
    settings_error: Exception | None = None,
) -> Harness:
    page = FakePage()
    credentials = FakeCredentialService()
    settings = FakeSettingsService(settings_error)
    market_source = Live(None)
    current_config = Live(SimpleNamespace(ibkr="startup-config"))
    broker_config = Live(SimpleNamespace(config="broker-config"))
    guards = Live((SimpleNamespace(name="market-data"),))
    orchestrator = SettingsOrchestrator(
        page=page,
        settings_service=settings,
        credential_service=credentials,
        initial_api_provider=initial_api_provider,
        current_config=current_config,
        broker_config=broker_config,
        runtime_guards=guards,
        active_market_source_id=market_source,
    )

    messages: list = []
    logs: list = []
    themes: list = []
    selections: list = []
    switches: list = []
    commits: list = []
    paper_confirmations: list = []
    extended_confirmations: list = []
    events: list = []

    def record(kind):
        def handler(*args):
            events.append((kind, *args))
            {
                "information": messages,
                "warning": messages,
                "log": logs,
                "theme": themes,
                "selection": selections,
                "switch": switches,
                "commit": commits,
                "paper": paper_confirmations,
                "extended": extended_confirmations,
            }[kind].append(args if len(args) > 1 else args[0])

        return handler

    orchestrator.information_requested.connect(record("information"))
    orchestrator.warning_requested.connect(record("warning"))
    orchestrator.log_requested.connect(record("log"))
    orchestrator.theme_preview_requested.connect(record("theme"))
    orchestrator.market_provider_selection_requested.connect(
        record("selection")
    )
    orchestrator.market_switch_requested.connect(record("switch"))
    orchestrator.settings_committed.connect(record("commit"))
    orchestrator.paper_order_capability_confirmation_requested.connect(
        record("paper")
    )
    orchestrator.extended_hours_confirmation_requested.connect(
        record("extended")
    )
    return Harness(
        orchestrator=orchestrator,
        page=page,
        credentials=credentials,
        settings=settings,
        market_source=market_source,
        current_config=current_config,
        broker_config=broker_config,
        guards=guards,
        messages=messages,
        logs=logs,
        themes=themes,
        selections=selections,
        switches=switches,
        commits=commits,
        paper_confirmations=paper_confirmations,
        extended_confirmations=extended_confirmations,
        events=events,
    )


def _draft(**overrides) -> SettingsDraft:
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
    return SettingsDraft(**values)


# -- B. render -----------------------------------------------------------


def test_render_reads_the_status_and_the_live_source_every_time() -> None:
    harness = _harness()
    harness.credentials.status_value = CredentialStatus(
        provider="finnhub_trades",
        requires_api_key=True,
        api_key_saved=False,
        api_secret_saved=False,
    )

    harness.orchestrator.render_current()
    assert harness.page.views[-1].credential_status_text == "Finnhub：未保存"
    assert harness.page.views[-1].credential_clear_enabled is True

    # Both facts changed behind the capability's back: a cached value would keep
    # drawing the old one.
    harness.credentials.status_value = CredentialStatus(
        provider="finnhub_trades",
        requires_api_key=True,
        api_key_saved=True,
        api_secret_saved=False,
    )
    harness.market_source.value = "finnhub_trades"
    harness.orchestrator.render_current()

    assert harness.page.views[-1].credential_status_text == "Finnhub：已加密保存"
    assert harness.page.views[-1].credential_clear_enabled is False
    assert harness.credentials.status_calls == [
        "finnhub_trades",
        "finnhub_trades",
    ]
    assert harness.market_source.reads == 2


@pytest.mark.parametrize(
    "provider,live,expected",
    [
        ("finnhub_trades", None, True),
        ("finnhub_trades", "finnhub_trades", False),
        ("finnhub_trades", "alpaca_iex", True),
        ("alpaca_iex", "finnhub_trades", True),
        ("alpaca_iex", "alpaca_iex", False),
        ("ibkr", None, False),
        ("ibkr", "ibkr", False),
    ],
)
def test_render_offers_clear_only_for_a_non_live_credential_provider(
    provider: str, live: str | None, expected: bool
) -> None:
    harness = _harness(initial_api_provider=provider)
    harness.market_source.value = live

    harness.orchestrator.render_current()

    view = harness.page.views[-1]
    assert view.credential_clear_enabled is expected
    assert view.credential_save_enabled is (provider != "ibkr")


def test_render_paints_exactly_once_per_call() -> None:
    harness = _harness()
    harness.orchestrator.render_current()
    harness.orchestrator.render_current()
    assert len(harness.page.views) == 2


def test_the_connection_fact_comes_from_the_capability() -> None:
    harness = _harness()

    harness.orchestrator.render_current()
    assert harness.page.views[-1].connection_settings_enabled is True

    harness.orchestrator.set_connection_settings_enabled(False)
    assert harness.page.views[-1].connection_settings_enabled is False

    harness.orchestrator.set_connection_settings_enabled(True)
    assert harness.page.views[-1].connection_settings_enabled is True
    assert [view.connection_settings_enabled for view in harness.page.views] == [
        True,
        False,
        True,
    ]


# -- C / D / E. provider selection ---------------------------------------


def test_selecting_the_api_provider_only_repaints() -> None:
    harness = _harness()

    harness.orchestrator.select_api_provider("alpaca_iex")

    assert harness.orchestrator.selected_api_provider == "alpaca_iex"
    assert len(harness.page.views) == 1
    assert harness.credentials.saved == []
    assert harness.credentials.cleared == []
    assert harness.settings.calls == []
    assert harness.selections == []
    assert harness.switches == []
    assert harness.page.api_provider_sets == []


@pytest.mark.parametrize(
    "market_provider,expected_api",
    [
        ("alpaca_iex", "alpaca_iex"),
        ("ibkr_extended", "ibkr"),
        ("ibkr", "ibkr"),
        ("finnhub_trades", "finnhub_trades"),
    ],
)
def test_selecting_a_market_provider_syncs_silently_and_publishes_once(
    market_provider: str, expected_api: str
) -> None:
    harness = _harness()

    harness.orchestrator.select_market_provider(market_provider)

    assert harness.orchestrator.selected_api_provider == expected_api
    assert harness.page.api_provider_sets == [(expected_api, False)]
    assert len(harness.page.views) == 1
    assert harness.selections == [market_provider]
    # It asks for the route's combo, not for a switch, and it never re-emits a
    # market-provider intent of its own.
    assert harness.switches == []
    assert harness.page.market_provider_sets == []


def test_adopting_the_market_provider_publishes_nothing() -> None:
    harness = _harness()

    harness.orchestrator.adopt_market_provider("alpaca_iex")

    assert harness.page.market_provider_sets == [("alpaca_iex", False)]
    assert harness.selections == []
    assert harness.switches == []
    assert harness.page.views == []
    # The selected API provider is the operator's choice in this workspace and
    # is deliberately not dragged along by a route-side change.
    assert harness.orchestrator.selected_api_provider == "finnhub_trades"


def test_the_two_directions_cannot_echo_each_other() -> None:
    """Every programmatic page write is silent, in both directions."""

    harness = _harness()

    harness.orchestrator.select_market_provider("alpaca_iex")
    harness.orchestrator.adopt_market_provider("finnhub_trades")
    harness.orchestrator.select_api_provider("ibkr")

    silent = (
        harness.page.api_provider_sets
        + harness.page.market_provider_sets
        + harness.page.paper_sets
        + harness.page.extended_sets
    )
    assert silent, "the programmatic setters were exercised"
    assert all(emit_change is False for _value, emit_change in silent)


# -- F / G / H. credential save ------------------------------------------


def test_saving_a_finnhub_key_writes_once_then_clears_and_repaints() -> None:
    harness = _harness()

    harness.orchestrator.save_credentials(
        CredentialDraft(
            provider="finnhub_trades", api_key="  key-1 ", api_secret=""
        )
    )

    assert harness.credentials.saved == [("finnhub_trades", "key-1", "")]
    assert harness.page.cleared_inputs == 1
    assert len(harness.page.views) == 1
    assert harness.logs == [mod.CREDENTIAL_SAVED_LOG]
    assert harness.messages == []


def test_an_empty_finnhub_input_is_a_no_change() -> None:
    harness = _harness()

    harness.orchestrator.save_credentials(
        CredentialDraft(provider="finnhub_trades", api_key=" ", api_secret="")
    )

    assert harness.credentials.saved == []
    assert harness.page.cleared_inputs == 0
    assert harness.page.views == []
    assert harness.messages == [
        (mod.NO_CHANGE_TITLE, mod.NO_CHANGE_MESSAGE)
    ]


def test_a_complete_alpaca_pair_is_written_together() -> None:
    harness = _harness()

    harness.orchestrator.save_credentials(
        CredentialDraft(
            provider="alpaca_iex",
            api_key=" key ",
            api_secret=" secret ",
        )
    )

    assert harness.credentials.saved == [("alpaca_iex", "key", "secret")]
    assert harness.page.cleared_inputs == 1
    assert len(harness.page.views) == 1


@pytest.mark.parametrize(
    "key,secret", [("key", ""), ("", "secret"), ("key", " ")]
)
def test_a_half_filled_alpaca_pair_is_refused_without_writing(
    key: str, secret: str
) -> None:
    harness = _harness()

    harness.orchestrator.save_credentials(
        CredentialDraft(
            provider="alpaca_iex", api_key=key, api_secret=secret
        )
    )

    assert harness.credentials.saved == []
    assert harness.page.cleared_inputs == 0
    assert harness.messages == [
        (mod.INCOMPLETE_TITLE, mod.INCOMPLETE_MESSAGE)
    ]


@pytest.mark.parametrize("provider", ["ibkr", "ibkr_extended"])
def test_ibkr_credentials_are_explained_not_saved(provider: str) -> None:
    harness = _harness()

    harness.orchestrator.save_credentials(
        CredentialDraft(provider=provider, api_key="key", api_secret="secret")
    )

    assert harness.credentials.saved == []
    assert harness.page.views == []
    assert harness.messages == [
        (mod.NO_API_KEY_TITLE, mod.NO_API_KEY_MESSAGE)
    ]


@pytest.mark.parametrize(
    "error",
    [
        CredentialStoreError("dpapi refused"),
        OSError("disk"),
        ValueError("bad provider"),
    ],
)
def test_a_failed_credential_save_never_reports_success(error: Exception) -> None:
    harness = _harness()
    harness.credentials.save_error = error

    harness.orchestrator.save_credentials(
        CredentialDraft(provider="finnhub_trades", api_key="key", api_secret="")
    )

    assert harness.messages == [
        (mod.CREDENTIAL_SAVE_FAILED_TITLE, str(error))
    ]
    # The operator's input is kept so the failure can be corrected and retried.
    assert harness.page.cleared_inputs == 0
    assert harness.page.views == []
    assert harness.logs == []


# -- I. credential clear -------------------------------------------------


def test_clearing_the_live_providers_credentials_is_refused() -> None:
    harness = _harness(initial_api_provider="finnhub_trades")
    harness.market_source.value = "finnhub_trades"

    harness.orchestrator.clear_credentials("finnhub_trades")

    assert harness.credentials.cleared == []
    assert harness.messages == [
        (mod.ACTIVE_SOURCE_TITLE, mod.ACTIVE_SOURCE_MESSAGE)
    ]
    assert harness.page.cleared_inputs == 0
    assert harness.page.views == []


def test_clearing_an_inactive_providers_credentials_succeeds() -> None:
    harness = _harness(initial_api_provider="alpaca_iex")
    harness.market_source.value = "finnhub_trades"

    harness.orchestrator.clear_credentials("alpaca_iex")

    assert harness.credentials.cleared == ["alpaca_iex"]
    assert harness.page.cleared_inputs == 1
    assert len(harness.page.views) == 1
    assert harness.logs == [
        "已清除当前 Windows 用户保存的 Alpaca 行情凭据"
    ]
    assert harness.messages == []


def test_the_live_source_is_read_at_clear_time_not_at_composition() -> None:
    """A stream that started later still blocks its own credentials."""

    harness = _harness(initial_api_provider="finnhub_trades")
    assert harness.market_source.reads == 0, "nothing read at composition"

    before = harness.market_source.reads
    harness.orchestrator.clear_credentials("finnhub_trades")
    assert harness.credentials.cleared == ["finnhub_trades"]
    assert harness.market_source.reads > before, "the gate reads the source"

    # The stream started after the first, allowed, clear attempt.
    harness.market_source.value = "finnhub_trades"
    harness.orchestrator.clear_credentials("finnhub_trades")

    assert harness.credentials.cleared == ["finnhub_trades"]
    assert harness.messages == [
        (mod.ACTIVE_SOURCE_TITLE, mod.ACTIVE_SOURCE_MESSAGE)
    ]


def test_clearing_ibkr_credentials_asks_the_service_for_nothing() -> None:
    harness = _harness(initial_api_provider="ibkr")

    harness.orchestrator.clear_credentials("ibkr")

    assert harness.credentials.cleared == []
    assert harness.messages == [
        (mod.NO_CLEAR_TITLE, mod.NO_CLEAR_MESSAGE)
    ]


def test_a_live_ibkr_source_is_reported_as_live_not_as_needing_no_clear() -> None:
    """The live-source gate runs first, as the retired handler did."""

    harness = _harness(initial_api_provider="ibkr")
    harness.market_source.value = "ibkr"

    harness.orchestrator.clear_credentials("ibkr")

    assert harness.messages == [
        (mod.ACTIVE_SOURCE_TITLE, mod.ACTIVE_SOURCE_MESSAGE)
    ]


@pytest.mark.parametrize(
    "error", [CredentialStoreError("dpapi refused"), OSError("disk")]
)
def test_a_failed_credential_clear_never_reports_success(
    error: Exception,
) -> None:
    harness = _harness()
    harness.credentials.clear_error = error

    harness.orchestrator.clear_credentials("finnhub_trades")

    assert harness.messages == [
        (mod.CREDENTIAL_CLEAR_FAILED_TITLE, str(error))
    ]
    assert harness.page.cleared_inputs == 0
    assert harness.logs == []


# -- K. the preference transaction ---------------------------------------


def test_saving_preferences_commits_once_and_publishes_the_commit() -> None:
    harness = _harness()
    draft = _draft(theme="light", market_provider="alpaca_iex")

    harness.orchestrator.save_preferences(draft)

    assert len(harness.settings.calls) == 1
    call = harness.settings.calls[0]
    assert call["preferences"] == UserPreferences(
        theme="light", market_provider="alpaca_iex"
    )
    # Every runtime participant comes from a provider, read at commit time.
    assert call["current_config"] is harness.current_config.value
    assert call["broker_config"] is harness.broker_config.value
    assert call["runtime_guards"] == tuple(harness.guards.value)
    assert len(harness.commits) == 1
    assert harness.commits[0].config is harness.settings.config
    assert harness.messages == []
    assert harness.switches == []


@pytest.mark.parametrize(
    "error",
    [
        UserSettingsError("invalid"),
        MarketDataActiveError("stream live"),
        BrokerAccountError("refresh running"),
    ],
)
def test_a_refused_preference_save_publishes_only_the_warning(
    error: Exception,
) -> None:
    harness = _harness(settings_error=error)
    harness.credentials.status_value = CredentialStatus(
        provider="finnhub_trades",
        requires_api_key=True,
        api_key_saved=False,
        api_secret_saved=False,
    )

    harness.orchestrator.save_preferences(_draft(theme="light"))

    assert len(harness.settings.calls) == 1
    assert harness.messages == [(mod.SAVE_FAILED_TITLE, str(error))]
    # Nothing downstream moved, and the page was not touched at all: the
    # operator's draft stays on screen for a retry.
    assert harness.commits == []
    assert harness.switches == []
    assert harness.selections == []
    assert harness.themes == []
    assert harness.page.views == []
    assert harness.page.cleared_inputs == 0


def test_a_programming_error_is_not_reported_as_an_unsaved_setting() -> None:
    """Only the transaction's declared failures are caught."""

    harness = _harness(settings_error=TypeError("bug"))

    with pytest.raises(TypeError):
        harness.orchestrator.save_preferences(_draft())

    assert harness.messages == []
    assert harness.commits == []


# -- M. provider switch --------------------------------------------------


def test_a_successful_provider_switch_adopts_before_it_switches() -> None:
    harness = _harness()
    draft = _draft(market_provider="alpaca_iex")

    harness.orchestrator.request_provider_switch(draft)

    assert [event[0] for event in harness.events] == ["commit", "switch"]
    assert len(harness.commits) == 1
    assert harness.commits[0].config is harness.settings.config
    assert harness.switches == ["alpaca_iex"]
    assert harness.messages == []


@pytest.mark.parametrize(
    "error",
    [
        UserSettingsError("invalid"),
        MarketDataActiveError("stream live"),
        BrokerAccountError("refresh running"),
    ],
)
def test_a_refused_provider_switch_never_reaches_the_market_route(
    error: Exception,
) -> None:
    """The regression: a refused save must not move a live feed."""

    harness = _harness(settings_error=error)

    harness.orchestrator.request_provider_switch(
        _draft(market_provider="alpaca_iex")
    )

    assert harness.messages == [(mod.SAVE_FAILED_TITLE, str(error))]
    assert harness.switches == []
    assert harness.commits == []
    assert harness.events == [("warning", mod.SAVE_FAILED_TITLE, str(error))]


def test_the_switch_uses_the_saved_provider_and_not_the_draft() -> None:
    """The committed preference is the truth the route is switched to."""

    harness = _harness()
    # The store normalises what it writes; the draft is only the input.
    harness.settings.commit = lambda preferences, **kwargs: (
        DesktopSettingsCommit(
            preferences=UserPreferences(
                market_provider="ibkr_extended", theme=preferences.theme
            ),
            config=harness.settings.config,
        )
    )

    harness.orchestrator.request_provider_switch(
        _draft(market_provider="ibkr_extended")
    )

    assert harness.switches == ["ibkr_extended"]


# -- N / O / P. theme and the capability toggles -------------------------


def test_a_theme_preview_is_only_republished() -> None:
    harness = _harness()

    harness.orchestrator.preview_theme("light")

    assert harness.themes == ["light"]
    assert harness.page.views == []


def test_turning_a_capability_off_needs_no_confirmation() -> None:
    harness = _harness()

    harness.orchestrator.request_paper_order_capability_toggle(False)
    harness.orchestrator.request_extended_hours_toggle(False)

    assert harness.paper_confirmations == []
    assert harness.extended_confirmations == []
    assert harness.page.paper_sets == []


def test_turning_the_paper_capability_on_asks_with_the_safety_wording() -> None:
    harness = _harness()

    harness.orchestrator.request_paper_order_capability_toggle(True)

    assert harness.paper_confirmations == [
        (mod.PAPER_CAPABILITY_TITLE, mod.PAPER_CAPABILITY_MESSAGE)
    ]
    # The wording still says what the flag does *not* authorise.
    for phrase in ("不会立即下单", "不会自动武装", "是否保留开启状态"):
        assert phrase in mod.PAPER_CAPABILITY_MESSAGE
    assert harness.page.paper_sets == []


def test_turning_extended_hours_on_asks_with_the_safety_wording() -> None:
    harness = _harness()

    harness.orchestrator.request_extended_hours_toggle(True)

    assert harness.extended_confirmations == [
        (mod.EXTENDED_HOURS_TITLE, mod.EXTENDED_HOURS_MESSAGE)
    ]
    for phrase in ("不会立即下单", "维护窗口仍会拒绝订单", "是否保留开启状态"):
        assert phrase in mod.EXTENDED_HOURS_MESSAGE


def test_accepting_a_capability_keeps_the_draft_and_starts_nothing() -> None:
    harness = _harness()

    harness.orchestrator.request_paper_order_capability_toggle(True)
    harness.orchestrator.confirm_paper_order_capability(True)

    assert harness.page.paper_sets == []
    assert harness.settings.calls == []
    assert harness.commits == []


def test_refusing_the_paper_capability_reverts_the_control_silently() -> None:
    harness = _harness()

    harness.orchestrator.request_paper_order_capability_toggle(True)
    harness.orchestrator.confirm_paper_order_capability(False)

    assert harness.page.paper_sets == [(False, False)]
    assert harness.settings.calls == []


def test_refusing_extended_hours_reverts_the_control_silently() -> None:
    harness = _harness()

    harness.orchestrator.request_extended_hours_toggle(True)
    harness.orchestrator.confirm_extended_hours(False)

    assert harness.page.extended_sets == [(False, False)]
    assert harness.settings.calls == []
