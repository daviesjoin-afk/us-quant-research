from __future__ import annotations

from dataclasses import dataclass
import socket
from threading import Event, Thread
from time import monotonic
from typing import Any


class IBKRClientConnectError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IBKRConnectionConfig:
    host: str
    port: int
    client_id: int
    api_read_only: bool
    paper_order_submission_enabled: bool
    connection_timeout_seconds: float

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Phase 2 IBKR connections must use loopback")
        if not 1 <= self.port <= 65535:
            raise ValueError("IBKR socket port must be in [1, 65535]")
        if self.client_id < 0:
            raise ValueError("IBKR client ID cannot be negative")
        if self.connection_timeout_seconds <= 0:
            raise ValueError("IBKR connection timeout must be positive")
        if self.paper_order_submission_enabled and self.api_read_only:
            raise ValueError(
                "paper order submission cannot be enabled while the API "
                "is read-only"
            )


@dataclass(frozen=True, slots=True)
class IBKRProbeResult:
    reachable: bool
    host: str
    port: int
    elapsed_ms: int
    detail: str


def connect_ibkr_client(
    client: Any,
    config: IBKRConnectionConfig,
    *,
    client_id: int | None = None,
    stop_event: Event | None = None,
) -> None:
    """Bound the official API's otherwise unbounded handshake loop."""

    completed = Event()
    failures: list[BaseException] = []

    def connect() -> None:
        try:
            client.connect(
                config.host,
                config.port,
                (
                    config.client_id
                    if client_id is None
                    else client_id
                ),
            )
        except BaseException as error:
            failures.append(error)
        finally:
            completed.set()

    thread = Thread(
        target=connect,
        name="ibkr-api-connect",
        daemon=True,
    )
    thread.start()
    deadline = monotonic() + config.connection_timeout_seconds
    while not completed.is_set():
        if stop_event is not None and stop_event.is_set():
            _disconnect_ibkr_client(client)
            thread.join(timeout=1)
            raise IBKRClientConnectError(
                "IBKR API connection was cancelled"
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            _disconnect_ibkr_client(client)
            thread.join(timeout=1)
            raise IBKRClientConnectError(
                "IBKR API protocol handshake timed out"
            )
        completed.wait(min(0.1, remaining))
    if failures:
        raise IBKRClientConnectError(
            f"IBKR API connection failed: {failures[0]}"
        ) from failures[0]
    if not client.isConnected():
        raise IBKRClientConnectError(
            "IBKR API socket closed before protocol handshake"
        )


def _disconnect_ibkr_client(client: Any) -> None:
    try:
        client.disconnect()
    except Exception:
        pass


def probe_ibkr_socket(config: IBKRConnectionConfig) -> IBKRProbeResult:
    """Check only whether the local Gateway/TWS socket is listening.

    This does not perform the IBKR protocol handshake, authenticate, read
    account data, or submit an order.
    """

    started = monotonic()
    try:
        with socket.create_connection(
            (config.host, config.port),
            timeout=config.connection_timeout_seconds,
        ):
            reachable = True
            detail = "local socket is accepting connections"
    except OSError as error:
        reachable = False
        detail = f"local socket is not reachable: {error}"

    return IBKRProbeResult(
        reachable=reachable,
        host=config.host,
        port=config.port,
        elapsed_ms=round((monotonic() - started) * 1000),
        detail=detail,
    )
