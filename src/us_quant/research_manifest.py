from __future__ import annotations

"""Immutable provenance for exploratory research runs.

The manifest intentionally records what was actually loaded, rather than
claiming that the current research archive is point-in-time executable data.
It is a small, dependency-free boundary between mutable files on disk and a
saved research result.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from us_quant.market_data import LoadedDailySeries
from us_quant.universe import UniverseRecord, UniverseSnapshot


RESEARCH_MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class LoadedArtifactManifest:
    symbol: str
    source_sha256: str
    path: str
    source: str
    price_basis: str
    point_in_time_membership: bool
    rows: int
    first_date: str | None
    last_date: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source_sha256": self.source_sha256,
            "path": self.path,
            "source": self.source,
            "price_basis": self.price_basis,
            "point_in_time_membership": self.point_in_time_membership,
            "rows": self.rows,
            "first_date": self.first_date,
            "last_date": self.last_date,
        }


@dataclass(frozen=True, slots=True)
class ResearchRunManifest:
    """JSON-safe, stable description of a single research input set."""

    schema_version: int
    policy_mode: str
    universe_sha256: str
    universe_generated_at: str
    selected_symbols: tuple[str, ...]
    loaded_artifacts: tuple[LoadedArtifactManifest, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_mode": self.policy_mode,
            "universe": {
                "sha256": self.universe_sha256,
                "generated_at": self.universe_generated_at,
            },
            "selected_symbols": list(self.selected_symbols),
            "loaded_artifacts": [
                artifact.to_dict() for artifact in self.loaded_artifacts
            ],
        }


def build_research_run_manifest(
    universe: UniverseSnapshot,
    records: dict[str, UniverseRecord],
    loaded_series: dict[str, LoadedDailySeries],
    *,
    policy_mode: str = "exploratory",
) -> ResearchRunManifest:
    """Build provenance from the exact post-filter research input set."""
    if policy_mode != "exploratory":
        raise ValueError("only exploratory research manifests are supported")
    artifacts = tuple(
        _artifact_manifest(loaded_series[symbol])
        for symbol in sorted(loaded_series)
    )
    return ResearchRunManifest(
        schema_version=RESEARCH_MANIFEST_SCHEMA_VERSION,
        policy_mode=policy_mode,
        universe_sha256=canonical_universe_sha256(universe),
        universe_generated_at=universe.generated_at.isoformat(),
        selected_symbols=tuple(sorted(records)),
        loaded_artifacts=artifacts,
    )


def canonical_universe_sha256(universe: UniverseSnapshot) -> str:
    """Hash the complete source snapshot with a canonical record ordering."""
    payload = {
        "generated_at": universe.generated_at.isoformat(),
        "source_timestamps": dict(sorted(universe.source_timestamps.items())),
        "records": [
            asdict(record)
            for record in sorted(
                universe.records,
                key=lambda row: (row.symbol, row.exchange, row.security_type),
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _artifact_manifest(series: LoadedDailySeries) -> LoadedArtifactManifest:
    bars = series.bars
    return LoadedArtifactManifest(
        symbol=series.symbol,
        source_sha256=series.source_sha256,
        path=str(series.path),
        source=series.source,
        price_basis=series.price_basis,
        point_in_time_membership=series.point_in_time_membership,
        rows=len(bars),
        first_date=(bars[0].trading_date.isoformat() if bars else None),
        last_date=(bars[-1].trading_date.isoformat() if bars else None),
    )
