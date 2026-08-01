import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.credential_store import WindowsCredentialStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class CredentialStoreTests(unittest.TestCase):
    def test_round_trip_is_encrypted_at_rest(self) -> None:
        with TemporaryDirectory() as directory:
            store = WindowsCredentialStore(Path(directory))
            path = store.save_secret(
                "finnhub_api_key", "unit-test-secret"
            )
            self.assertNotIn(
                b"unit-test-secret", path.read_bytes()
            )
            self.assertEqual(
                store.load_secret("finnhub_api_key"),
                "unit-test-secret",
            )
            store.delete_secret("finnhub_api_key")
            self.assertIsNone(
                store.load_secret("finnhub_api_key")
            )
