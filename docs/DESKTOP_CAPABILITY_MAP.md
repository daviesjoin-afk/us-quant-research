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
| **Account** | `BrokerAccountApplication.portfolio` (delegated, not copied) | `AccountOrchestrator` | `BrokerAccountApplication`, `AccountLedger` | `portfolio`, `fresh_paper_net_liquidation`, `request_refresh`, `render_current`, `set_presentation_inputs`, `set_notice` (finished notice text only — the G2-A seam; it accepts no capability domain type), signals | `portfolio_changed` → `_on_account_portfolio_changed` (dashboard, AutoQuant snapshot, targeted preflight, AutoQuant preflight). `shell_health_changed` → `_render_account_shell_health` paints the header badges separately | v2O-B complete |
| **Universe** | `UniverseOrchestrator.snapshot` | `UniverseOrchestrator` | `DesktopUniverseService` | `snapshot`, `request_refresh`, `request_cancel`, `cancel_for_shutdown`, `restore_snapshot`, `render_current`, signals | `snapshot_changed` → `_on_universe_changed` (repoints `universe_path`, then `_refresh_market_scope_summary`). History and Scanner read the snapshot through a provider callable, not an object handle | v2O-C1 complete |
| **History** | `DesktopHistoryService` queue (not copied) | `HistoryOrchestrator` | `DesktopHistoryService` | `request_run_ibkr`, `request_run_public`, `request_schedule`, `retry_failed`, `render_current`, signals | `history_changed` → `_refresh_market_scope_summary`. Nothing else; see *History* below | v2O-C1 complete |
| **Scanner** | `ScannerOrchestrator.scan` | `ScannerOrchestrator` | `DesktopMarketScanService` | `scan`, `request_scan`, `request_chart`, `restore_saved`, `adopt_external_scan`, `render_current`, signals | `scan_changed` → `_refresh_market_scope_summary`. AutoQuant **publishes** its finished scan in through `adopt_external_scan` (direction: AutoQuant → Scanner) | v2O-C2 complete |
| **Backtest** | `BacktestOrchestrator._runs` (private) | `BacktestOrchestrator` | `DesktopBacktestService` | `refresh_strategy_options`, `request_selected`, `request_compare_all`, `select_run`, `render_current`, signals | None. `MainWindow` only constructs, connects and shows the refusal dialog; the strategy catalogue arrives as a provider callable | v2O-C3 complete |
| **Cross Section** | `CrossSectionOrchestrator._report` (private) | `CrossSectionOrchestrator` | `DesktopCrossSectionService` | `request_capital_change`, `request_run`, `restore_saved`, `render_current`, signals | `capital_changed` → `_on_research_scenario_capital_changed` (Account presentation only). `report_changed` → `_on_cross_section_report_changed` (reloads the artifact catalogue, repaints the Dashboard). Startup `restore_saved()` publishes nothing | v2O-C4 complete |
| **Targeted Evidence** | `TargetedEvidenceOrchestrator.snapshot` (published: the terminal export reads all seven families) | `TargetedEvidenceOrchestrator` (evidence half only) | `DesktopTargetedEvidenceService` | `snapshot`, `request_replay`, `request_robustness`, `select_robustness_run`, `select_review_run`, `restore_saved`, `render_current`, signals | `refused` → `_report_targeted_evidence_refusal` (dialog). `runtime_event_requested` → `_route_runtime_event` (the window's one router, into the Runtime Events store). `minute_status_refresh_requested` → `TargetedSessionOrchestrator.refresh_minute_status`. `focus_requested` → `_focus_targeted_evidence` (research route + targeted workspace) | v2O-C5A complete |
| **Targeted Session / Preflight** | `TargetedSessionOrchestrator.snapshot` (target draft / target status / minute status / preflight) | `TargetedSessionOrchestrator` | `DesktopTargetedSessionService` | `snapshot`, `refresh_strategy_options`, `adopt_target_draft`, `request_strategy_selection`, `request_target_apply`, `request_target_subscribe`, `refresh_minute_status`, `refresh_preflight`, `render_current`, signals | Market snapshot → `refresh_preflight`. Account portfolio → `refresh_preflight`. Evidence replay completion → `refresh_minute_status`. Shadow snapshot change → `render_current` (repaint only). Subscription and start go out through two narrow injected Market commands | v2O-C5B complete |
| **Shadow** | `ShadowOrchestrator.snapshot` (the engine stays the session's own state owner) | `ShadowOrchestrator` (session half only, via the injected repaint) | `us_quant.shadow.*` (`ShadowPaperEngine`, `ShadowPaperStore`, `build_targeted_shadow_config`), the shared execution lease | `snapshot`, `is_active`, `recent_fills`, `start` (no-op when already active; never releases a lease it does not hold), `stop`, `shutdown`, `on_market_snapshot`, signals | Market snapshot → `on_market_snapshot` (a no-op when nothing runs; repaints the session, never the evidence tables). `refused` → `_report_shadow_refusal` (dialog). `runtime_event_requested` → `_route_runtime_event` (the window's one router, into the Runtime Events store). `log_requested` → `_log`. Market stop takes it down through the window's interlock, which may name Shadow because Market may not | v2O-D complete |
| **Paper** | `PaperWorkflowController` (phase / active plan / execution lease / latest result / reconciliation evidence / finalization evidence); `PaperTradingService` (candidate and active broker connections, plus the two-phase promotion *and* release of the active slot); `PaperOrchestrator.presentation` (the **retained immutable presentation snapshot** — the last published result projected for display, kept because `finalize_if_safe` clears the canonical result when it releases PAPER) | `PaperOrchestrator` (the whole run: launch, active session, recovery, finalization, the shutdown verdict, and the presentation projection); `MainWindow` renders from `paper_orchestrator.presentation` and shows the two confirmation dialogs | `PaperTradingService`, the injected session-build seam (`_build_paper_session`), a market-snapshot provider, a reconciliation-rows provider, the workflow controller | `start`, `on_market_snapshot`, `poll`, `pause`, `resume`, `stop`, `reconcile`, `confirm_reconciliation_resume`, `prepare_shutdown`, `result`, `runtime_active`, `has_runtime_obligations`, `presentation`, `session_control_facts`, signals (`refused`, `log_requested`, `result_changed`, `runtime_event_requested`, `presentation_refresh_requested`, `session_finalized`, `manual_recovery_required`) | `start_requested` → `_confirm_and_start_auto_quant` and `resume_reconciliation_requested` → `_confirm_paper_reconciliation_resume`: the two `QMessageBox` confirmations, which may not move into the capability. `pause_requested` / `resume_requested` / `stop_requested` / `reconcile_requested` / `paper_order_timer.timeout` reach the capability **directly**. Market `snapshot_changed` → `_on_market_snapshot_changed` hands the fact over → `on_market_snapshot` (the fan-out decides nothing about Paper). `result_changed` → `_on_paper_result_changed`: render only, **and it stores nothing** — the session fact it draws is `paper_orchestrator.presentation`, and the read model around it is `pages/execution/projector.build_session_view`. `_publish_execution_controls` asks `session_control_facts` rather than comparing phase values. `presentation_refresh_requested` → `_apply_paper_workflow_button_state`; `manual_recovery_required` → `_on_paper_manual_recovery_required` (undo a refused close); `session_finalized` → `_on_paper_session_finalized` (health line + clear the arm flag). The market stop/switch interlock reads `has_runtime_obligations` and Shadow's capital gate reads `runtime_active`, both from the canonical result rather than any window cache. `closeEvent` asks `prepare_shutdown()` and only presents its verdict. Still the window's: the launch/resume confirmations, the candidate-preparation sequencing, `closeEvent`'s generic teardown, and the route's fetching | v2O-E complete |
| **System** | `RuntimeEventStore` (persisted events); `DesktopSettingsService` (settings transaction) | `RuntimeEventsOrchestrator` (Runtime Events); `SettingsOrchestrator` (Settings) | `RuntimeEventStore`, `export_terminal_bundle` via the injected bundle provider; `DesktopSettingsService`, `DesktopCredentialService` | Runtime Events: `record`, `refresh`, `resolve`, `export`, `notify_task_count_changed`, `last_export`, signals. Settings: `render_current`, `select_api_provider`, `select_market_provider`, `adopt_market_provider`, `set_connection_settings_enabled`, `preview_theme`, `save_credentials`, `clear_credentials`, `save_preferences`, `request_provider_switch`, `request/confirm_paper_order_capability`, `request/confirm_extended_hours`, `selected_api_provider`, `connection_settings_enabled`, signals | Runtime Events: every capability's `runtime_event_requested` → `_route_runtime_event` → the one store write; the export's cross-capability facts stay in `_export_runtime_bundle`. Settings: the page's nine intents reach the capability directly; the window keeps the theme fan-out, the market selection/switch bridges, the commit adoption and the dialogs | Runtime Events **v2O-F1 complete**; Settings **v2O-F2 complete** |
| **Dashboard** | none — a projection, not a truth | `DashboardOrchestrator` (G1): the one `DashboardPage.render` caller, plus the retained chart fact | reads four published facts through callables: account portfolio, market snapshot, artifact catalogue, market stop reason | `render_current`, `set_chart`, `build_view`, `chart` | account/portfolio, market snapshot and artifact-catalogue changes each reach `render_current` through a window bridge; a research chart is adopted with `set_chart` (exactly one render). The IBKR gateway probe is **not** here: it is a workbench shell diagnostic | **G1 complete** |
| **Strategy Governance** | `StrategyApplication` (the catalogue / lifecycle authority — not copied, not cached) | `StrategyGovernanceOrchestrator` (G2-A): the one `StrategyPage.render` caller | `StrategyApplication` only — the strategy package imports no Account / Market / Paper / Execution capability | `refresh`, `select_version`, `clone`, `transition`, signals (`catalog_changed`, `account_notice_requested`, `warning_requested`, `log_requested`, `runtime_event_requested`) | `catalog_changed` → `_on_strategy_catalog_changed` (backtest / targeted refill their own options; the execution combo is now `execution_orchestrator.refresh_strategy_options()`, so the G2-A transitional seam is retired). `account_notice_requested` → **MainWindow composition wiring** → `AccountOrchestrator.set_notice(text)` (finished text only): this is a window bridge, **not** a Strategy → Account dependency. `runtime_event_requested` → `_route_runtime_event` (the window's one router). Governance row selection **never** touches `StrategySelectionService` — the runtime selections (AUTO_ROTATION / TARGETED_SHADOW / BACKTEST) are pointed only by their own page combos | **G2-A complete** |
| **Execution / AutoQuant** | `ExecutionOrchestrator` — the route's own three facts and nothing else: `candidates` (the one retained AutoQuant shortlist), the local launch-busy flag and the channel-probe flag. The AUTO_ROTATION *runtime selection* stays `StrategySelectionService`'s; the scan stays `ScannerOrchestrator.scan`'s; the session stays `PaperOrchestrator.presentation`'s | `ExecutionOrchestrator` (G2-B): the one orchestration caller of `ExecutionPage.render` / `render_candidates` / `render_context` / `render_preflight` / `render_execution_health` / `set_control_state` / `set_strategy_options` / `set_arm_confirmed`. The window only constructs the page and hands it the palette | `StrategySelectionService` (read for `AUTO_ROTATION`); the injected provider group, whose **complete surface is enumerated and frozen by a guard**. Two kinds of entry live in it, and the distinction matters: **(1) every Market-lifecycle-related entry is a read-only fact** — `market_snapshot`, `market_is_live`, `market_provider`, `was_recently_ready`, `recently_ready_symbols`; **(2) explicit narrow callbacks / actions** — `adopt_scan`, `schedule_history`, `refresh_history`, `run_market_scan`, `probe_order_channel` (this route owns *when* to adopt a finished scan, when to queue history, when to scan and when to probe the channel; none of them is a cross-capability interlock) — plus the remaining ambient reads: `fresh_paper_capital`, `account_portfolio`, `broker_state`, `scan`, `universe`, journal rows, `exposure_multipliers`, `research_scenario_capital`, `maximum_position_exposure_pct`, `paper_capability_enabled`, `extended_hours_enabled`. **Any Market start / stop / switch / subscription lifecycle command must travel as `market_*_requested` → MainWindow composition; it may not appear here under any name** — the earlier name-based denylist missed `stop_market_data`, which is why the surface is enumerated rather than guessed at. `PaperOrchestrator`'s six delegated preparation seams. **It imports no capability object at all** — no other orchestrator, no Paper type, no risk/execution application, no broker adapter | `refresh_strategy_options`, `select_strategy`, `refresh_preflight`, `refresh_current`, `refresh_all`, `refresh_controls`, `refresh_extended_hours_status`, `set_scope`, `request_prepare`, `request_channel_check`, `request_start`, `confirm_start`, `request_stop_stream`, `clear_arm_confirmation`, `render_launch_context`, `current_strategy`, `current_preflight`, `candidates`, `capital_limit`, `launch_locked`, `on_paper_result_changed`, `on_paper_session_finalized`, signals (`start_confirmation_requested`, `paper_start_requested`, `market_start_requested`, `market_switch_requested`, `market_subscription_requested`, `market_stop_requested`, `market_readiness_inputs_changed`, `information_requested`, `warning_requested`, `log_requested`) | `strategy_selected` / `preflight_inputs_changed` / `prepare_requested` / `channel_check_requested` / `start_requested` / `stop_stream_requested` reach the route **directly** from the page. `catalog_changed` → `refresh_strategy_options`. `market_orchestrator.controls_changed` → `refresh_controls`. `market_readiness_inputs_changed` → `_publish_market_readiness_inputs` → `MarketReadinessInputs` → `market_orchestrator.set_readiness_inputs`. **The four Market commands are requests, and the window is the interlock**: `market_start_requested` / `market_switch_requested(provider)` / `market_subscription_requested(symbols)` / `market_stop_requested` are consumed by composition, which reads the Paper and Shadow facts and calls the market capability. The stop is the sharpest case — the route does **not** read `has_runtime_obligations` (that seam is not even on its Paper port), so `request_stop_stream` emits one request and stops; `MainWindow._on_execution_market_stop_requested` refuses under Paper obligations, takes Shadow down first when active, then calls `market_orchestrator.stop()`, and hands the outcome back through `on_market_stop_refused()` / `on_market_stopped()` for the route to present. **Execution = request + route presentation only; MainWindow composition = Paper/Shadow interlock + Market command.** `start_confirmation_requested` → `_confirm_execution_start` (the one `QMessageBox`; the answer returns through `confirm_start`), and `paper_start_requested` → `paper_orchestrator.start()`. Paper's `result_changed` / `presentation_refresh_requested` / `session_finalized` are rendered by the route; `manual_recovery_required` stays a window bridge because it combines a Paper fact with the global close drain | **G2-B complete** |

## Bridges that need spelling out

These are the entries above where the direction and the trigger are what a reader
gets wrong.

**Market.** Three separate paths, and they must not be collapsed:

```text
snapshot_changed       → MainWindow._on_market_snapshot_changed
    → workflow readiness, minute snapshot evidence, Dashboard, AutoQuant candidate
      presentation, targeted preflight refresh, the targeted session repaint (when
      Shadow is active), and the stream ingress handed to an active Paper / Shadow
      workflow (each capability decides whether the fact belongs to it)
shell_health_changed   → MainWindow._render_market_shell_health
    → the shell badges only; it does not go through snapshot_changed
load_scan_watchlist_requested (a Market-page user intent)
                       → MainWindow._apply_intraday_watchlist
    → reads Scanner's scan + Account's paper capital + research capital, then sets
      Market's subscription symbols
```

The third is a **Market + Scanner cross-workflow command**, not a reaction to a
snapshot: the operator clicked on the market page.

**History.** The only bridge is `history_changed → _refresh_market_scope_summary`.
The AutoQuant preparation path does schedule history gaps and render the History
page, but that direction is **AutoQuant → History** — AutoQuant calls the capability.

**Cross Section.** Two bridges, neither a second truth:

```text
capital_changed  → MainWindow._on_research_scenario_capital_changed
    → _publish_account_presentation_inputs + AccountOrchestrator.render_current
    (Account presentation only; Scanner / AutoQuant / Targeted / the watchlist pull
     the canonical scalar when they next build a request)
report_changed   → MainWindow._on_cross_section_report_changed
    → reload the artifact catalogue + repaint the Dashboard
```

**Targeted Evidence.** Four bridges, each for a fact that is *not* an evidence
decision:

```text
refused                          → _report_targeted_evidence_refusal (QMessageBox)
runtime_event_requested          → _route_runtime_event (the one store write)
minute_status_refresh_requested  → TargetedSessionOrchestrator.refresh_minute_status
focus_requested                  → _focus_targeted_evidence
                                     (shell.navigate_to("research") +
                                      research_page.set_active_workspace(TARGETED))
```

**System.** Two capabilities, two owners, one page:

```text
Runtime Events: runtime_event_requested (6 capabilities: Market, Account, Shadow,
  Paper, Targeted Evidence, Strategy Governance) → MainWindow._route_runtime_event
  → RuntimeEventsOrchestrator.record → RuntimeEventStore.add (the one write path)
  → coalesced repaint; refresh / resolve / export intents reach the capability directly
export → _export_runtime_bundle (cross-capability facts) → EXPORT_OK → repaint → report
Settings: the page's nine intents → SettingsOrchestrator directly
  render: credential status + live market source, both read on every repaint
  credentials / preferences sequenced here; the transaction stays the service's
  commit ok → settings_committed (window adopts + fans out) → market_switch_requested
  theme preview → MainWindow._apply_theme;  toggles → confirmation → confirm_*(accepted)
```

No `SystemOrchestrator`: the two workspaces share the page and nothing else (§31, §32
of `docs/DESKTOP_DECOMPOSITION.md`).

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

| Module / object | What it is | What it is not |
| --- | --- | --- |
| `desktop_v2/orchestration/tasking.py` | The `TaskSubmitter` protocol and two callable aliases. Every capability's constructor is annotated with it so the task boundary is legible at the call site. | Not a task runner and not a state holder: it is the callable boundary only. Nothing about a task lifecycle is implemented here. |
| `desktop_tasks.py` — `DesktopTaskController` | **The worker collection owner.** Registration, the resource-group admission rule, and every read (`running_workers` / `has_running_workers` / `active_count`, all recomputed from the collection). The collection never leaves the class. | Not where `TaskThread` is constructed and not a signal hub. There is no `MainWindow.workers` alias and no compatibility property either — a second mutable handle on the collection is the bug this class exists to prevent. |
| `runtime_supervisor.py` — `RuntimeSupervisor` | **Shutdown admission truth and generic teardown owner.** `shutting_down` is the one admission fact (`begin_shutdown` raises it, only `cancel_shutdown` lowers it); `shutdown()` releases every registered component in `order`, isolating each failure. | Not a second closing boolean, and not a component registry the window may read behind its back. `MainWindow` deliberately holds no `_closing` flag: gate, drain and refused-close recovery all read this one flag, so they cannot disagree. |
| `MainWindow` (`desktop.py`) | Composes the generic task lifecycle: `TaskThread` construction, signal wiring, generic task submission/composition, the busy dialog/presentation, and the `submit_task` bridge every capability calls. | Not the owner of any of those facts. It composes and drives `DesktopTaskController` and `RuntimeSupervisor`; it owns neither the worker list nor the closing gate. |

## The shape every capability converges on

```
Page intent  ──signal──▶  Orchestrator  ──call──▶  Application / Service  ──▶  Domain
                               │
                               └──render──▶  Page

Cross-workflow facts  ──▶  MainWindow composition bridge  ──▶  whoever else cares
```

A capability owns its own state and its own page. `MainWindow` composes it,
routes its published facts, and composes the generic task lifecycle through
`DesktopTaskController` (the worker collection) and `RuntimeSupervisor` (shutdown
admission and generic teardown) — nothing else.
