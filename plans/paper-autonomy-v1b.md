# Exec plan: Paper Autonomous Trading v1-B

## Goal and completion criteria
Complete the reviewed PR #60 recovery closure and follow-up authorization-race fix on `feat/paper-autonomy-supervisor`. Preserve fail-closed startup, canonical Paper ownership and all existing scope limits. Update evidence, push the branch, wait for CI on the new HEAD, and stop without merging.

## Constraints
- No OCR, OpenCodeReview, Live, real-money execution, AI, or strategy evolution.
- Resolution records `OPERATOR_RESOLVED`; it never claims success/failure/refusal, enables autonomy, clears kill, starts a host/session, removes history, or permits a same-day unattended START retry.
- Preserve unrelated untracked workspace content and keep changes limited to this review.
- Record blocked-event dedup as future observability cleanup; do not widen this patch.

## Context map
- `src/us_quant/trading/application/paper_autonomy_recovery.py` — recovery authority.
- `src/us_quant/trading/ports/paper_autonomy_action_repository.py` and `src/us_quant/trading/adapters/sqlite/paper_autonomy_action_repository.py` — atomic resolution contract and storage.
- `src/us_quant/cli.py` and `src/us_quant/trading/composition/paper_autonomy.py` — operator surface and wiring.
- `tests/test_paper_autonomy_recovery.py` — recovery behavior and authority guards.
- `scripts/mutation_paper_autonomy_b1.ps1` — B1 safety mutants.
- `docs/TRADING_ARCHITECTURE_V2.md` — exact control-cycle formulas and crash recovery.

## Status
- [x] Reviewed exact HEAD and preserved existing unrelated untracked files.
- [x] Implemented operator-resolved terminal status, atomic stale-checked SQLite update, Qt-free recovery application, CLI commands, documentation and regression/architecture cases.
- [x] Replaced timestamp authorization ordering with a durable monotonic revision floor. Recovery now resolves, fresh-reads A1, then seals exactly once; startup fails closed on any unsealed resolution.
- [x] Kept the existing shared read-only `snapshot()` port and CLI resolution output free of a post-commit A1 read; added `seal-action` to resume a crash between resolution and sealing.
- [x] Added a process-lifetime recovery gate: each tick compares the current recovery floor and sealed-resolution generation with immutable startup markers; any change permanently blocks this Supervisor until Desktop restarts and reruns startup proof.
- [x] Added floor integrity guards: a sealed floor cannot predate the action intent revision, and corrupt stored rows fail every full-ledger safety read.
- [x] Required targeted suites: 567 passed. B1 mutation harness: 73/73 red, 0 survivors, 0 harness errors (M73–M76 cover old-process blocking, same-floor generations, parser corruption and seal validation). A1: 26/26 red, 0 survivors, 0 harness errors.
- [x] Python 3.14.7 full suite: 5,180 passed, 0 skipped; doctor, compileall, Desktop offscreen self-test, and `git diff --check` passed.
- [x] Commit and push process-lifetime implementation `d13575c23dd72e4da360c4271f126a0527af9131`; Windows/Python 3.14 CI run `36322505051` passed (`5177 passed, 3 skipped`, the same three unreachable-base skips). PR remains OPEN and unmerged.

## Decision log
- 2026-09-27: Prior PR #60 review findings are addressed on the branch; current GitHub review-thread count was zero before this update. Do not report the historical two P1 threads as unresolved.
- 2026-09-27: Disabled intent is required immediately before resolving; a latched kill with `DISABLED` is allowed and remains latched.
- 2026-09-27: `OPERATOR_RESOLVED` counts as a terminal history row and as a same-day START attempt, but never as a successful control cycle.
- 2026-09-27: Wall-clock timestamps remain audit data only. Startup requires the active A1 revision to be strictly greater than every sealed operator-resolution floor; any unsealed resolution blocks startup until an operator seals it while DISABLED.
- 2026-09-27: Revision-floor implementation passed local and new-head CI validation; only user-directed merge review remains, and this task must leave the PR unmerged.
- 2026-09-27: Revision-floor code commit is pushed; new-head CI and final PR checks passed. PR is OPEN, non-draft, mergeable/CLEAN, with zero unresolved review threads. No merge was performed.
- 2026-09-27: A recovery after Supervisor construction invalidates that process even after explicit re-enable; only a new Desktop process may rerun broker/account startup proof. The action count distinguishes repeated recoveries that share one A1 revision floor.
- 2026-09-27: Process-lifetime follow-up passed local and fresh CI gates. PR is OPEN, non-draft, MERGEABLE/CLEAN, with zero unresolved review threads. No merge was performed.
