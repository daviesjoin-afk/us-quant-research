# Desktop Capability Map

A navigation index, not a design document: when a reviewer or maintainer needs to
change a desktop capability, they should know which files to open **without
reading `desktop.py`**. Design rationale stays in `docs/TRADING_ARCHITECTURE_V2.md`
and `docs/DESKTOP_DECOMPOSITION.md`; if a note here needs five paragraphs it
belongs in one of those. Every orchestration PR updates this table.

## How to read a row

- **Canonical truth owner** — the object that holds the capability's desktop
  state. If two objects hold it, that is a bug, not a design choice.
- **Page render owner** — the only object allowed to paint the page. A second
  painter is a second state that can draw over the first.
- **Application / service dependency** — the Qt-free layer the capability calls.
- **Public orchestration API** — the surface a caller outside the capability may
  use. Anything not listed is private by construction.
- **Cross-workflow bridge** — what `MainWindow` still owns *because* it combines
  this capability with facts from another one. See the section below the table.

## Capabilities

| Capability | Canonical truth owner | Page render owner | Application / service dependency | Public orchestration API | Cross-workflow bridge | Status |
| --- | --- | --- | --- | --- | --- | --- |
| **Market** | `MarketOrchestrator.snapshot` | `MarketRenderer` (built by the orchestrator) | `MarketDataApplication`, `DesktopCredentialService` | `snapshot`, `is_live`, `start`, `stop`, `request_switch`, `set_readiness_inputs`, `set_scope`, `set_selected_provider`, `set_subscription_symbols`, `worker_running`, signals | Three distinct paths — see *Market* below | v2O-A complete |
| **Account** | `BrokerAccountApplication.portfolio` (delegated, not copied) | `AccountOrchestrator` | `BrokerAccountApplication`, `AccountLedger` | `portfolio`, `fresh_paper_net_liquidation`, `request_refresh`, `render_current`, `set_presentation_inputs`, signals | `portfolio_changed` → `_on_account_portfolio_changed` (dashboard, AutoQuant snapshot, targeted preflight, AutoQuant preflight). `shell_health_changed` → `_render_account_shell_health` paints the header badges separately | v2O-B complete |
| **Universe** | `UniverseOrchestrator.snapshot` | `UniverseOrchestrator` | `DesktopUniverseService` | `snapshot`, `request_refresh`, `request_cancel`, `cancel_for_shutdown`, `restore_snapshot`, `render_current`, signals | `snapshot_changed` → `_on_universe_changed` (repoints `universe_path`, then `_refresh_market_scope_summary`). History and Scanner read the snapshot through a provider callable, not an object handle | v2O-C1 complete |
| **History** | `DesktopHistoryService` queue (not copied) | `HistoryOrchestrator` | `DesktopHistoryService` | `request_run_ibkr`, `request_run_public`, `request_schedule`, `retry_failed`, `render_current`, signals | `history_changed` → `_refresh_market_scope_summary`. Nothing else; see *History* below | v2O-C1 complete |
| **Scanner** | `ScannerOrchestrator.scan` | `ScannerOrchestrator` | `DesktopMarketScanService` | `scan`, `request_scan`, `request_chart`, `restore_saved`, `adopt_external_scan`, `render_current`, signals | `scan_changed` → `_refresh_market_scope_summary`. AutoQuant **publishes** its finished scan in through `adopt_external_scan` (direction: AutoQuant → Scanner) | v2O-C2 complete |
| **Backtest** | `BacktestOrchestrator._runs` (private) | `BacktestOrchestrator` | `DesktopBacktestService` | `refresh_strategy_options`, `request_selected`, `request_compare_all`, `select_run`, `render_current`, signals | None. `MainWindow` only constructs, connects and shows the refusal dialog; the strategy catalogue arrives as a provider callable | v2O-C3 complete |
| **Cross Section** | `CrossSectionOrchestrator._report` (private) | `CrossSectionOrchestrator` | `DesktopCrossSectionService` | `request_capital_change`, `request_run`, `restore_saved`, `render_current`, signals | `capital_changed` → `_on_research_scenario_capital_changed` (Account presentation only). `report_changed` → `_on_cross_section_report_changed` (reloads the artifact catalogue, repaints the Dashboard). Startup `restore_saved()` publishes nothing | v2O-C4 complete |
| **Targeted Evidence** | `TargetedEvidenceOrchestrator.snapshot` (published: the terminal export reads all seven families) | `TargetedEvidenceOrchestrator` (evidence half only) | `DesktopTargetedEvidenceService` | `snapshot`, `request_replay`, `request_robustness`, `select_robustness_run`, `select_review_run`, `restore_saved`, `render_current`, signals | `refused` → `_report_targeted_evidence_refusal` (dialog). `runtime_event_requested` → `_record_targeted_evidence_runtime_event` (store). `minute_status_refresh_requested` → `TargetedSessionOrchestrator.refresh_minute_status`. `focus_requested` → `_focus_targeted_evidence` (research route + targeted workspace) | v2O-C5A complete |
| **Targeted Session / Preflight** | `TargetedSessionOrchestrator.snapshot` (target draft / target status / minute status / preflight) | `TargetedSessionOrchestrator` | `DesktopTargetedSessionService` | `snapshot`, `refresh_strategy_options`, `adopt_target_draft`, `request_strategy_selection`, `request_target_apply`, `request_target_subscribe`, `refresh_minute_status`, `refresh_preflight`, `render_current`, signals | Market snapshot → `refresh_preflight`. Account portfolio → `refresh_preflight`. Evidence replay completion → `refresh_minute_status`. Shadow snapshot change → `render_current` (repaint only). Subscription and start go out through two narrow injected Market commands | v2O-C5B complete |
| **Shadow** | `ShadowOrchestrator.snapshot` (the engine stays the session's own state owner) | `ShadowOrchestrator` (session half only, via the injected repaint) | `us_quant.shadow.*` (`ShadowPaperEngine`, `ShadowPaperStore`, `build_targeted_shadow_config`), the shared execution lease | `snapshot`, `is_active`, `recent_fills`, `start` (no-op when already active; never releases a lease it does not hold), `stop`, `shutdown`, `on_market_snapshot`, signals | Market snapshot → `on_market_snapshot` (a no-op when nothing runs; repaints the session, never the evidence tables). `refused` → `_report_shadow_refusal` (dialog). `runtime_event_requested` → `_record_shadow_runtime_event` (store). `log_requested` → `_log`. Market stop takes it down through the window's interlock, which may name Shadow because Market may not | v2O-D complete |
| **Paper** | `PaperWorkflowController` (phase / active plan / execution lease / latest result / reconciliation evidence / finalization evidence); `PaperTradingService` (candidate and active broker connections) | `PaperOrchestrator` (the whole run: launch, active session, recovery, finalization and the shutdown verdict); `MainWindow` renders the published result and shows the two confirmation dialogs | `PaperTradingService`, the injected session-build seam (`_build_paper_session`), a market-snapshot provider, a reconciliation-rows provider, the workflow controller | `start`, `on_market_snapshot`, `poll`, `pause`, `resume`, `stop`, `reconcile`, `confirm_reconciliation_resume`, `prepare_shutdown`, `result`, `runtime_active`, `has_runtime_obligations`, signals (`refused`, `log_requested`, `result_changed`, `runtime_event_requested`, `presentation_refresh_requested`, `session_finalized`, `manual_recovery_required`) | `start_requested` → `_confirm_and_start_auto_quant` and `resume_reconciliation_requested` → `_confirm_paper_reconciliation_resume`: the two `QMessageBox` confirmations, which may not move into the capability. `pause_requested` / `resume_requested` / `stop_requested` / `reconcile_requested` / `paper_order_timer.timeout` reach the capability **directly**. Market `snapshot_changed` → `_on_market_snapshot_changed` hands the fact over → `on_market_snapshot` (the fan-out decides nothing about Paper). `result_changed` → `_on_paper_result_changed`: render only; the result's *consequences* are `_after_result`'s. `presentation_refresh_requested` → `_apply_paper_workflow_button_state`; `manual_recovery_required` → `_on_paper_manual_recovery_required` (undo a refused close); `session_finalized` → `_on_paper_session_finalized` (health line + clear the arm flag). The market stop/switch interlock reads `has_runtime_obligations` and Shadow's capital gate reads `runtime_active`, both from the canonical result rather than a window cache. `closeEvent` asks `prepare_shutdown()` and only presents its verdict. Still the window's: the launch/resume confirmations, `closeEvent`'s generic teardown, and execution render ownership | Paper — v2O-E1 launch orchestration complete / v2O-E2 active runtime orchestration complete / v2O-E3 recovery/finalization orchestration complete / v2O-E partial |
| **System** | `RuntimeEventStore`, `DesktopSettingsService` | the two System pages | `RuntimeEventStore`, `DesktopSettingsService` | none yet | Runtime events are *requested* by other capabilities rather than written by them; orchestration scheduled for v2O-F | pages done, orchestration not started |

## Bridges that need spelling out

These are the entries above where the direction and the trigger are what a reader
gets wrong.

**Market.** Three separate paths, and they must not be collapsed:

```text
snapshot_changed            → MainWindow._on_market_snapshot_changed
    → workflow readiness, minute snapshot evidence, Dashboard,
      AutoQuant candidate presentation, targeted preflight refresh,
      the targeted session repaint (when Shadow is active),
      and the stream ingress handed to an active Paper / Shadow workflow
      (each capability decides for itself whether the fact belongs to it)

shell_health_changed        → MainWindow._render_market_shell_health
    → the shell badges only; it does not go through snapshot_changed

load_scan_watchlist_requested (a Market-page user intent)
                            → MainWindow._apply_intraday_watchlist
    → reads Scanner's scan + Account's paper capital + research capital,
      then sets Market's subscription symbols
```

The third one is a **Market + Scanner cross-workflow command**, not a reaction to
a snapshot. It is triggered by the operator clicking on the market page.

**History.** The only bridge is `history_changed → _refresh_market_scope_summary`.
The AutoQuant preparation path does schedule history gaps and render the History
page, but that direction is **AutoQuant → History** — AutoQuant calls the
capability. It is not `history_changed → AutoQuant`.

**Cross Section.** Two bridges, neither a second truth:

```text
capital_changed  → MainWindow._on_research_scenario_capital_changed
    → _publish_account_presentation_inputs + AccountOrchestrator.render_current
    (Account presentation only; Scanner / AutoQuant / Targeted / the watchlist
     pull the canonical scalar when they next build a request)

report_changed   → MainWindow._on_cross_section_report_changed
    → reload the artifact catalogue + repaint the Dashboard
```

Neither the Dashboard nor the artifact catalogue is known to the capability.
`restore_saved()` deliberately emits neither signal: the catalogue is read during
the same startup pass, so announcing a re-read would fan out twice.

**Targeted Evidence.** Four bridges, each for a fact that is *not* an evidence
decision:

```text
refused                          → _report_targeted_evidence_refusal (QMessageBox)
runtime_event_requested          → _record_targeted_evidence_runtime_event (store)
minute_status_refresh_requested  → TargetedSessionOrchestrator.refresh_minute_status
focus_requested                  → _focus_targeted_evidence
                                     (shell.navigate_to("research") +
                                      research_page.set_active_workspace(TARGETED))
```

The first three exist so the capability holds no dialog, no event store and no
minute-status ownership. The fourth is the split that matters: a completed suite
navigates its **own page's** evidence workspace to REVIEW (the capability's
presentation behaviour), and separately *asks* for the desktop route through
`focus_requested` (shell composition, which stays on the window). See
`tests/test_desktop_targeted_evidence_orchestration.py`.

