"""The official universe refresh, without Qt.

``MainWindow`` used to run the whole refresh inline inside a task callback:
it prepared the writable reference directory, downloaded the official
Nasdaq Trader and SEC lists, then enriched 500 SEC profiles.  This module
owns that orchestration so the window keeps only the Qt half -- the cancel
event, the ``TaskThread``, the Chinese progress copy and the buttons.

Nothing here is presentation.  The service emits domain events
(:class:`UniverseRefreshProgress`); the window turns them into Chinese text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from us_quant.paths import ApplicationPaths
from us_quant.universe import (
    UniverseSnapshot,
    enrich_us_profiles,
    refresh_official_universe,
)

STAGE_PREPARE_REFERENCE = "prepare_reference"
STAGE_DOWNLOAD_OFFICIAL = "download_official"
STAGE_ENRICH_SEC_START = "enrich_sec_start"
STAGE_ENRICH_SEC = "enrich_sec"

# The number of SEC profiles verified per refresh.  Deliberately a literal:
# it is a product decision, not a tuning knob, and the UI copy quotes it.
SEC_PROFILE_BUDGET = 500


@dataclass(frozen=True, slots=True)
class UniverseRefreshProgress:
    """One domain-level step of a refresh.

    Carries no exception, no widget, no worker and no event: the window
    owns all of those.  ``detail`` is whatever the domain layer reported --
    a ticker on success, and on failure the domain layer's own message --
    and is never parsed or truncated here.
    """

    stage: str
    done: int = 0
    total: int = 0
    detail: str = ""


UniverseProgressCallback = Callable[[UniverseRefreshProgress], None]


class DesktopUniverseService:
    """Orchestrates the official universe refresh.

    The constructor performs no I/O: it only remembers ``paths``.  Every
    side effect -- creating the writable reference catalog, the network
    download, the SEC enrichment -- happens inside :meth:`refresh`.
    """

    def __init__(self, *, paths: ApplicationPaths) -> None:
        self.paths = paths

    def refresh(
        self,
        *,
        should_stop: Callable[[], bool] | None,
        progress: UniverseProgressCallback | None = None,
    ) -> UniverseSnapshot:
        """Run the refresh and return the enriched snapshot.

        ``should_stop`` is forwarded unchanged to both domain calls, so a
        single cancellation event covers the whole refresh.
        ``UniverseRefreshCancelled`` and any other exception propagate
        as-is: the domain module owns fallback semantics and the window
        owns reporting.
        """

        def report(event: UniverseRefreshProgress) -> None:
            if progress is not None:
                progress(event)

        report(UniverseRefreshProgress(stage=STAGE_PREPARE_REFERENCE))
        reference_root = self.paths.ensure_user_reference_catalog()
        report(UniverseRefreshProgress(stage=STAGE_DOWNLOAD_OFFICIAL))
        snapshot = refresh_official_universe(
            cache_root=reference_root,
            leader_seed_path=(
                self.paths.resource_root / "configs" / "sector_leaders.csv"
            ),
            china_denylist_path=(
                self.paths.resource_root
                / "configs"
                / "china_concept_denylist.csv"
            ),
            should_stop=should_stop,
            save_snapshot=False,
        )
        report(UniverseRefreshProgress(stage=STAGE_ENRICH_SEC_START))
        return enrich_us_profiles(
            snapshot,
            cache_root=reference_root / "sec_profiles",
            max_new_profiles=SEC_PROFILE_BUDGET,
            progress=lambda done, total, symbol: report(
                UniverseRefreshProgress(
                    stage=STAGE_ENRICH_SEC,
                    done=done,
                    total=total,
                    detail=symbol,
                )
            ),
            should_stop=should_stop,
        )
