"""CLI for the read-only supervised Paper canary readiness projection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from us_quant.config import load_config
from us_quant.paths import ApplicationPaths
from us_quant.trading.application.paper_canary_readiness import (
    BrokerCheckProjection,
    PaperCanaryEvidenceTarget,
    PaperCanaryInspectionSpec,
)
from us_quant.trading.application.portfolio_reconciliation import (
    DEFAULT_MAX_RECONCILIATION_SNAPSHOT_AGE,
)
from us_quant.trading.composition.paper_canary_readiness import (
    build_live_broker_account_application,
    build_paper_canary_readiness_application,
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    paths = ApplicationPaths.discover()
    parser = argparse.ArgumentParser(
        description="Read-only supervised Paper canary readiness inspector"
    )
    parser.add_argument("--strategy-version", action="append", required=True)
    parser.add_argument(
        "--evidence-target", action="append", required=True, help="VERSION_ID:SYMBOL"
    )
    parser.add_argument("--provider", default="IBKR")
    parser.add_argument("--expected-runtime-revision")
    parser.add_argument("--config", type=Path, default=paths.config_path)
    parser.add_argument("--live-broker-check", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def resolve_git_head() -> str | None:
    """Resolve the checkout revision in the CLI layer only."""
    try:
        cwd = ApplicationPaths.discover().resource_root
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status.stdout.strip():
            return None
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _broker_projection(config, *, live_check: bool) -> BrokerCheckProjection | None:
    if not live_check:
        return None
    broker_config = config.ibkr
    account_application = build_live_broker_account_application(broker_config)
    try:
        portfolio = account_application.refresh(
            timeout_seconds=broker_config.connection_timeout_seconds
        )
    except Exception as error:  # noqa: BLE001 - failed broker reads become a diagnostic
        return BrokerCheckProjection(
            checked=True,
            refresh_succeeded=False,
            configured_environment=config.environment.value,
            configured_host=broker_config.host,
            configured_port=broker_config.port,
            configured_client_id=broker_config.client_id,
            paper_order_submission_enabled=broker_config.paper_order_submission_enabled,
            api_read_only=broker_config.api_read_only,
            last_error=type(error).__name__,
        )
    now = datetime.now(UTC)
    account = portfolio.account
    age = (now - account.observed_at.astimezone(UTC)).total_seconds()
    freshness = (
        "FRESH"
        if 0 <= age <= DEFAULT_MAX_RECONCILIATION_SNAPSHOT_AGE.total_seconds()
        else "STALE"
    )
    return BrokerCheckProjection(
        checked=True,
        refresh_succeeded=True,
        configured_environment=config.environment.value,
        configured_host=broker_config.host,
        configured_port=broker_config.port,
        configured_client_id=broker_config.client_id,
        paper_order_submission_enabled=broker_config.paper_order_submission_enabled,
        api_read_only=broker_config.api_read_only,
        account_alias=account.account_alias,
        broker_environment=account.environment.value,
        observed_at=account.observed_at,
        freshness=freshness,
        age_seconds=age,
        net_liquidation_available=(
            account.net_liquidation is not None and account.net_liquidation > 0
        ),
        cash_available=account.cash is not None and account.cash > 0,
    )


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _parse_spec(args: argparse.Namespace) -> PaperCanaryInspectionSpec:
    targets = []
    for value in args.evidence_target:
        version_id, separator, symbol = value.partition(":")
        if not separator or not version_id.strip() or not symbol.strip():
            raise ValueError("--evidence-target must use VERSION_ID:SYMBOL")
        targets.append(
            PaperCanaryEvidenceTarget(
                strategy_version_id=version_id.strip(),
                symbol=symbol.strip(),
            )
        )
    return PaperCanaryInspectionSpec(
        strategy_version_ids=tuple(args.strategy_version),
        evidence_targets=tuple(targets),
        provider=args.provider,
        expected_runtime_revision=args.expected_runtime_revision,
    )


def _print_text(report) -> None:
    print(f"overall_status = {report.overall_status.value}")
    for strategy in report.strategies:
        print(
            f"{strategy.version_id}  {strategy.status}/{strategy.mode}  "
            f"paper_launch_authorized={str(strategy.paper_launch_authorized).lower()}  "
            f"performance={strategy.paper_performance_status.value}"
        )
    for item in report.evidence_targets:
        print(
            f"{item.strategy_version_id}/{item.symbol}  "
            f"{item.review_ready_sessions}/{item.required_sessions}  {item.status}"
        )
    print(f"portfolio_plan = {report.portfolio_plan.status}")
    print(f"broker_check = {'CHECKED' if report.broker_check.checked else 'NOT_RUN'}")
    if report.broker_check.checked:
        print(
            "broker_config = "
            f"{report.broker_check.configured_environment}@"
            f"{report.broker_check.configured_host}:"
            f"{report.broker_check.configured_port} "
            f"client_id={report.broker_check.configured_client_id} "
            f"paper_orders_enabled={str(report.broker_check.paper_order_submission_enabled).lower()} "
            f"api_read_only={str(report.broker_check.api_read_only).lower()} "
            f"net_liquidation_available={str(report.broker_check.net_liquidation_available).lower()} "
            f"cash_available={str(report.broker_check.cash_available).lower()}"
        )
        print(
            f"broker_truth = {report.broker_check.broker_environment or 'UNKNOWN'} "
            f"account={report.broker_check.account_alias or 'UNKNOWN'} "
            f"freshness={report.broker_check.freshness} "
            f"observed_at={report.broker_check.observed_at or 'UNKNOWN'} "
            f"last_error={report.broker_check.last_error or 'NONE'}"
        )
    print(f"reconciliation = {report.reconciliation.status}")
    print(f"Stage 6-F blocker = {report.stage6_f_blocker}")
    for blocker in report.blockers:
        print(f"blocker = {blocker}")


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        spec = _parse_spec(args)
        config = load_config(args.config) if args.live_broker_check else None
        application = build_paper_canary_readiness_application(spec)
        broker = _broker_projection(config, live_check=args.live_broker_check)
        report = application.inspect(
            current_runtime_revision=resolve_git_head(),
            broker_check=broker,
        )
    except (OSError, ValueError, TypeError) as error:
        print(f"paper canary inspection failed: {error}", file=sys.stderr)
        return 2
    if args.json_output:
        print(
            json.dumps(
                _json_value(report), ensure_ascii=False, sort_keys=True, indent=2
            )
        )
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