**Targeted Session.** Two bridges out, four facts in:

```text
refused        → _report_targeted_session_refusal (QMessageBox; level honoured --
                 warning for a mistyped symbol, information for a running Shadow
                 session or a live feed)
log_requested  → _log

Market snapshot    → refresh_preflight       (a new quote changes the verdict)
Account portfolio  → refresh_preflight       (broker truth is half the gates)
Evidence replay    → refresh_minute_status   (fresh local rows landed)
Shadow snapshot    → render_current          (positions and fills moved)
```

The Shadow line is a **request**, not a copy: the capability owns the snapshot and
calls the session's `render_current`; the session never receives it, so there is no
second mutable Shadow truth, and no evidence repaint. Rationale: §25.

## Shared facts

Not capabilities: one fact with several consumers, and therefore one owner.
These are the objects a maintainer greps for when a workflow behaves oddly.

| Fact | Owner | Editor | Consumers | It is **not** |
| --- | --- | --- | --- | --- |
| **Research Scenario Capital** (研究情景资金) | `ResearchScenarioCapitalState` (`orchestration/research/scenario_capital.py`) | the Cross Section page control, via `CrossSectionOrchestrator.request_capital_change` | Cross Section research, Scanner manual scan, AutoQuant candidate preparation, Market watchlist fallback, Targeted replay, Targeted robustness, Account presentation | Not `NetLiquidation`, not buying power, not risk capital, **not** a `CapitalAllocator`. A future allocator is computed from broker truth + portfolio risk; research scenario dollars must never be promoted into it |

## Shared plumbing

| Module | What it is | What it is not |
| --- | --- | --- |
| `desktop_v2/orchestration/tasking.py` | The `TaskSubmitter` protocol and two callable aliases. Every capability's constructor is annotated with it so the task boundary is legible at the call site. | Not a task runner, not a worker registry, not a resource-group owner. The generic task lifecycle — `TaskThread`, `DesktopTaskController`, the worker list, the closing gate, the busy dialog — stays on `MainWindow`. |

## The shape every capability converges on

```
Page intent  ──signal──▶  Orchestrator  ──call──▶  Application / Service  ──▶  Domain
                               │
                               └──render──▶  Page

Cross-workflow facts  ──▶  MainWindow composition bridge  ──▶  whoever else cares
```

A capability owns its own state and its own page. `MainWindow` composes it,
routes its published facts, and owns the generic task lifecycle — nothing else.
