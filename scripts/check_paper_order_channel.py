from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderService,
    PaperOrderJournal,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostic for the IBKR Paper order channel."
    )
    parser.add_argument("--client-id", type=int, default=217)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=args.client_id,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=args.timeout,
    )
    with TemporaryDirectory() as directory:
        service = IBKRPaperOrderService(
            config,
            journal=PaperOrderJournal(
                Path(directory) / "paper_channel_check.sqlite3"
            ),
        )
        try:
            connection = service.connect()
            state = service.broker_state()
            print(
                json.dumps(
                    {
                        "connected": connection.connected,
                        "account": connection.account_alias,
                        "net_liquidation": (
                            str(state.net_liquidation)
                            if state.net_liquidation is not None
                            else None
                        ),
                        "cash": (
                            str(state.cash)
                            if state.cash is not None
                            else None
                        ),
                        "positions": len(state.positions),
                        "open_api_orders": (
                            connection.open_broker_orders
                        ),
                        "unreconciled_local_orders": (
                            connection.unreconciled_local_orders
                        ),
                        "orders_submitted": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
        finally:
            service.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
