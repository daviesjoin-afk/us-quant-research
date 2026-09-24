"""Settings orchestration: the desktop owner of the System settings workspace.

The Settings page was already native v2 and the two services already existed,
but the *sequence* between them and the operator was still the window's:
``_settings_api_provider``, ``_connection_settings_enabled``, the view assembly,
the credential status projection, the save/clear sequencing, the preference
transaction's adapter, the provider-selection sync and the two capability
confirmations.  This class owns that sequence now.

The boundary it draws:

* **the transaction is not re-implemented.**  ``DesktopSettingsService`` still
  owns validate -> derive -> preflight -> persist -> apply, in that order, and
  this class only decides what a refusal means and who hears about a success.
  It never writes a preference, never reads a credential store and never
  validates a value;
* **the credential semantics are not re-implemented.**
  ``DesktopCredentialService`` still owns which provider needs which secret,
  and this class only sequences the clicks around it;
* **there is no second application configuration.**  ``user_preferences`` and
  ``AppConfig`` stay the composition root's: they are read through callables and
  the finished commit is published for the root to adopt.  Holding them here
  would force Market, Paper, Risk and Research to reach *into Settings* for the
  current configuration, which is the wrong direction and the coupling this
  decomposition exists to remove;
* **no widget, no dialog, no other capability.**  The page is a render seam, the
  messages are signals, and the market route is asked through a signal -- this
  module does not import ``MarketOrchestrator``, ``AccountOrchestrator``,
  ``PaperOrchestrator``, any Research orchestrator, Risk, Execution or a broker
  adapter.  The three exception types it catches are the transaction's declared
  contract: two are ports (``us_quant.trading.ports.market_data`` and
  ``us_quant.trading.ports.broker_account``) and the third,
  ``UserSettingsError``, is the preferences store's own -- so nothing here knows
  a concrete broker, stream, repository or store;
* **its presentation facts are presentation facts.**  The selected API provider
  and whether the connection controls are enabled describe the workspace, not
  the application; the live market source is read through a provider on every
  repaint and never cached, because a stream that started after composition
  would otherwise be able to delete the credentials it is using.

One real sequencing defect was found and fixed while drawing that boundary:
:meth:`request_provider_switch` used to save and then switch *unconditionally*,
so a refused save still moved a live feed to a provider whose preference had
never been written.  Nothing is published here until the commit succeeded, and
the commit fact is published before the switch request.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Signal

from us_quant.config import AppConfig
from us_quant.credential_store import CredentialStoreError
from us_quant.desktop_credentials import DesktopCredentialService
from us_quant.desktop_settings import (
    BrokerConfigOwnerPort,
    DesktopSettingsCommit,
    DesktopSettingsService,
    RuntimeReconfigurationGuard,
)
from us_quant.desktop_v2.pages.system.settings.models import (
    CredentialDraft,
    SettingsDraft,
    SettingsPageView,
    api_provider_for_market_provider,
)
from us_quant.trading.ports.broker_account import BrokerAccountError
from us_quant.trading.ports.market_data import MarketDataActiveError
from us_quant.user_settings import UserPreferences, UserSettingsError

from .models import CredentialSaveOutcome
from .queries import (
    credential_save_plan,
    credential_status_text,
    preferences_from_draft,
    provider_label,
    provider_requires_api_key,
)

#: The dialog wording this capability publishes.  The decision to say any of it
#: is Settings'; the widget is the window's.
SAVE_FAILED_TITLE = "设置未保存"

NO_API_KEY_TITLE = "无需 API Key"
NO_API_KEY_MESSAGE = (
    "IBKR Gateway 使用本机 Host、端口和 Client ID，"
    "不在这里保存 API Key。"
)

NO_CHANGE_TITLE = "没有变化"
NO_CHANGE_MESSAGE = "所选数据源的输入框为空，没有修改已保存凭据。"

INCOMPLETE_TITLE = "凭据不完整"
INCOMPLETE_MESSAGE = "Alpaca 需要同时填写 API Key 和 API Secret。"

CREDENTIAL_SAVE_FAILED_TITLE = "凭据保存失败"

NO_CLEAR_TITLE = "无需清除"
NO_CLEAR_MESSAGE = "IBKR Gateway 没有保存在此处的 API Key。"

ACTIVE_SOURCE_TITLE = "行情运行中"
ACTIVE_SOURCE_MESSAGE = (
    "当前数据源正在使用这组凭据；请先切换或停止行情，再清除凭据。"
)

CREDENTIAL_CLEAR_FAILED_TITLE = "凭据清除失败"

CREDENTIAL_SAVED_LOG = "API 凭据已使用 Windows 当前用户 DPAPI 加密保存"

#: The two capability confirmations.  The wording is the operator-facing safety
#: explanation, kept verbatim from the retired handlers: a toggle only ever
#: edits a preference draft, and these say exactly what it does *not* authorise.
PAPER_CAPABILITY_TITLE = "开启 IBKR Paper 模拟下单能力"
PAPER_CAPABILITY_MESSAGE = (
    "这只打开客户端的 Paper 能力标志，不会立即下单，也不会"
    "自动武装策略。\n\n实际提交前仍必须满足：本机端口 4002、"
    "唯一 DU 模拟账户、fresh 实时行情、合格候选、单笔上限、"
    "会话内再次确认与武装。自动量化页武装后，策略信号可能"
    "自动向 IBKR Paper 提交 DAY 限价单。\n\nLive 账户、"
    "市场单、碎股、做空、借款和"
    "期权仍被禁止。是否保留开启状态？"
)

EXTENDED_HOURS_TITLE = "开启 IBKR Paper 5×24 扩展时段"
EXTENDED_HOURS_MESSAGE = (
    "这会让 Paper 自动量化按当前美东时段路由整股限价单："
    "盘前/盘后为 SMART + OutsideRth，隔夜为 OVERNIGHT。\n\n"
    "它不会立即下单，不会连接 Live，也不会绕过实时行情、"
    "策略、风控、DU 账户和逐会话确认。周末、休市和美东 "
    "03:50–04:00 维护窗口仍会拒绝订单。是否保留开启状态？"
)


class SettingsOrchestrator(QObject):
    """Owns the Settings workspace and renders the Settings page."""

    #: The operator must be told something.  The window owns the dialog.
    information_requested = Signal(str, str)

    #: Something the operator asked for failed, and the wording is ours.
    warning_requested = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: A theme preview, which the whole workbench applies.
    theme_preview_requested = Signal(str)

    #: The Settings page picked a market provider; the route should follow.
    market_provider_selection_requested = Signal(str)

    #: The preference commit succeeded and asked for a market switch.
    market_switch_requested = Signal(str)

    #: A finished settings transaction, carrying preferences and the new config.
    settings_committed = Signal(object)

    #: A capability toggle needs the operator's confirmation (title, message).
    paper_order_capability_confirmation_requested = Signal(str, str)

    #: The same for the extended-hours toggle.
    extended_hours_confirmation_requested = Signal(str, str)

    def __init__(
        self,
        *,
        page: object,
        settings_service: DesktopSettingsService,
        credential_service: DesktopCredentialService,
        initial_api_provider: str,
        current_config: Callable[[], AppConfig],
        broker_config: Callable[[], BrokerConfigOwnerPort | None],
        runtime_guards: Callable[[], Sequence[RuntimeReconfigurationGuard]],
        active_market_source_id: Callable[[], str | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._page = page
        self._settings_service = settings_service
        self._credential_service = credential_service
        self._current_config = current_config
        self._broker_config = broker_config
        self._runtime_guards = runtime_guards
        self._active_market_source_id = active_market_source_id
        # Presentation state, and only presentation state: which provider's
        # credentials the page is editing, and whether the connection controls
        # are currently open for editing.
        self._selected_api_provider = initial_api_provider
        self._connection_settings_enabled = True

    # -- read-only facts -------------------------------------------------

    @property
    def selected_api_provider(self) -> str:
        """The provider whose credentials the page is editing."""

        return self._selected_api_provider

    @property
    def connection_settings_enabled(self) -> bool:
        """Whether the connection controls accept input right now."""

        return self._connection_settings_enabled

    # -- rendering -------------------------------------------------------

    def render_current(self) -> None:
        """Draw one consistent Settings view from the current facts.

        Three reads, all live: the credential status from the service, the
        active market source from its provider, and this class's own two
        presentation facts.  Nothing is cached between repaints -- a cached
        status would survive a save or a clear, and a cached source id would let
        a stream that started later delete the credentials it is using.
        """

        provider = self._selected_api_provider
        requires_api_key = provider_requires_api_key(provider)
        self._page.render(
            SettingsPageView(
                credential_status_text=credential_status_text(
                    self._credential_service.status(provider)
                ),
                credential_save_enabled=requires_api_key,
                credential_clear_enabled=(
                    requires_api_key
                    and provider != self._active_market_source_id()
                ),
                connection_settings_enabled=(
                    self._connection_settings_enabled
                ),
            )
        )

    # -- provider selection ----------------------------------------------

    def select_api_provider(self, provider: str) -> None:
        """The operator pointed the credential provider combo."""

        self._selected_api_provider = provider
        self.render_current()

    def select_market_provider(self, provider: str) -> None:
        """The operator picked a market provider on the Settings page.

        Three local steps, then one publication.  The programmatic sync of the
        credential combo is silent by construction: a set that emitted would
        look like a second operator intent and the two combos would drive each
        other.  The selected API provider *does* follow here -- the operator
        chose a provider in order to configure it -- while
        :meth:`adopt_market_provider` deliberately does not, which is the
        asymmetry the retired window had.
        """

        api_provider = api_provider_for_market_provider(provider)
        self._selected_api_provider = api_provider
        self._page.set_api_provider(api_provider, emit_change=False)
        self.render_current()
        self.market_provider_selection_requested.emit(provider)

    def adopt_market_provider(self, provider: str) -> None:
        """The market route moved; point the Settings control at it, silently.

        A programmatic sync and nothing more: no intent is published, so the
        Settings combo cannot drive the route that just drove it.
        """

        self._page.set_market_provider(provider, emit_change=False)

    def set_connection_settings_enabled(self, enabled: bool) -> None:
        """Adopt the market route's published fact for the connection controls."""

        self._connection_settings_enabled = enabled
        self.render_current()

    def preview_theme(self, theme: str) -> None:
        """A theme preview is a Settings intent the whole workbench applies."""

        self.theme_preview_requested.emit(theme)

    # -- credentials -----------------------------------------------------

    def save_credentials(self, draft: CredentialDraft) -> None:
        """Sequence one Save-credentials click.

        The decision (which provider, how many halves, whether anything was
        typed) is a pure rule in :mod:`queries`; this method only turns the
        outcome into a message or a write.  A failed write keeps the operator's
        input on screen -- clearing it would hide what has to be retried -- and
        never reports success.
        """

        plan = credential_save_plan(
            draft.provider,
            api_key=draft.api_key,
            api_secret=draft.api_secret,
        )
        if plan.outcome is CredentialSaveOutcome.NOT_REQUIRED:
            self.information_requested.emit(
                NO_API_KEY_TITLE, NO_API_KEY_MESSAGE
            )
            return
        if plan.outcome is CredentialSaveOutcome.NO_CHANGE:
            self.information_requested.emit(
                NO_CHANGE_TITLE, NO_CHANGE_MESSAGE
            )
            return
        if plan.outcome is CredentialSaveOutcome.INCOMPLETE:
            self.warning_requested.emit(
                INCOMPLETE_TITLE, INCOMPLETE_MESSAGE
            )
            return
        try:
            self._credential_service.save_provider(
                plan.provider,
                api_key=plan.api_key,
                api_secret=plan.api_secret,
            )
        except (CredentialStoreError, OSError, ValueError) as error:
            self.warning_requested.emit(
                CREDENTIAL_SAVE_FAILED_TITLE, str(error)
            )
            return
        self._page.clear_credential_inputs()
        self.render_current()
        self.log_requested.emit(CREDENTIAL_SAVED_LOG)

    def clear_credentials(self, provider: str) -> None:
        """Sequence one Clear-credentials click.

        The live-source gate is read *now* and compared to the provider being
        cleared: deleting the blob a running stream is using would leave that
        stream holding a key that no longer exists on disk.  It is the
        selected provider's own identity that matters, so a different provider's
        credentials stay clearable while one stream runs.
        """

        if provider == self._active_market_source_id():
            self.warning_requested.emit(
                ACTIVE_SOURCE_TITLE, ACTIVE_SOURCE_MESSAGE
            )
            return
        if not provider_requires_api_key(provider):
            self.information_requested.emit(
                NO_CLEAR_TITLE, NO_CLEAR_MESSAGE
            )
            return
        try:
            self._credential_service.clear_provider(provider)
        except (CredentialStoreError, OSError, ValueError) as error:
            self.warning_requested.emit(
                CREDENTIAL_CLEAR_FAILED_TITLE, str(error)
            )
            return
        self._page.clear_credential_inputs()
        self.render_current()
        self.log_requested.emit(
            f"已清除当前 Windows 用户保存的 {provider_label(provider)}"
            " 行情凭据"
        )

    # -- preferences -----------------------------------------------------

    def save_preferences(self, draft: SettingsDraft) -> None:
        """Commit the operator's draft and publish the finished transaction.

        A refusal publishes the warning and nothing else: no commit fact, no
        cross-capability fan-out, and the page keeps the operator's input.
        """

        commit = self._commit(draft)
        if commit is None:
            return
        self.settings_committed.emit(commit)

    def request_provider_switch(self, draft: SettingsDraft) -> None:
        """Save the draft, then ask for the market switch -- in that order.

        The order is the fix for a real defect: the retired window saved,
        ignored whether the save succeeded, and then switched, so a refused save
        still reconfigured a live feed to a provider whose preference had never
        been written.  Here the switch request is published only after the
        commit succeeded, and the commit fact goes first so the composition root
        has adopted the new preferences before the market route reacts.
        """

        commit = self._commit(draft)
        if commit is None:
            return
        self.settings_committed.emit(commit)
        self.market_switch_requested.emit(
            commit.preferences.market_provider
        )

    def _commit(
        self, draft: SettingsDraft
    ) -> DesktopSettingsCommit | None:
        """Run the settings transaction, or report why it refused.

        The three exception types are the transaction's declared contract: the
        market-data and broker-account failures come from the *ports* those
        applications declare, and ``UserSettingsError`` is the preferences
        store's.  Catching them (rather than a bare ``Exception``) is what keeps
        a programming error visible instead of being reported to the operator as
        "not saved".
        """

        try:
            preferences: UserPreferences = preferences_from_draft(draft)
            return self._settings_service.commit(
                preferences,
                current_config=self._current_config(),
                broker_config=self._broker_config(),
                runtime_guards=self._runtime_guards(),
            )
        except (
            UserSettingsError,
            MarketDataActiveError,
            BrokerAccountError,
        ) as error:
            self.warning_requested.emit(SAVE_FAILED_TITLE, str(error))
            return None

    # -- capability toggles ----------------------------------------------
    #
    # Both toggles edit a *preference draft* and nothing else.  They are not
    # trading authorisation: the Paper capability flag only decides whether the
    # order controls may be offered, and the extended-hours flag only decides how
    # an already-authorized Paper order would be routed.  Neither reaches the
    # broker, the risk layer or the execution lease, and neither persists
    # anything until the operator saves the settings.

    def request_paper_order_capability_toggle(self, checked: bool) -> None:
        """Ask for the confirmation the enable direction needs."""

        if not checked:
            return
        self.paper_order_capability_confirmation_requested.emit(
            PAPER_CAPABILITY_TITLE, PAPER_CAPABILITY_MESSAGE
        )

    def confirm_paper_order_capability(self, accepted: bool) -> None:
        """Apply the operator's answer: a refusal reverts the control silently."""

        if accepted:
            return
        self._page.set_paper_order_capability(False, emit_change=False)

    def request_extended_hours_toggle(self, checked: bool) -> None:
        """Ask for the confirmation the enable direction needs."""

        if not checked:
            return
        self.extended_hours_confirmation_requested.emit(
            EXTENDED_HOURS_TITLE, EXTENDED_HOURS_MESSAGE
        )

    def confirm_extended_hours(self, accepted: bool) -> None:
        """Apply the operator's answer: a refusal reverts the control silently."""

        if accepted:
            return
        self._page.set_extended_hours_paper(False, emit_change=False)


__all__ = [
    "ACTIVE_SOURCE_MESSAGE",
    "ACTIVE_SOURCE_TITLE",
    "CREDENTIAL_CLEAR_FAILED_TITLE",
    "CREDENTIAL_SAVED_LOG",
    "CREDENTIAL_SAVE_FAILED_TITLE",
    "EXTENDED_HOURS_MESSAGE",
    "EXTENDED_HOURS_TITLE",
    "INCOMPLETE_MESSAGE",
    "INCOMPLETE_TITLE",
    "NO_API_KEY_MESSAGE",
    "NO_API_KEY_TITLE",
    "NO_CHANGE_MESSAGE",
    "NO_CHANGE_TITLE",
    "NO_CLEAR_MESSAGE",
    "NO_CLEAR_TITLE",
    "PAPER_CAPABILITY_MESSAGE",
    "PAPER_CAPABILITY_TITLE",
    "SAVE_FAILED_TITLE",
    "SettingsOrchestrator",
]
