import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)


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

    def test_has_secret_tracks_the_encrypted_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = WindowsCredentialStore(root)
            self.assertFalse(store.has_secret("finnhub_api_key"))

            (root / "finnhub_api_key.dpapi").write_bytes(b"not-a-blob")
            self.assertTrue(store.has_secret("finnhub_api_key"))

            store.delete_secret("finnhub_api_key")
            self.assertFalse(store.has_secret("finnhub_api_key"))

    def test_has_secret_survives_a_blob_that_cannot_be_decrypted(
        self,
    ) -> None:
        """The two operations disagree on purpose.

        A corrupt blob -- or one written by another Windows user -- makes
        ``load_secret`` raise, but presence is still presence.  Reporting
        "saved" must not depend on being able to decrypt, or a status
        line would become an error.
        """

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = WindowsCredentialStore(root)
            (root / "finnhub_api_key.dpapi").write_bytes(
                b"definitely not base64 ciphertext"
            )

            self.assertTrue(store.has_secret("finnhub_api_key"))
            with self.assertRaises(CredentialStoreError):
                store.load_secret("finnhub_api_key")

    def test_has_secret_reuses_the_name_sanitisation(self) -> None:
        with TemporaryDirectory() as directory:
            store = WindowsCredentialStore(Path(directory))

            for bad_name in ("../foo", "", "  ", "a/b", "a\\b"):
                with self.subTest(name=bad_name):
                    with self.assertRaises(ValueError):
                        store.has_secret(bad_name)

    def test_has_secret_normalises_like_the_other_operations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = WindowsCredentialStore(root)
            (root / "finnhub_api_key.dpapi").write_bytes(b"x")

            self.assertTrue(store.has_secret("  FINNHUB_API_KEY  "))

    def test_has_secret_is_the_presence_check_it_was_specified_as(
        self,
    ) -> None:
        """The body is pinned, so a change here has to be deliberate.

        ``exists()`` and ``is_file()`` behave identically for every state
        this store can produce (``save_secret`` writes a regular file,
        ``delete_secret`` unlinks one), so no behavioural test can tell
        them apart.  Pinning the two statements keeps the shape honest:
        sanitised name, presence check, and no decryption attempt.
        """

        tree = ast.parse(
            Path(
                __import__("us_quant.credential_store", fromlist=["x"])
                .__file__
            ).read_text(encoding="utf-8")
        )
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "has_secret"
        )

        body = [
            node
            for node in method.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        self.assertEqual(len(body), 2, ast.dump(method))
        assignment, check = body
        self.assertIsInstance(assignment, ast.Assign)
        self.assertEqual(
            ast.unparse(assignment.value),
            "self.root / f'{_clean_name(name)}.dpapi'",
        )
        self.assertEqual(
            ast.unparse(check), "return target.exists()"
        )
