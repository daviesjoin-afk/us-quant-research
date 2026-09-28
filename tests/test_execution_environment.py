from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.adapters.ibkr.live_execution import IBKRLiveExecutionAdapter
from us_quant.trading.application.live_canary_execution import LiveCanaryExecutionGuard
from us_quant.trading.composition.execution import (
    build_execution_candidate_factory,
    build_live_execution_candidate_factory,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.execution_environment import (
    ExecutionDeploymentBlocker,
    ExecutionDeploymentError,
    evaluate_execution_deployment,
)
from us_quant.trading.domain.live_canary import LiveCanaryTruth
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveOperatorAuthorization,
    LiveRecoveryLatch,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_startup import LiveEndpointIdentity, LiveStartupProof


def _live_build_context():
    now = datetime.now(timezone.utc)
    fingerprint = LiveAccountFingerprint.from_identity(
        provider="IBKR",
        environment="LIVE",
        account_id="U1234567",
        endpoint_identity="ibkr-live-loopback:127.0.0.1:4001",
    )
    limits = LiveCanaryLimits(
        capital_limit=Decimal("1000"),
        max_order_notional=Decimal("250"),
        max_daily_loss=Decimal("100"),
        max_positions=1,
        max_open_orders=1,
        allowed_symbols=("AAPL",),
        allowed_strategy_versions=("strategy-v1",),
    )
    authorization = LiveOperatorAuthorization(
        authorization_id="authorization-1",
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        expected_account_fingerprint=fingerprint,
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=limits,
    )
    state = LiveAuthorizationState(authorization).request_session_arm(
        safety_revision=1,
        now=now,
        account_fingerprint=fingerprint,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    proof = LiveStartupProof.capture(
        now=now,
        broker_connected=True,
        observed_endpoint=LiveEndpointIdentity(),
        managed_account_ids=("U1234567",),
        broker_account_id="U1234567",
        connection_observed_at=now,
        account_truth_known=True,
        account_truth_observed_at=now,
        market_truth_known=True,
        market_truth_observed_at=now,
        open_orders_known=True,
        open_orders_observed_at=now,
        positions_known=True,
        positions_observed_at=now,
        reconciliation_clean=True,
        reconciliation_observed_at=now,
        authorization_state=state,
    )

    class _Truth:
        def snapshot(self):
            return LiveCanaryTruth(
                account_fingerprint=fingerprint,
                observed_at=now,
                net_liquidation=Decimal("5000"),
                daily_pnl=Decimal("0"),
                open_orders=(),
                positions=(),
            )

    return state, proof, _Truth()


def _live_safety_repository(tmp_path, state):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(
            revision=1,
            authorization=state.authorization,
            kill_latch=state.kill_latch,
        ),
    )
    return repository


@pytest.mark.parametrize(
    ("environment", "flag", "allowed", "blocker", "reason"),
    [
        (
            Environment.BACKTEST,
            False,
            False,
            ExecutionDeploymentBlocker.BACKTEST_HAS_NO_BROKER_CHANNEL,
            "backtest does not own a broker execution channel",
        ),
        (
            Environment.BACKTEST,
            True,
            False,
            ExecutionDeploymentBlocker.BACKTEST_HAS_NO_BROKER_CHANNEL,
            "backtest does not own a broker execution channel",
        ),
        (
            Environment.PAPER,
            False,
            True,
            ExecutionDeploymentBlocker.NONE,
            "Paper execution is served by the existing Paper-only adapter",
        ),
        (
            Environment.PAPER,
            True,
            True,
            ExecutionDeploymentBlocker.NONE,
            "Paper execution is served by the existing Paper-only adapter",
        ),
        (
            Environment.LIVE,
            False,
            False,
            ExecutionDeploymentBlocker.LIVE_FEATURE_DISABLED,
            "Live trading feature flag is disabled",
        ),
        (
            Environment.LIVE,
            True,
            False,
            ExecutionDeploymentBlocker.LIVE_CANARY_GATES_REQUIRED,
            "Live authorization, startup proof, and canary gates are required",
        ),
    ],
)
def test_deployment_truth_table(environment, flag, allowed, blocker, reason) -> None:
    decision = evaluate_execution_deployment(
        environment=environment,
        live_trading_enabled=flag,
    )

    assert decision.environment is environment
    assert decision.broker_submission_allowed is allowed
    assert decision.blocker is blocker
    assert (decision.blocker is ExecutionDeploymentBlocker.NONE) is allowed
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
        for _ in range(3):
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


def test_live_feature_flag_without_canary_context_cannot_construct_a_channel() -> None:
    with pytest.raises(ExecutionDeploymentError, match="authorization, startup proof"):
        build_live_execution_candidate_factory(
            environment=Environment.LIVE,
            live_trading_enabled=True,
        )


def test_live_feature_flag_remains_required_when_all_canary_facts_are_present() -> None:
    state, proof, truth = _live_build_context()
    with pytest.raises(ExecutionDeploymentError, match="feature flag is disabled"):
        build_live_execution_candidate_factory(
            environment=Environment.LIVE,
            live_trading_enabled=False,
            live_authorization_state=lambda _record: state,
            live_startup_proof=lambda: proof,
            live_truth=truth,
        )


def test_backtest_never_selects_live_even_with_authorization_context() -> None:
    state, proof, truth = _live_build_context()
    with pytest.raises(ExecutionDeploymentError, match="environment=LIVE"):
        build_live_execution_candidate_factory(
            environment=Environment.BACKTEST,
            live_trading_enabled=True,
            live_authorization_state=lambda _record: state,
            live_startup_proof=lambda: proof,
            live_truth=truth,
        )


def test_live_composition_wraps_only_the_concrete_live_adapter_in_the_canary_guard(
    tmp_path,
) -> None:
    state, proof, truth = _live_build_context()
    factory = build_live_execution_candidate_factory(
        environment=Environment.LIVE,
        live_trading_enabled=True,
        live_authorization_state=lambda _record: state,
        live_safety_repository=_live_safety_repository(tmp_path, state),
        live_startup_proof=lambda: proof,
        live_truth=truth,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4001,
        client_id=94,
        api_read_only=False,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=3,
    )

    channel = factory(config, repository=repository)

    assert isinstance(channel, LiveCanaryExecutionGuard)
    assert isinstance(channel._broker, IBKRLiveExecutionAdapter)
    assert channel._broker.config.port == 4001


def test_live_composition_rejects_paper_profile_and_extended_hours(tmp_path) -> None:
    state, proof, truth = _live_build_context()
    factory = build_live_execution_candidate_factory(
        environment=Environment.LIVE,
        live_trading_enabled=True,
        live_authorization_state=lambda _record: state,
        live_safety_repository=_live_safety_repository(tmp_path, state),
        live_startup_proof=lambda: proof,
        live_truth=truth,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    paper_config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=95,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=3,
    )
    live_config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4001,
        client_id=96,
        api_read_only=False,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=3,
    )

    with pytest.raises(ExecutionDeploymentError, match="port 4001"):
        factory(paper_config, repository=repository)
    with pytest.raises(ExecutionDeploymentError, match="DAY orders only"):
        factory(live_config, repository=repository, extended_hours_enabled=True)


