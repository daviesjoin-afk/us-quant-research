"""The manual market scan, without Qt.

``MainWindow._run_scan`` used to run the scanner inline inside its task
callback: read the quality-gated local daily bars, build the scan, then
write ``market_scan.json``.  This module owns that orchestration.

The window keeps the Qt half -- the missing-universe dialog, the progress
copy, the ``TaskThread`` and the rendering in ``_scan_finished``.

This is deliberately the *manual* path only.  The AutoQuant candidate
preparation path runs the same two calls on purpose; it is wired into Paper
``PREPARING`` and failure cleanup, and is out of scope here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from us_quant.portfolio import SubstitutionRule
from us_quant.scanner import MarketScan, save_market_scan, scan_market
from us_quant.universe import UniverseSnapshot


class DesktopMarketScanService:
    """Runs the manual market scan and persists its result.

    The constructor performs no I/O: it only remembers the three paths.
    Reading daily bars, scanning and writing the scan all happen inside
    :meth:`scan`.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        fallback_data_root: Path | None,
        scan_path: Path,
    ) -> None:
        self.data_root = data_root
        self.fallback_data_root = fallback_data_root
        self.scan_path = scan_path

    def scan(
        self,
        universe: UniverseSnapshot,
        *,
        capital: Decimal,
        max_position_risk_pct: Decimal,
        substitutions: dict[str, SubstitutionRule],
    ) -> MarketScan:
        """Scan ``universe`` and save it, returning the domain result.

        The order is ``scan_market`` -> ``save_market_scan`` -> return.  A
        failing scan must not write anything, and a failing save must not
        report success: both exceptions propagate unchanged, which keeps the
        old ``TaskThread.failed`` behaviour.

        ``allow_quality_second_tier`` is deliberately not passed: the
        scanner's own default stays in force.
        """

        result = scan_market(
            universe,
            data_root=self.data_root,
            fallback_data_root=self.fallback_data_root,
            capital=capital,
            max_position_risk_pct=max_position_risk_pct,
            substitutions=substitutions,
        )
        save_market_scan(result, self.scan_path)
        return result
