from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid5, NAMESPACE_URL


LEGACY_CROSS_SECTIONAL_REASONS = (
    "旧版替代执行品风险敞口未统一按元数据折算",
    "top3/top5 组合与当前 10% 总敞口风控不一致",
    "当前上市成分存在幸存者偏差",
)


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    artifact_type: str
    run_id: str
    status: str
    generated_at: str | None
    data_as_of: str | None
    source: str
    file_sha256: str
    config_version: str
    code_version: str
    path: str
    limitations: tuple[str, ...] = ()

    @property
    def deployable(self) -> bool:
        return self.status in {"research_validated", "paper_shadow"}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ArtifactCatalog:
    artifacts: tuple[ArtifactProvenance, ...]
    loaded_at: str

    @property
    def current_artifacts(self) -> tuple[ArtifactProvenance, ...]:
        return tuple(
            artifact
            for artifact in self.artifacts
            if artifact.status != "load_error"
        )


def load_artifact_catalog(results_root: Path) -> ArtifactCatalog:
    loaders = (
        ("market_scan", results_root / "market_scan.json"),
        (
            "executable_cross_sectional_research",
            results_root / "cross_sectional_executable_research.json",
        ),
        (
            "cross_sectional_research",
            results_root / "cross_sectional_research.json",
        ),
    )
    artifacts = tuple(
        _load_artifact(artifact_type, path)
        for artifact_type, path in loaders
    )
    return ArtifactCatalog(
        artifacts=artifacts,
        loaded_at=datetime.now(timezone.utc).isoformat(),
    )


def _load_artifact(
    artifact_type: str,
    path: Path,
) -> ArtifactProvenance:
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fingerprint = sha256(str(path).encode("utf-8")).hexdigest()
        return ArtifactProvenance(
            artifact_type=artifact_type,
            run_id=f"load-error-{fingerprint[:12]}",
            status="load_error",
            generated_at=None,
            data_as_of=None,
            source="unavailable",
            file_sha256=fingerprint,
            config_version="unknown",
            code_version="unknown",
            path=str(path),
            limitations=(f"{type(error).__name__}: {error}",),
        )

    fingerprint = sha256(raw_bytes).hexdigest()
    generated_at = _string_or_none(payload.get("generated_at"))
    data_as_of = _artifact_data_date(artifact_type, payload)
    source = _artifact_source(artifact_type, payload)
    status = "research_exploratory"
    limitations = tuple(
        str(item) for item in payload.get("limitations", ())
    )
    if artifact_type == "cross_sectional_research":
        status = "legacy_invalidated"
        limitations = LEGACY_CROSS_SECTIONAL_REASONS + limitations
    elif artifact_type == "executable_cross_sectional_research":
        status = str(payload.get("status", "research_exploratory"))
    run_id = str(
        uuid5(
            NAMESPACE_URL,
            f"us-quant:{artifact_type}:{fingerprint}",
        )
    )
    return ArtifactProvenance(
        artifact_type=artifact_type,
        run_id=run_id,
        status=status,
        generated_at=generated_at,
        data_as_of=data_as_of,
        source=source,
        file_sha256=fingerprint,
        config_version="unverified:paper.toml:v1",
        code_version="unverified:local-0.17.0",
        path=str(path),
        limitations=limitations,
    )


def _artifact_data_date(
    artifact_type: str,
    payload: dict[str, Any],
) -> str | None:
    if artifact_type == "market_scan":
        return _string_or_none(payload.get("data_date"))
    scope = payload.get("scope")
    if isinstance(scope, dict):
        return _string_or_none(scope.get("last_completed_date"))
    return None


def _artifact_source(
    artifact_type: str,
    payload: dict[str, Any],
) -> str:
    source = payload.get("source")
    if isinstance(source, str) and source:
        return source
    if artifact_type == "market_scan":
        return "本地事后调整日 K 研究代理；不可证明历史整股成交"
    if artifact_type == "executable_cross_sectional_research":
        return (
            "事后复权日 K walk-forward 研究代理；"
            "整股、费用与风险结果不可晋级"
        )
    return "本地日 K 横截面研究；非实时账户数据"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
