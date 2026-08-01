from unittest.mock import MagicMock, patch
import unittest
from time import monotonic, sleep

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
    probe_ibkr_socket,
)


class IBKRProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = IBKRConnectionConfig(
            host="127.0.0.1",
            port=4002,
            client_id=17,
            api_read_only=True,
            paper_order_submission_enabled=False,
            connection_timeout_seconds=2,
        )

    @patch("us_quant.ibkr.socket.create_connection")
    def test_reports_reachable_local_gateway(
        self, create_connection: MagicMock
    ) -> None:
        create_connection.return_value = MagicMock()
        result = probe_ibkr_socket(self.config)
        self.assertTrue(result.reachable)
        create_connection.assert_called_once_with(
            ("127.0.0.1", 4002),
            timeout=2,
        )

    @patch("us_quant.ibkr.socket.create_connection")
    def test_reports_closed_gateway_port(
        self, create_connection: MagicMock
    ) -> None:
        create_connection.side_effect = ConnectionRefusedError(
            "connection refused"
        )
        result = probe_ibkr_socket(self.config)
        self.assertFalse(result.reachable)
        self.assertIn("not reachable", result.detail)

    def test_rejects_remote_host_during_paper_build(self) -> None:
        with self.assertRaises(ValueError):
            IBKRConnectionConfig(
                host="192.0.2.1",
                port=4002,
                client_id=17,
                api_read_only=True,
                paper_order_submission_enabled=False,
                connection_timeout_seconds=2,
            )

    def test_protocol_handshake_is_bounded(self) -> None:
        class BlockingClient:
            def __init__(self) -> None:
                self.disconnected = False

            def connect(
                self, host: str, port: int, client_id: int
            ) -> None:
                del host, port, client_id
                while not self.disconnected:
                    sleep(0.01)

            def disconnect(self) -> None:
                self.disconnected = True

            def isConnected(self) -> bool:
                return False

        config = IBKRConnectionConfig(
            host="127.0.0.1",
            port=4002,
            client_id=1,
            api_read_only=True,
            paper_order_submission_enabled=False,
            connection_timeout_seconds=0.1,
        )
        started = monotonic()
        with self.assertRaises(IBKRClientConnectError):
            connect_ibkr_client(BlockingClient(), config)
        self.assertLess(monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()
