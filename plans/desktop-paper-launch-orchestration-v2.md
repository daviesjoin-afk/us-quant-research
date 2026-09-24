# v2O-E1 — Paper Launch / Arm Orchestration

Branch `refactor/desktop-paper-launch-orchestration-v2`, baseline `508cda9` (origin/main,
PR #47 v2O-D). PR title `refactor: extract desktop Paper launch orchestration`. Not merged.

## Scope

READY → CONNECTING → (2nd preflight + identity revalidation) → broker/account/capital
validation → build runtime → arm → ensure promotable → publish_armed → promote → RUNNING.

Out of scope (E2/E3/E4): pause/resume/stop, HALT recovery, manual reconciliation,
finalization, closeEvent teardown, active-session rendering.

## New package `src/us_quant/desktop_v2/orchestration/paper/`

Budgets `__init__.py ≤60 / models.py ≤180 / queries.py ≤250 / orchestrator.py ≤450`.

- `__init__.py` — exports `PaperOrchestrator` only.
- `models.py` — frozen shapes + operator wording + the two injected Protocols. Qt-free.
  - `PaperOrderChannel(config, repository, extended_hours_enabled)` — the channel an
    attempt connects with, read once at plan time so `IBKRConnectionConfig` construction
    stays in the composition root.
  - `PaperLaunchRequest(plan, strategy_version, candidates, order_channel)`.
  - `PaperAccountReading(net_liquidation: Decimal, cash: Decimal, account_alias: str)`.
  - `PaperSessionBuildResult(engine, orders, session_id, candidate_symbols, candidate_count)`.
  - `PaperLaunchPublication(runtime, result, session_id, candidate_count)`.
  - `PaperLaunchEvent(severity, component, code, message)`.
  - `PaperLaunchWorkflow` Protocol — `phase`, `active_plan`, `begin_connecting`,
    `reject_connecting` → bool, `publish_armed`. Closes the import (Shadow `ShadowLease`
    precedent) and makes the orchestrator fakeable.
  - `PaperSessionBuilder` type alias — the narrow build seam.
- `queries.py` — pure rules, no Qt/I-O/broker: `launch_attempt_in_flight(phase)`,
  `validate_broker_state(...) -> str | None`, `current_inputs_match(...)`,
  `freeze_launch(...)`, `preflight_failure_text(...)`. Reuses `us_quant.auto_launch`
  rather than copying it.
- `orchestrator.py` — sequencing only.

## Ownership (unchanged from the brief)

Workflow owns phase/active_plan/lease; `PaperTradingService` owns candidate/active
service; coordinator owns armed-session sequencing; `TradingRuntime` owns the session.
The orchestrator stores **no** mirror of any of them — in particular no `_active_plan`,
no phase, no lease flag. Canonical attempt-in-flight truth is
`workflow.phase is CONNECTING` (exactly equivalent to today's
`_active_auto_launch_plan is not None`, and correct after publish where
`active_plan` outlives the attempt into RUNNING).

## `PaperOrchestrator` surface

Signals `refused(str,str)`, `log_requested(str)`, `session_published(object)`,
`runtime_event_requested(object)`. One method: `start()`. Private `_connect_finished`.

Injected: `launch_workflow`, `paper_trading`, `shadow_is_active`,
`preflight_provider`, `strategy_provider`, `candidates_provider`,
`capital_limit_provider`, `order_channel_provider`, `session_builder`,
`health_evaluator`, `clear_arm_confirmation`, `render_launch_state`,
`render_launch_context`, `submit_task` (`TaskSubmitter`).

## start() order (each step faithful to the retired handler)

1. duplicate gate — `phase is CONNECTING` → refuse, **no** arm clear (as today).
2. shadow active → `clear_arm_confirmation()` + refuse.
3. first preflight → `clear_arm_confirmation()` + refuse with the bulleted failure list.
4. `queries.freeze_launch` — attempt counter (internal, integer, starts 0), strategy,
   candidates, capital limit, order channel read once.
5. `begin_connecting(plan)` — the workflow acquires PAPER. `WorkflowStateError` → refuse.
6. `render_launch_state()` + `render_launch_context(CONNECTING_SUMMARY)`.
7. submit the connect task through the injected `TaskSubmitter`, `resource_group="broker"`.
   Not admitted → `reject_connecting` + `render_launch_state()`.

## `_connect_finished(result)` — stale protection preserved

A shape → `TypeError` loudly (no `except Exception: return`). B connect error →
reject without touching any candidate. C stale plan → `discard_candidate` and
`reject_connecting` only. D second preflight. E identity revalidation. Any reject path
goes through one helper: `discard_candidate` in `try`, `reject_connecting` + arm clear +
render + log in `finally`, dialog only when `reject_connecting` returned True and
`show_message` (preserves today's `reset and show_message`).

## Publish ordering (hard constraint)

`candidate_service` borrowed → read broker state → `queries.validate_broker_state` gate →
`session_builder(request, service, reading)` (build config/runtime, `start()`, `arm()`) →
`ensure_candidate_can_promote` → `publish_armed` → `promote_candidate` → emit
`session_published`. Any exception before publish → reject that candidate, never active.
`ensure_candidate_can_promote < publish_armed < promote_candidate` locked.

## Composition root (MainWindow)

Two callables keep the window the composition root without keeping launch sequencing:

- `_build_paper_session(request, service, reading)` — the narrow seams: resolves capital
  via `resolve_paper_session_capital` (Decimal chain, never float), builds the risk
  authority, the execution application over the **borrowed** candidate, the auto-rotation
  config and the runtime, calls `runtime.start()`, then `service.arm(...)`. Returns
  `PaperSessionBuildResult`. This is where `IBKRConnectionConfig`-based composition and
  `RiskApplication`/`ExecutionApplication` construction stay, so the orchestrator imports
  no adapter and no application.
- `_on_paper_session_published(publication)` — renders via the existing
  `_apply_paper_workflow_result` and keeps the E2-legacy `trading_runtime` /
  `auto_quant_snapshot` assignment.

## MainWindow removals

Deleted: `_start_auto_quant`, `_auto_order_service_connected`,
`_reject_unpublished_auto_candidate`, `_reject_auto_launch_without_service`,
`_current_auto_launch_matches`, `_reset_auto_launch_controls`, and the
`_active_auto_launch_plan` / `_next_auto_launch_attempt` attributes. No shim, no
forwarding property. `_confirm_and_start_auto_quant` **stays** as a pure presentation
gate (confirmation dialog + `set_arm_confirmed`) that calls `paper_orchestrator.start()` —
the brief forbids the orchestrator from importing `QMessageBox`. `_launch_locked` and that
gate's duplicate check read `paper_trading.phase() is CONNECTING` instead of the attribute.

Still on the window (E2/E3/E4): `_apply_paper_workflow_result`, `_poll_auto_quant_orders`,
Paper ingress in `_on_market_snapshot_changed`, pause/resume/stop, manual reconciliation,
finalization refresh, `_finish_auto_quant_session_if_safe`, `closeEvent` teardown,
execution-page session rendering, `_auto_quant_preflight` (cross-capability aggregation).

## Tests

New: `tests/test_desktop_paper_launch_orchestrator.py` (fakes; start/duplicate,
stale callback, five second-preflight variants, four broker gates, publish ordering,
seven failure paths asserting phase/lease/candidate/active/result),
`tests/test_desktop_paper_orchestration_architecture.py` (Guards A–G incl. the
`candidate_service(` single-call-site allowlist and line budgets),
`tests/test_desktop_v2_paper_wiring.py` (real offscreen `MainWindow` + `ExecutionPage`:
signal → orchestrator → fake submitter/service/builder → RUNNING, exactly one candidate,
active once, no window launch handler; duplicate signal twice → one task, one candidate,
lease neither released nor replaced).

Updated (assertions re-pointed from `MainWindow` methods to `PaperOrchestrator`; the
safety meaning is kept, not dropped): `test_desktop_paper_trading_service_wiring.py`,
`test_desktop_paper_controller.py`, guard N in `test_trading_architecture.py`,
`test_desktop_capability_map.py` (Paper row points at the capability and says
"v2O-E1 launch orchestration complete / v2O-E partial" — never "v2O-E complete").

## Docs

`docs/DESKTOP_CAPABILITY_MAP.md` (one-line Paper row; the map is at 148/150 lines so the
edit must be net-neutral), `docs/DESKTOP_DECOMPOSITION.md`,
`docs/TRADING_ARCHITECTURE_V2.md`, forward reference in
`docs/PAPER_TRADING_DECOMPOSITION.md`. E2/E3/E4 listed as next stages.

## Verify

`.\scripts\verify.ps1 -Workers 4` green (full pytest, doctor, compileall, offscreen
self-test). Windows CI on Python 3.12 and 3.13 green. No frozen core file touched
unless a test proves a safety bug.
