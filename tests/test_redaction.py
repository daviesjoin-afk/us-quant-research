from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.redaction import redact_text, sanitize_value
from us_quant.runtime_events import RuntimeEventStore


class RedactionTests(unittest.TestCase):
    def test_common_header_json_and_query_secrets_are_redacted(self) -> None:
        value = (
            "Authorization: Bearer abc.def "
            "access_token=TOKEN123 "
            "url=https://example.test/?api_key=KEY123&x=1 "
            '"client_secret":"SECRET123"'
        )
        redacted = redact_text(value)
        for secret in ("abc.def", "TOKEN123", "KEY123", "SECRET123"):
            self.assertNotIn(secret, redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 4)

    def test_runtime_event_is_sanitized_before_database_write(self) -> None:
        with TemporaryDirectory() as directory:
            store = RuntimeEventStore(Path(directory) / "events.sqlite3")
            created = store.add(
                severity="error",
                component="test",
                code="AUTH",
                message="Authorization: Bearer DO_NOT_STORE",
            )
            loaded = store.list_recent(1)[0]
        self.assertNotIn("DO_NOT_STORE", created.message)
        self.assertNotIn("DO_NOT_STORE", loaded.message)

    def test_nested_sensitive_keys_are_removed(self) -> None:
        sanitized = sanitize_value(
            {"api_key": "ONE", "nested": {"access_token": "TWO"}}
        )
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(
            sanitized["nested"]["access_token"], "[REDACTED]"
        )


if __name__ == "__main__":
    unittest.main()
