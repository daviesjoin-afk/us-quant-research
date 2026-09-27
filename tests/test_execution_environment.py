from __future__ import annotations

from unittest.mock import patch

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.composition.execution import (
    build_execution_candidate_factory,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.execution_environment import (
    ExecutionDeploymentError,
    evaluate_execution_deployment,
)


@pytest.mark.parametrize(
    ("environment", "flag", "allowed", "reason"),
    [
        (
            Environment.BACKTEST,
            False,
            False,
            "backtest does not own a broker execution channel",
        ),
        (
            Environment.BACKTEST,
            True,
            False,
            "backtest does not own a broker execution channel",
        ),
        (
            Environment.PAPER,
            False,
            True,
            "Paper execution is served by the existing Paper-only adapter",
        ),
        (
            Environment.PAPER,
            True,
            True,
            "Paper execution is served by the existing Paper-only adapter",
        ),
        (
            Environment.LIVE,
            False,
            False,
            "Live trading feature flag is disabled",
        ),
        (
            Environment.LIVE,
            True,
            False,
            "Live execution is not implemented in Stage 3-A",
        ),
    ],
)
def test_deployment_truth_table(environment, flag, allowed, reason) -> None:
    decision = evaluate_execution_deployment(
        environment=environment,
        live_trading_enabled=flag,
    )

    assert decision.environment is environment
    assert decision.broker_submission_allowed is allowed
    assert decision.reason == reason


@pytest.mark.parametrize("invalid", ["live", None, object()])
def test_unknown_environment_is_rejected(invalid) -> None:
    with pytest.raises(ExecutionDeploymentError, match="unsupported"):
        evaluate_execution_deployment(
            environment=invalid,
            live_trading_enabled=True,
        )


@pytest.mark.parametrize(
    ("environment", "flag"),
    [
        (Environment.BACKTEST, False),
        (Environment.LIVE, False),
        (Environment.LIVE, True),
    ],
)
def test_denied_deployment_never_constructs_paper_adapter(
    tmp_path, environment, flag
) -> None:
    factory = build_execution_candidate_factory(
        environment=environment,
        live_trading_enabled=flag,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=91,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=3,
    )

    with patch(
        "us_quant.trading.composition.execution.IBKRExecutionAdapter"
    ) as adapter:
        with pytest.raises(ExecutionDeploymentError):
            factory(config, repository=repository)

    adapter.assert_not_called()


def test_paper_factory_selects_only_existing_paper_adapter(tmp_path) -> None:
    factory = build_execution_candidate_factory(
        environment=Environment.PAPER,
        live_trading_enabled=True,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=92,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=3,
    )
    expected = object()

    with patch(
        "us_quant.trading.composition.execution.IBKRExecutionAdapter",
        return_value=expected,
    ) as adapter:
        actual = factory(
            config,
            repository=repository,
            extended_hours_enabled=True,
        )

    assert actual is expected
    adapter.assert_called_once_with(
        config,
        repository=repository,
        extended_hours_enabled=True,
    )


def test_invalid_live_feature_flag_is_rejected() -> None:
    with pytest.raises(ExecutionDeploymentError, match="feature flag"):
        evaluate_execution_deployment(
            environment=Environment.LIVE,
            live_trading_enabled=1,
        )
