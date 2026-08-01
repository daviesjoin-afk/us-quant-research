from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from us_quant.user_settings import (
    UserPreferences,
    UserPreferencesStore,
    UserSettingsError,
)


class UserSettingsTests(unittest.TestCase):
    def test_round_trip_has_no_stock_pool_or_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "preferences.json"
            store = UserPreferencesStore(path)
            saved = store.save(
                UserPreferences(
                    theme="light",
                    market_provider="alpaca_iex",
                    ibkr_client_id=23,
                    paper_order_capability_enabled=True,
                    extended_hours_paper_enabled=True,
                )
            )
            loaded = store.load()
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded, saved)
        self.assertEqual(payload["theme"], "light")
        self.assertTrue(payload["paper_order_capability_enabled"])
        self.assertTrue(payload["extended_hours_paper_enabled"])
        self.assertNotIn("watchlist", payload)
        self.assertFalse(
            any(
                "key" in key or "secret" in key or "token" in key
                for key in payload
            )
        )

    def test_legacy_watchlist_is_ignored_and_removed_on_save(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "preferences.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "theme": "light",
                        "market_provider": "finnhub_trades",
                        "watchlist": "BABA,SPY,TSLA",
                    }
                ),
                encoding="utf-8",
            )
            store = UserPreferencesStore(path)
            loaded = store.load()
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(hasattr(loaded, "watchlist"))
        self.assertNotIn("watchlist", payload)

    def test_corrupt_file_falls_back_to_safe_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text("{not-json", encoding="utf-8")
            loaded = UserPreferencesStore(path).load()
        self.assertEqual(loaded.theme, "dark")
        self.assertEqual(loaded.ibkr_port, 4002)

    def test_live_or_remote_ibkr_settings_are_rejected(self) -> None:
        with self.assertRaises(UserSettingsError):
            UserPreferences(ibkr_port=4001).validated()
        with self.assertRaises(UserSettingsError):
            UserPreferences(ibkr_host="192.168.1.10").validated()


if __name__ == "__main__":
    unittest.main()