@pytest.mark.parametrize("invalid", [1, 0, "true", None])
def test_invalid_live_feature_flag_is_rejected(invalid) -> None:
    with pytest.raises(ExecutionDeploymentError, match="feature flag"):
        evaluate_execution_deployment(
            environment=Environment.LIVE,
            live_trading_enabled=invalid,
        )


def test_deployment_decision_is_immutable() -> None:
    decision = evaluate_execution_deployment(
        environment=Environment.LIVE,
        live_trading_enabled=True,
    )
    with pytest.raises((AttributeError, TypeError)):
        decision.broker_submission_allowed = True


def test_live_composition_constructs_recovery_channel_while_barrier_is_persisted(
    tmp_path,
) -> None:
    state, proof, truth = _live_build_context()
    safety_repository = _live_safety_repository(tmp_path, state)
    current = safety_repository.load()
    safety_repository.save(
        expected_revision=current.revision,
        replacement=LiveSafetyRecord(
            current.revision + 1,
            current.authorization,
            current.kill_latch,
            LiveRecoveryLatch().require(
                at=datetime.now(timezone.utc), reason="test disconnect"
            ),
        ),
    )
    factory = build_live_execution_candidate_factory(
        environment=Environment.LIVE,
        live_trading_enabled=True,
        live_authorization_state=lambda _record: state,
        live_safety_repository=safety_repository,
        live_startup_proof=lambda: proof,
        live_truth=truth,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4001,
        client_id=98,
        api_read_only=False,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=3,
    )

    expected_broker = object()
    with patch(
        "us_quant.trading.composition.execution.IBKRLiveExecutionAdapter",
        return_value=expected_broker,
    ) as adapter:
        channel = factory(config, repository=repository)

    assert isinstance(channel, LiveCanaryExecutionGuard)
    assert channel._broker is expected_broker
    adapter.assert_called_once()
    assert safety_repository.load().recovery_latch.is_required


def test_live_composition_latches_fresh_reconciliation_on_process_start(tmp_path) -> None:
    state, proof, truth = _live_build_context()
    safety_repository = _live_safety_repository(tmp_path, state)
    factory = build_live_execution_candidate_factory(
        environment=Environment.LIVE,
        live_trading_enabled=True,
        live_authorization_state=lambda _record: state,
        live_safety_repository=safety_repository,
        live_startup_proof=lambda: proof,
        live_truth=truth,
    )
    repository = SQLiteOrderRepository(tmp_path / "orders.sqlite3")
    config = IBKRConnectionConfig(
        host="127.0.0.1",
        port=4001,
        client_id=99,
        api_read_only=False,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=3,
    )

    with patch(
        "us_quant.trading.composition.execution.IBKRLiveExecutionAdapter",
        return_value=object(),
    ):
        factory(config, repository=repository)

    durable = safety_repository.load()
    assert durable.recovery_latch.is_required
    assert durable.recovery_latch.reason == (
        "Live process start requires fresh broker reconciliation"
    )
