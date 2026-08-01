"""Proxy support for external market-data websockets.

All external feeds (Finnhub, Alpaca) run through a local Clash-compatible
HTTP CONNECT proxy by default (127.0.0.1:7897), so users behind restrictive
networks can reach the feeds without touching code.  An explicit
``HTTPS_PROXY``/``https_proxy`` environment variable always wins over the
default.  When the proxy itself is unreachable (e.g. Clash is not running)
the stream automatically falls back to a direct connection for the rest of
its lifetime instead of failing forever against a dead proxy.
"""

from __future__ import annotations

import errno
import os


DEFAULT_PROXY = "http://127.0.0.1:7897"


def resolve_proxy() -> str | None:
    """Return the proxy for external websocket feeds.

    Priority: ``HTTPS_PROXY``/``https_proxy`` environment variable, then the
    built-in Clash default (``127.0.0.1:7897``).  Returns ``None`` only when
    both are unavailable, which cannot happen unless the env var is set to an
    empty value and the default is disabled.
    """

    environment_value = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    ).strip()
    if environment_value:
        return environment_value
    return DEFAULT_PROXY


def proxy_unreachable(error: Exception) -> bool:
    """Return whether ``error`` means the proxy itself is not reachable.

    A refused/unreachable proxy should switch the stream to a direct
    connection, while any error *through* the proxy (timeout, TLS reset)
    must keep the proxy because the direct route is likely blocked.
    """

    if isinstance(error, ConnectionRefusedError):
        return True
    if isinstance(error, OSError) and error.errno in {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EADDRNOTAVAIL,
    }:
        return True
    text = str(error).casefold()
    return (
        "connection refused" in text
        or ("proxy" in text and "refused" in text)
        or ("proxy" in text and "unreachable" in text)
    )
