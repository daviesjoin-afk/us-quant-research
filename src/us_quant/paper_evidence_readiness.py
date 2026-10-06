"""CLI: read-only readiness diagnostics for real Paper evidence acquisition.

Run with ``python -m us_quant.paper_evidence_readiness``.  This command only
reads: it probes the loopback IBKR socket without handshaking, reads the
recorder's newest health line, and opens the minute-quote database read-only.
It starts no recorder, writes no database, switches no provider, edits no
configuration and submits no order.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from us_quant.config import AppConfig, load_config
from us_quant.paths import ApplicationPaths
from us_quant.trading.application.market_data import (
    SOURCE_IBKR,
    SUPPORTED_SOURCES,
)
from us_quant.trading.application.paper_evidence_readiness import (
    PaperEvidenceReadinessReport,
    PaperEvidenceReadinessSpec,
    PaperEvidenceReadinessStatus,
)
from us_quant.trading.application.strategy_defaults import DEFAULT_STRATEGY_SEEDS
from us_quant.trading.composition.paper_evidence_readiness import (
    EvidenceStoreUnavailable,
    build_evidence_readiness,
    build_ibkr_projection,
    build_paper_evidence_readiness_application,
    load_stream_projection,
    projection_from_payload,
)

DEFAULT_SYMBOLS = ("SPY", "QQQ", "AAPL", "NVDA")
#: The parameters the readiness diagnostic uses to judge session quality.  They
#: are the shipped intraday Paper strategy defaults, so the readiness verdict and
#: the recorder's evidence quality are measured with the same ruler.
READINESS_STRATEGY_ID = "intraday-targeted-t"


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    paths = ApplicationPaths.discover()
    parser = argparse.ArgumentParser(
        description="Read-only Paper evidence acquisition readiness diagnostic"
    )
    parser.add_argument(
        "--source",
        default=SOURCE_IBKR,
        choices=SUPPORTED_SOURCES,
        help="固定行情源；不会自动切换 provider",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="逗号分隔的固定 symbol 清单",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=paths.runtime_root / "minute_quotes.sqlite3",
        help="minute quote SQLite 数据库路径（只读打开）",
    )
    parser.add_argument(
        "--health-log",
        type=Path,
        default=None,
        help=(
            "recorder health JSONL 路径（只读）；recorder 默认把 health "
            "打印到 stdout，需由操作者重定向"
        ),
    )
    parser.add_argument(
        "--health-stdin",
        action="store_true",
        help="从 stdin 读取 recorder 的 health JSON 行",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=paths.config_path,
        help="应用配置路径",
    )
    parser.add_argument(
        "--skip-socket-probe",
        action="store_true",
        help="跳过本地 socket 探测（仍读取配置）",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def _normalized_symbols(value: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in value.split(","):
        if item.strip():
            seen.setdefault(item.strip().upper(), None)
    if not seen:
        raise ValueError("至少需要一个 symbol")
    return tuple(seen)


def _readiness_parameters() -> dict[str, object]:
    for seed in DEFAULT_STRATEGY_SEEDS:
        if seed.strategy_id == READINESS_STRATEGY_ID:
            return dict(seed.parameters)
    raise RuntimeError("readiness strategy defaults are missing")


def _resolve_provider(source_id: str, config: AppConfig) -> str:
    from us_quant.trading.application.market_data import SOURCE_LABELS

    return SOURCE_LABELS.get(source_id, source_id)


def build_report(
    args: argparse.Namespace,
    *,
    config: AppConfig,
    symbols: tuple[str, ...],
    parameters: dict[str, object],
) -> PaperEvidenceReadinessReport:
    provider = _resolve_provider(args.source, config)
    spec = PaperEvidenceReadinessSpec(provider=provider, symbols=symbols)
    ibkr = build_ibkr_projection(
        config.ibkr, checked=not args.skip_socket_probe
    )
    stream = load_stream_projection(
        None if args.health_stdin else args.health_log,
        default_source_id=args.source,
        default_provider=provider,
    )
    if args.health_stdin:
        stream = _read_health_from_stdin(
            default_source_id=args.source,
            default_provider=provider,
            stream=stream,
        )
    evidence = ()
    unavailable: str | None = None
    try:
        evidence = build_evidence_readiness(
            spec=spec,
            quote_path=args.db,
            parameters=parameters,
        )
    except EvidenceStoreUnavailable:
        unavailable = "EVIDENCE_STORE_UNAVAILABLE"
    application = build_paper_evidence_readiness_application(spec)
    return application.inspect(
        ibkr=ibkr,
        stream=stream,
        evidence=evidence,
        evidence_unavailable_reason=unavailable,
    )


def _read_health_from_stdin(
    *,
    default_source_id: str,
    default_provider: str,
    stream,
):
    """Fold stdin health lines over the file-based projection.

    ``--health-log`` and ``--health-stdin`` are two transports for the same
    fact, so the last line read from either wins; the file transport is applied
    first so a piped live stream is the fresher one.
    """

    payload: dict[str, object] | None = None
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            candidate = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
    if payload is None:
        return stream
    return projection_from_payload(
        payload,
        default_source_id=default_source_id,
        default_provider=default_provider,
    )


def render(report: PaperEvidenceReadinessReport) -> str:
    lines: list[str] = []
    ibkr = report.ibkr
    lines.append("IBKR:")
    lines.append(f"  socket: {'PASS' if ibkr.socket_reachable else 'FAIL'}")
    lines.append(f"  port: {ibkr.port}")
    lines.append(f"  readonly: {'PASS' if ibkr.api_read_only else 'FAIL'}")
    lines.append(
        "  order_submission: "
        f"{'ENABLED' if ibkr.paper_order_submission_enabled else 'DISABLED'}"
    )
    lines.append("")
    lines.append("Market stream:")
    lines.append(f"  realtime: {'PASS' if report.stream.realtime else 'FAIL'}")
    lines.append("")
    lines.append("Evidence:")
    for target in report.targets:
        lines.append(
            f"  {target.symbol:<5} {target.review_ready_sessions}"
            f"/{target.required_sessions}"
        )
    lines.append("")
    lines.append("Blockers:")
    for blocker in report.blockers:
        lines.append(f"  {blocker}")
    lines.append("")
    lines.append("Status:")
    lines.append(report.display_status)
    return "\n".join(lines)


def _json_payload(report: PaperEvidenceReadinessReport) -> str:
    payload = asdict(report)
    payload["status"] = str(report.status)
    payload["display_status"] = report.display_status
    payload["observed_at"] = report.observed_at.isoformat()
    for key in ("last_snapshot_at",):
        value = payload["stream"].get(key)
        if isinstance(value, datetime):
            payload["stream"][key] = value.isoformat()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        symbols = _normalized_symbols(args.symbols)
        config = load_config(args.config)
        parameters = _readiness_parameters()
        report = build_report(
            args,
            config=config,
            symbols=symbols,
            parameters=parameters,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"paper evidence readiness cannot run: {error}", file=sys.stderr)
        return 2
    if args.json_output:
        print(_json_payload(report))
    else:
        print(render(report))
    return 0 if report.status is PaperEvidenceReadinessStatus.READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
