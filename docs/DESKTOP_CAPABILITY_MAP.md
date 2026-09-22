# Desktop Capability Map

A navigation index, not a design document. The purpose is one thing: when a
reviewer or maintainer needs to change a desktop capability, they should know
which files to open **without reading `desktop.py`**.

Design rationale stays in `docs/TRADING_ARCHITECTURE_V2.md` and
`docs/DESKTOP_DECOMPOSITION.md`. Every orchestration PR updates this table.

## How to read a row

- **Canonical truth owner** — the object that holds the capability's desktop
  state. If two objects hold it, that is a bug, not a design choice.
- **Page render owner** — the only object allowed to call the page's `render`.
  A second caller is a second state that can paint the page.
- **Application / service dependency** — the Qt-free layer the capability calls.
  The capability may not reach past it into the domain.
- **Public orchestration API** — the surface a caller outside the capability may
  use. Anything not listed is private by construction.
- **Cross-workflow bridge** — what `MainWindow` still owns *because* it combines
  this capability with facts from another one.

## Capabilities

| Capability | Canonical truth owner | Page render owner | Application / service dependency | Public orchestration API | Cross-workflow bridge | Status |
| --- | --- | --- | --- | --- | --- | --- |
| **Market** | `MarketOrchestrator.snapshot` | `MarketOrchestrator` | `MarketDataApplication` | `snapshot`, `request_start`, `request_stop`, `request_switch`, `render_current`, `set_readiness_inputs`, signals | `MainWindow` routes snapshot changes to the scanner candidate selection, the intraday watchlist and the shell badges | v2O-A complete |
| **Account** | `BrokerAccountApplication.portfolio` (delegated, not copied) | `AccountOrchestrator` | `BrokerAccountApplication`, `AccountLedger` | `portfolio`, `fresh_paper_net_liquidation`, `request_refresh`, `render_current`, `set_presentation_inputs`, signals | `MainWindow` fans the published portfolio out to dashboard, AutoQuant preflight and targeted preflight | v2O-B complete |
| **Universe** | `UniverseOrchestrator.snapshot` | `UniverseOrchestrator` | `DesktopUniverseService` | `snapshot`, `request_refresh`, `request_cancel`, `cancel_for_shutdown`, `restore_snapshot`, `render_current`, signals | `MainWindow` refreshes the market-scope summary; History and Scanner read the snapshot through a provider callable | v2O-C1 complete |
| **History** | `DesktopHistoryService` queue (not copied) | `HistoryOrchestrator` | `DesktopHistoryService` | `request_run_ibkr`, `request_run_public`, `request_schedule`, `retry_failed`, `render_current`, signals | `MainWindow` refreshes the market-scope summary and the AutoQuant candidates from the queue counts | v2O-C1 complete |
| **Scanner** | `ScannerOrchestrator.scan` | `ScannerOrchestrator` | `DesktopMarketScanService` | `scan`, `request_scan`, `request_chart`, `restore_saved`, `adopt_external_scan`, `render_current`, signals | `MainWindow` refreshes the market-scope summary; AutoQuant **publishes** its finished scan in through `adopt_external_scan` | v2O-C2 complete |
| **Backtest** | `BacktestOrchestrator._runs` | `BacktestOrchestrator` | `DesktopBacktestService` | `refresh_strategy_options`, `request_selected`, `request_compare_all`, `select_run`, `render_current`, signals | None. `MainWindow` only constructs, connects and shows the refusal dialog; the strategy catalogue arrives as a provider callable | v2O-C3 complete |
| **Cross Section** | `MainWindow.cross_section_report` | `MainWindow._publish_cross_section_view` | `us_quant.cross_sectional` (called inline) | none yet | **Research capital** is a window-owned scalar shared by Cross Section, the Scanner run inputs and the Account presentation; its ownership is designed in v2O-C4 | not started |
| **Targeted** | `MainWindow` (`_selected_robustness_run_id`, `_selected_review_run_id`, `_target_status`, …) | `MainWindow._publish_targeted_view` | the `targeted_*` modules (called inline) | none yet | Shares the target symbol with the market stream; scheduled for v2O-C5 | not started |
| **Shadow** | `MainWindow.shadow_engine` | `MainWindow` | `ShadowPaperStore`, shadow workflow | none yet | Reads the account truth and the scan; scheduled for v2O-D | not started |
| **Paper** | `PaperTradingService` / workflow controllers | `MainWindow` (execution page) | `us_quant.trading.*` | none yet | The largest remaining bridge; scheduled for v2O-E | not started |
| **System** | `RuntimeEventStore`, `DesktopSettingsService` | the two System pages | `RuntimeEventStore`, `DesktopSettingsService` | none yet | Runtime events are *requested* by other capabilities rather than written by them | v2O-F |

## Shared plumbing

| Module | What it is | What it is not |
| --- | --- | --- |
| `desktop_v2/orchestration/tasking.py` | The `TaskSubmitter` protocol and two callable aliases. Every capability's constructor is annotated with it so the task boundary is legible at the call site. | Not a task runner, not a worker registry, not a resource-group owner. The generic task lifecycle — `TaskThread`, `DesktopTaskController`, the worker list, the closing gate, the busy dialog — stays on `MainWindow`. |
| `tests/desktop_architecture_support.py` | Mechanical AST queries shared by the architecture guards: parse, imports, find a class, read a method's source. | Not a place for rules. Each guard declares what may or may not exist; this module only reads code. |

## The shape every capability converges on

```
Page intent  ──signal──▶  Orchestrator  ──call──▶  Application / Service  ──▶  Domain
                               │
                               └──render──▶  Page

Cross-workflow facts  ──▶  MainWindow composition bridge  ──▶  whoever else cares
```

A capability owns its own state and its own page. `MainWindow` composes it,
routes its published facts, and owns the generic task lifecycle — nothing else.
