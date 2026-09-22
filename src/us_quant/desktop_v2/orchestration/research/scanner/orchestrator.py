"""Scanner orchestration: the desktop owner of the canonical ``MarketScan``.

``MarketScan`` used to live on ``MainWindow`` as ``self.scan``, and by the time
of this extraction it had become a shared desktop fact: the manual scan wrote
it, startup restoration rebuilt it by parsing ``market_scan.json`` by hand, the
AutoQuant preparation path overwrote it, and five unrelated consumers read it
(the Scanner page, the intraday watchlist, the market-scope summary, the
execution-context lines and the AutoQuant candidate selection).

This class is now its single owner, and it owns it *only*:

* **the scan.**  :attr:`scan` is the canonical desktop scan truth.  There is no
  ``self.scan`` on the window and no compatibility property, so answering "who
  owns the latest MarketScan?" takes one grep;
* **the three ways a scan can arrive.**  They are deliberately three entry
  points rather than one flag-driven setter, because they mean different
  things and will be changed separately:

  * :meth:`request_scan` -- the operator clicked 扫描.  Refuses without a
    universe, freezes the run inputs on the UI thread, reads the universe
    again at execution time, renders, publishes and writes a completion log;
  * :meth:`restore_saved` -- startup re-read of a local artifact.  Seeds the
    truth and paints, and publishes nothing: re-reading a file is not a new
    scan, so announcing one would be a lie;
  * :meth:`adopt_external_scan` -- another workflow already ran a scan
    (AutoQuant preparation) and hands over a finished fact.  It becomes the
    canonical scan, the page is repainted and the change is published, but the
    manual "扫描完成" line is not written: nobody clicked 扫描.

* **the chart read.**  :meth:`request_chart` is the symbol-selection intent.
  A failure stays a log line and never clears the chart that is already drawn.

What it deliberately does not own:

* **the task lifecycle.**  ``TaskThread``, the controller, the busy dialog and
  the cancellation handling stay on the window.  This class receives one narrow
  callable, ``submit_task``, and never learns which worker it started;
* **the dialog.**  A refusal is published through :attr:`refused` and the
  window shows it, which is what keeps this module free of widgets;
* **the AutoQuant/Paper chain.**  The candidate preparation path runs the
  scanner directly on purpose -- it is wired into Paper ``PREPARING`` and
  failure cleanup.  It only *publishes* its finished scan here; the direction is
  AutoQuant -> Scanner, never the reverse.

Two dependencies are callables rather than objects:

* ``universe_provider`` returns the snapshot to scan.  Scanner must not import
  ``UniverseOrchestrator``: the universe implementation will keep changing, and
  a Scanner that depended on it would have to change with it.  The provider is
  read **at execution time**, because a refresh that landed while the task
  queued must be the universe that gets scanned;
* ``run_inputs_provider`` returns the frozen capital / risk / substitutions.
  It is called **on the request thread**, because changing the research capital
  slider while a scan is queued must not change the scan about to run.  The
  scanner never reads ``AppConfig``, ``RiskApplication`` or a widget.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_market_scan_service import DesktopMarketScanService
from us_quant.desktop_v2.orchestration.research.scanner.models import (
    ScannerRunInputs,
)
from us_quant.desktop_v2.pages.research.scanner.models import ScannerChartView
from us_quant.desktop_v2.pages.research.scanner.presenter import (
    build_scanner_view,
)
from us_quant.scanner import MarketScan
from us_quant.universe import UniverseSnapshot

#: The resource group that serializes market scans.  A second concurrent scan
#: would rewrite ``market_scan.json`` while the first is still producing it.
SCAN_RESOURCE_GROUP = "scan"

#: The status line written when the manual scan task is admitted.
SCAN_START_MESSAGE = "市场扫描中…"

#: The progress line the manual scan task reports before it starts reading.
SCAN_PROGRESS_MESSAGE = "正在读取已通过质量门的本地日 K…"

#: The refusal shown when a scan is requested with no universe loaded.
MISSING_UNIVERSE_TITLE = "缺少标的池"
MISSING_UNIVERSE_MESSAGE = "请先刷新官方标的。"


class ScannerOrchestrator(QObject):
    """Owns the desktop scan truth, its three arrival paths and the chart."""

    #: The canonical scan changed in a way the rest of the desktop should
    #: notice.  A manual scan and a cross-workflow adoption both emit it;
    #: startup restoration does not, because nothing changed -- the process
    #: merely re-read a file.  The window routes this; what else cares (the
    #: market-scope summary today) is not a scanner decision.
    scan_changed = Signal(object)

    #: A request was refused before it reached the service.  The window owns
    #: the dialog, so this module stays widget-free.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        service: DesktopMarketScanService,
        page: object,
        submit_task: Callable[..., bool],
        universe_provider: Callable[[], UniverseSnapshot | None],
        run_inputs_provider: Callable[[], ScannerRunInputs],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._universe_provider = universe_provider
        self._run_inputs_provider = run_inputs_provider
        self._scan: MarketScan | None = None

    # -- read-only fact the rest of the desktop consumes -------------------

    @property
    def scan(self) -> MarketScan | None:
        """The canonical desktop scan truth, or ``None`` before any scan.

        This is a stored fact, not a delegation: the service is a stateless
        procedure, so the desktop layer is where the scan lives.  Consumers
        read this property rather than holding their own copy -- a mirrored
        copy would be a second truth, and the two would disagree the first
        time a scan updated only one of them.
        """

        return self._scan

    # -- lifecycle --------------------------------------------------------

    def restore_saved(self) -> None:
        """Adopt the scan the startup loader read from disk.

        Startup restoration is **not** a new scan.  It seeds the truth and
        paints it, and publishes nothing: no :attr:`scan_changed` and no log
        line, because neither the market-scope summary nor the operator should
        be told a scan just completed when the process only re-read a file.

        A malformed artifact leaves the truth empty and still paints once, so a
        corrupt scan file cannot stop the desktop from starting -- the same
        tolerance the window had when it parsed the JSON itself.
        """

        try:
            restored = self._service.load_saved()
        except Exception:
            restored = None
        self._scan = restored
        self.render_current()

    def request_scan(self) -> None:
        """Run the manual market scan on the shared background task boundary.

        The timing here is the contract, and the two halves are opposites:

        * the universe is checked now (so an operator with nothing loaded is
          told immediately rather than after a worker starts) and then read
          **again inside the task**, so a refresh that landed while this task
          queued is the universe that gets scanned.  If the universe is gone by
          then the task fails closed rather than scanning a stale copy;
        * the run inputs are frozen **now**, on this thread, so the scan runs
          with the capital the operator saw when they clicked.

        This class owns *what* a scan is and which state surrounds it, and
        nothing about how a background task is admitted.  That is what
        ``submit_task`` is for.
        """

        if self._universe_provider() is None:
            self.refused.emit(
                MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE
            )
            return

        inputs = self._run_inputs_provider()

        def task(progress: Callable[[str], None]) -> MarketScan:
            progress(SCAN_PROGRESS_MESSAGE)
            universe = self._universe_provider()
            if universe is None:
                # Fail closed.  Scanning the universe captured at request time
                # would silently scan a snapshot the operator can no longer
                # see, which is worse than a failed task.
                raise RuntimeError(MISSING_UNIVERSE_MESSAGE)
            return self._service.scan(
                universe,
                capital=inputs.capital,
                max_position_risk_pct=inputs.max_position_risk_pct,
                substitutions=inputs.substitutions_mapping(),
            )

        self._submit_task(
            task,
            on_success=self._scan_finished,
            start_message=SCAN_START_MESSAGE,
            resource_group=SCAN_RESOURCE_GROUP,
        )

    def request_chart(self, symbol: str) -> None:
        """Draw one symbol's close series on the page's chart.

        A failed read is a status line, not a dialog, and it leaves the
        currently drawn chart alone: clearing it would replace something the
        operator can still read with a blank panel, which loses information
        rather than reporting the failure.
        """

        try:
            points = self._service.load_chart(symbol)
        except Exception as error:
            self.log_requested.emit(f"{symbol} 图表读取失败：{error}")
            return
        self._page.render_chart(
            ScannerChartView(symbol=symbol, points=points)
        )

    def adopt_external_scan(self, scan: MarketScan) -> None:
        """Adopt a scan another workflow completed.

        The direction matters: AutoQuant's preparation path runs the scanner
        itself and hands the finished fact over through the window, so Scanner
        never learns that Paper exists.  This is a *new* scan from the
        desktop's point of view -- the truth moves and the change is published
        -- but it is not a manual one, so no "扫描完成" line is written.
        """

        self._scan = scan
        self.render_current()
        self.scan_changed.emit(scan)

    # -- rendering --------------------------------------------------------

    def render_current(self) -> None:
        """Draw the page from this capability's own state.

        It never fetches: the scan is whatever the last scan, adoption or
        startup restore published.  A render that could start a read would be a
        second scan path.

        The research count prefers the universe because the universe is the
        *intended* research scope; the scan is the fallback, since it only
        counted the symbols it could actually score.
        """

        universe = self._universe_provider()
        if universe is not None:
            research_count = int(
                universe.summary()["research_eligible"]
            )
        elif self._scan is not None:
            research_count = len(self._scan.results) + len(self._scan.skipped)
        else:
            research_count = 0
        self._page.render(
            build_scanner_view(
                self._scan,
                research_count=research_count,
            )
        )

    # -- success path -----------------------------------------------------

    def _scan_finished(self, result: object) -> None:
        """Publish one successful manual scan: scan, page, event, log."""

        if not isinstance(result, MarketScan):
            raise TypeError("unexpected market scan result")
        self._scan = result
        self.render_current()
        self.scan_changed.emit(result)
        summary = result.summary()
        self.log_requested.emit(
            f"扫描完成：{summary['scanned']} 个，"
            f"趋势候选 {summary['positive_signal']} 个。"
        )


__all__ = [
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "SCAN_PROGRESS_MESSAGE",
    "SCAN_RESOURCE_GROUP",
    "SCAN_START_MESSAGE",
    "ScannerOrchestrator",
]
