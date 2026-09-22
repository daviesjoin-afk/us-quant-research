"""The market scan data boundary: scan, restore and chart reads, without Qt.

``MainWindow._run_scan`` used to run the scanner inline inside its task
callback: read the quality-gated local daily bars, build the scan, then write
``market_scan.json``.  This module owns that orchestration.

The window keeps the Qt half -- the missing-universe dialog, the progress
copy, the ``TaskThread`` -- and the capability that owns the scan truth keeps
the rendering.

The boundary has since grown to cover everything the Scanner workspace needs
from the filesystem, which is why it is a *scanner* service rather than a
"manual scan" one:

* :meth:`scan` -- read the bars, scan, persist;
* :meth:`load_saved` -- re-read the artifact the previous run wrote.  The
  window used to parse ``market_scan.json`` itself, which meant it also owned
  the artifact schema; parsing belongs next to the writer, and this class is
  the writer;
* :meth:`load_chart` -- the one-symbol close series the chart draws.

All three are the same kind of thing -- Scanner data access -- so they share
one owner.  What is deliberately **not** here is the AutoQuant candidate
preparation path: it runs the same two scanner calls on purpose, because it is
wired into Paper ``PREPARING`` and failure cleanup, and that execution
ownership stays where it is.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path

from us_quant.portfolio import SubstitutionRule
from us_quant.scanner import (
    MarketScan,
    ScanResult,
    load_close_series,
    save_market_scan,
    scan_market,
)
from us_quant.universe import UniverseSnapshot


class DesktopMarketScanService:
    """Runs the market scan, persists it, and reads Scanner artifacts back.

    The constructor performs no I/O: it only remembers the three paths.
    Reading daily bars, scanning, writing the scan and restoring it all happen
    inside the methods below.
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

    def load_saved(self) -> MarketScan | None:
        """Re-read the scan artifact this service wrote, or ``None``.

        Three outcomes, and they are deliberately different:

        * the file is absent -> ``None``.  Nothing has been scanned yet, which
          is the ordinary first-run state, not an error;
        * the file parses -> the ``MarketScan``, with ``generated_at``,
          ``data_date``, every row's ``trading_date``,
          ``max_position_risk_pct`` and ``skipped`` all restored;
        * the file is malformed -> the exception propagates.  A corrupt
          artifact is a real problem and silently degrading it to "no scan"
          would hide it.

        Restoring is **not** a successful scan: this returns a fact and
        publishes nothing.  Whether the caller paints, logs or announces is
        the capability's decision, not this boundary's.
        """

        if not self.scan_path.exists():
            return None
        payload = json.loads(self.scan_path.read_text(encoding="utf-8"))
        results = []
        for row in payload["results"]:
            row["trading_date"] = date.fromisoformat(row["trading_date"])
            results.append(ScanResult(**row))
        return MarketScan(
            generated_at=datetime.fromisoformat(payload["generated_at"]),
            capital=float(payload["capital"]),
            data_date=(
                date.fromisoformat(payload["data_date"])
                if payload["data_date"]
                else None
            ),
            results=tuple(results),
            skipped=dict(payload["skipped"]),
            max_position_risk_pct=float(
                payload.get("max_position_risk_pct", 0.10)
            ),
        )

    def load_chart(self, symbol: str) -> tuple[tuple[date, float], ...]:
        """The close series the Scanner chart draws for one symbol.

        The two roots are this service's own, so the caller cannot point a
        chart at a different tree than the scan read from.  An unreadable
        symbol raises exactly what the loader raises: the capability decides
        that a chart failure is a log line and not a dialog.
        """

        return load_close_series(
            symbol,
            data_root=self.data_root,
            fallback_data_root=self.fallback_data_root,
        )
