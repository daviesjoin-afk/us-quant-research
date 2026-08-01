from __future__ import annotations

import errno
import os
import unittest

from us_quant.proxy_support import (
    DEFAULT_PROXY,
    proxy_unreachable,
    resolve_proxy,
)


class ProxySupportTests(unittest.TestCase):
    def test_default_proxy_points_at_local_clash(self) -> None:
        self.assertEqual(DEFAULT_PROXY, "http://127.0.0.1:7897")

    def test_resolve_proxy_uses_environment_first(self) -> None:
        old_upper = os.environ.pop("HTTPS_PROXY", None)
        old_lower = os.environ.pop("https_proxy", None)
        try:
            self.assertEqual(resolve_proxy(), DEFAULT_PROXY)
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
            self.assertEqual(
                resolve_proxy(), "http://127.0.0.1:8080"
            )
            os.environ.pop("HTTPS_PROXY")
            os.environ["https_proxy"] = "http://127.0.0.1:8081"
            self.assertEqual(
                resolve_proxy(), "http://127.0.0.1:8081"
            )
        finally:
            if old_upper is not None:
                os.environ["HTTPS_PROXY"] = old_upper
            if old_lower is not None:
                os.environ["https_proxy"] = old_lower

    def test_proxy_unreachable_detects_refused_connection(self) -> None:
        self.assertTrue(proxy_unreachable(ConnectionRefusedError()))
        refused = OSError()
        refused.errno = errno.ECONNREFUSED
        self.assertTrue(proxy_unreachable(refused))
        self.assertTrue(
            proxy_unreachable(
                OSError("Proxy CONNECT failed: Connection refused")
            )
        )

    def test_proxy_unreachable_ignores_remote_failures(self) -> None:
        self.assertFalse(proxy_unreachable(TimeoutError("timed out")))
        self.assertFalse(
            proxy_unreachable(
                ConnectionError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
            )
        )
        unrelated = OSError()
        unrelated.errno = errno.ENOTCONN
        self.assertFalse(proxy_unreachable(unrelated))


if __name__ == "__main__":
    unittest.main()
