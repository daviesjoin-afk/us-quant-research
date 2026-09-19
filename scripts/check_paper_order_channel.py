"""Read-only diagnostic for the IBKR Paper order channel.

Connects to a locally running IB Gateway, reads the account and the broker's
open-order count, prints one JSON object and disconnects.  It is a *diagnostic*
and stays one:

* it never arms the session, so ``reserve`` and ``submit`` are unreachable and
  ``orders_submitted`` is always ``0``;
* it writes to a temporary order store, so a run cannot touch the real one;
* it connects, reads and disconnects -- nothing else.

The channel is built through the execution composition root, which is the same
assembly production uses, so what this script observes is the channel the app
actually runs rather than a second copy of it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.composition.execution import (
    build_execution_candidate,
    build_order_repository,
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
        repository = build_order_repository(
            Path(directory) / "paper_channel_check.sqlite3"
        )
        service = build_execution_candidate(config, repository=repository)
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