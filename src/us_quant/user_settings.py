from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile


SETTINGS_SCHEMA_VERSION = 1
ALLOWED_THEMES = frozenset({"dark", "light"})
ALLOWED_MARKET_PROVIDERS = frozenset(
    {"finnhub_trades", "alpaca_iex", "ibkr", "ibkr_extended"}
)
ALLOWED_IBKR_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PAPER_GATEWAY_PORT = 4002


class UserSettingsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UserPreferences:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    theme: str = "dark"
    market_provider: str = "finnhub_trades"
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = PAPER_GATEWAY_PORT
    ibkr_client_id: int = 71
    connection_timeout_seconds: float = 15.0
    paper_order_capability_enabled: bool = False
    extended_hours_paper_enabled: bool = False

    def validated(self) -> UserPreferences:
        if self.schema_version != SETTINGS_SCHEMA_VERSION:
            raise UserSettingsError("unsupported settings schema")
        if self.theme not in ALLOWED_THEMES:
            raise UserSettingsError("theme must be dark or light")
        if self.market_provider not in ALLOWED_MARKET_PROVIDERS:
            raise UserSettingsError("unsupported market data provider")
        host = self.ibkr_host.strip().lower()
        if host not in ALLOWED_IBKR_HOSTS:
            raise UserSettingsError(
                "IBKR Paper 只允许连接本机 Gateway"
            )
        if self.ibkr_port != PAPER_GATEWAY_PORT:
            raise UserSettingsError(
                "首期只允许 IBKR Paper Gateway 端口 4002"
            )
        if not 1 <= self.ibkr_client_id <= 999_999:
            raise UserSettingsError("IBKR client ID must be in 1..999999")
        if not 1 <= self.connection_timeout_seconds <= 120:
            raise UserSettingsError(
                "connection timeout must be in 1..120 seconds"
            )
        if not isinstance(self.paper_order_capability_enabled, bool):
            raise UserSettingsError(
                "paper order capability flag must be boolean"
            )
        if not isinstance(self.extended_hours_paper_enabled, bool):
            raise UserSettingsError(
                "extended-hours Paper flag must be boolean"
            )
        return UserPreferences(
            schema_version=SETTINGS_SCHEMA_VERSION,
            theme=self.theme,
            market_provider=self.market_provider,
            ibkr_host=host,
            ibkr_port=PAPER_GATEWAY_PORT,
            ibkr_client_id=self.ibkr_client_id,
            connection_timeout_seconds=self.connection_timeout_seconds,
            paper_order_capability_enabled=(
                self.paper_order_capability_enabled
            ),
            extended_hours_paper_enabled=(
                self.extended_hours_paper_enabled
            ),
        )


class UserPreferencesStore:
    """Atomic, non-secret preferences stored under the writable state root."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(
        self, defaults: UserPreferences | None = None
    ) -> UserPreferences:
        fallback = (defaults or UserPreferences()).validated()
        if not self.path.exists():
            return fallback
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise UserSettingsError("settings payload must be an object")
            merged = {
                **asdict(fallback),
                **{
                    key: payload[key]
                    for key in asdict(fallback)
                    if key in payload
                },
            }
            preferences = UserPreferences(**merged).validated()
            allowed_keys = set(asdict(fallback))
            if set(payload) - allowed_keys:
                try:
                    self.save(preferences)
                except UserSettingsError:
                    pass
            return preferences
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UserSettingsError,
        ):
            return fallback

    def save(self, preferences: UserPreferences) -> UserPreferences:
        safe = preferences.validated()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            asdict(safe),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise UserSettingsError(
                f"unable to save preferences: {error}"
            ) from error
        return safe
