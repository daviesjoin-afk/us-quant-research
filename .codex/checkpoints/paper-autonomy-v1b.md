# Task checkpoint: Paper autonomy v1-B recovery closure

## Goal
Finish the latest PR #60 recovery review on `feat/paper-autonomy-supervisor`, push the verified change, wait for new-head CI, and stop without merging.

## Constraints
- Preserve NO OCR / NO OpenCodeReview / NO Live / NO real money / NO AI / NO strategy evolution.
- Resolution requires freshly read `DISABLED` intent; it never enables, clears kill, starts a host/session, asserts an outcome, deletes history, or releases the same-day START limit.
- After resolution, startup accepts ENABLED/PAUSED only when the A1 intent revision is strictly greater than the latest sealed recovery floor. Any unsealed OPERATOR_RESOLVED row blocks startup. Timestamps remain audit data only.
- Preserve canonical Paper / Execution / Risk ownership, unrelated untracked files, and minimal scope. Block-event dedup remains future cleanup.

## Current phase
Process-lifetime recovery gate is implemented and locally verified on top of reviewed PR HEAD `a2773247d1b5af06bc0cbd230b491680126a28ae`. Targeted suites pass (567); B1 is 73/73 RED and A1 is 26/26 RED. Python 3.14.7 full suite passed (5,180, zero skips); doctor, compileall, Desktop offscreen self-test, and `git diff --check` passed. Commit and push this follow-up, then wait for fresh Windows/Python 3.14 CI. PR #60 must stay OPEN and unmerged.

## Decisions
- `OPERATOR_RESOLVED` is terminal and distinct from owner outcomes; same-day START still counts, while successful control-cycle counts remain unchanged.
- Kill-latched + `DISABLED` permits resolution and leaves kill latched. Only a new Desktop process reruns startup proof.
- A currently `ENABLED`/`PAUSED` intent is startup-safe after resolution only if `revision > latest_authorization_floor_revision`; equal, earlier, invalid, or unreadable state blocks. `DISABLED` remains safe because it cannot authorize autonomous work.
- Recovery reads intent through the existing `PaperAutonomyIntentReaderPort.snapshot()`; CLI success output has no post-commit A1 read.
- Resolution commit is followed by a fresh A1 read and a write-once SQLite seal. A crash between those operations leaves an unresolved record; startup rejects the full ledger until `seal-action` completes while DISABLED.
- Supervisor startup retains the latest recovery floor and sealed-resolution count. Each tick rereads both; any change after startup permanently blocks the current process, including while DISABLED or after a later explicit enable. Restart Desktop to rerun broker/account startup proof. The count catches multiple resolutions that share one A1 floor.
- A stored authorization floor below its action's `intent_revision` is corrupt. Both full-ledger parsing and the seal write boundary reject it.
- The previous revision-floor PR check confirmed implementation HEAD `ca1a7987f50f4c0b38e174a326ea86702c9dccdd`, successful CI, and zero unresolved threads; the current process-lifetime follow-up requires its own new-head check.

## Changed or inspected files
- Recovery: `src/us_quant/trading/application/paper_autonomy_recovery.py`, shared supervisor intent port, action repository port/SQLite adapter, enum, composition, CLI.
- Guards/evidence: recovery and CLI tests, `tests/test_trading_architecture.py`, B1 mutation harness, `docs/TRADING_ARCHITECTURE_V2.md`, plan and this checkpoint.
- Preserve unrelated untracked content listed by `git status` under `.codex/`, `00_项目档案/`, and `plans/desktop-research-v2*`.

## Verification
- Current process-lifetime targeted suites — 567 passed.
- B1 73/73 (M73–M76 added), A1 26/26; 0 survivors and 0 harness errors.
- Python 3.14.7 `pytest -q` — 5,180 passed, 0 skipped; doctor, compileall, Desktop offscreen self-test, and `git diff --check` passed.
- The last CI run `36318898478` covers the previous HEAD only; fresh CI for the process-lifetime follow-up is pending.

## Blockers or risks
- Local gates pass; new-head GitHub CI and final PR-state verification remain.

## Next action
Commit and push the follow-up, wait for new-head CI, and recheck PR state and unresolved threads. Do not merge.
