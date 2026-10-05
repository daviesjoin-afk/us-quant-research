# Exec plan: Stage 6-D.5 market evidence acquisition

## Purpose and success criteria
Add one read-only, restart-safe market evidence capture path that uses the existing MarketDataApplication, persists captured snapshots append-once, shares one canonical quality calculation across robustness/data-quality/readiness, and reports readiness without issuing governance decisions. Open PR #88 on the user-specified baseline after focused, mutation, full-suite and safety checks.

## Context map
- `src/us_quant/minute_data.py` — durable minute storage and schema migration.
- `src/us_quant/targeted_robustness.py` — session usability rules.
- `src/us_quant/targeted_data_quality.py` — high-quality session rules.
- `src/us_quant/desktop_market_service.py` and market composition — existing provider-neutral MarketData application.
- `src/us_quant/desktop_targeted_evidence_service.py` — present targeted research pipeline.
- `tests/` — behavior characterization and architecture guards.

## Milestones
- [x] M1: Branch from exact baseline; inspect composition; characterize current session rules and append-once persistence.
- [x] M2: Implement capture application, CLI/composition using existing MarketDataApplication, health reporting, readiness model, shared canonical quality primitive; remove duplicate rules.
- [x] M3: Add focused, restart, architecture, timezone, data-quality and readiness tests; implement D5 mutation harness and run it.
- [x] M4: Run required regression suites, full tests, doctor, compileall and diff checks; open exact-head PR #88.
- [x] M5a: Freeze PR #88 on exact head (CI, Codex review and zero unresolved threads).
- [x] M5b: PR #88 merged; start the separate read-only canary readiness inspector PR #89 from its actual merge SHA.
- [x] M6: Implement and locally verify PR #89 inspector, CLI, read-only composition, architecture guards, mutation gates, and full regression suite.
- [x] M7: Commit/push PR #89 branch, open PR, then validate exact-head CI/review and unresolved threads; merge only after all exact-head gates pass.
- [x] M8: Add a subprocess exit/restart regression that persists a Paper performance evaluation in one process and rehydrates the same canonical result read-only in a fresh process; keep this separate from real operational evidence.

## Decision log
- 2026-10-03: Keep scope to PR #88 first because the request explicitly sequences #89 after its merge.
- 2026-10-03: No review or governance artifacts may be generated from the empty quote DB; collector readiness cannot manufacture evidence.
- 2026-10-05: The pre-existing performance "restart" test rebuilt repository/application objects in the same Python process and reused in-memory broker observations. Added a two-process regression: one process writes the evaluation and exits; a fresh process loads the canonical evaluation read-only from SQLite. Its deterministic broker fixture is test input only and does not prove real broker observations are durable.

## Completion record
- 2026-10-03: Exact-baseline feature branch confirmed; shared quality primitive and append-once capture path implemented; focused suite currently 49/49 green, compileall and diff check pass.
- 2026-10-04: PR #88 is open from exact base `3efbf40f0a11cc137faf82a4df414ef5e8d93045`, current head `9a88299cae4dbf155d6dbc8be7536830dfa45ed4`.
- 2026-10-04: Full verification passed (6,402 passed, 1 skipped); D5 25/25, D2 15/15, and 6-E 37/37 mutants were all killed. Exact-head GitHub CI passed; unresolved review threads are 0. No merge attempted.
- 2026-10-04: Review caught preview/import rows sharing the minute uniqueness key; capture now replaces only non-captured cache rows and preserves first-write-wins for captured evidence.
- 2026-10-04: No local rows exist yet for SPY/QQQ/AAPL/NVDA. The configured Paper API socket `127.0.0.1:4002` is not listening, so the recorder was not started and no provider fallback was attempted.
- 2026-10-04: Exact-head review on `c65f16af5ddb7f3bf4e14847c4dec5a907df4e37` found synchronous SQLite writes on push listener callbacks. Changed CLI wiring to persist only from periodic snapshots; focused evidence/migration/replay tests passed 56/56 and D5 mutations killed 36/36. New head verification is pending.
- 2026-10-04: Exact-head review on `5b5164b24251e5c9084cb9917c9ca8f7d92d9238` found extended-IBKR session route rotation missing and disconnected health mislabeled RUNNING. Added orderly stop/join/reprepare routing, explicit DISCONNECTED/NOT_READY status, and regressions. Focused suite passed 111/111; D5 mutations killed 38/38. New head `76e18568f6cb13f29a31784a8da03a2a74d69938` is pushed; exact-head CI and review are pending, all known threads resolved.
- 2026-10-04: Exact-head review on `76e18568f6cb13f29a31784a8da03a2a74d69938` found non-realtime streams could still report RUNNING. Status now returns `NON_REALTIME`; focused suite passed 111/111 and D5 mutations killed 39/39. Final fix pending commit/push.
- 2026-10-04: Exact-head review on `cbd15f7cf130857f5370538e5736c43f5d6b9333` found cache-origin rows could enter robustness/data-quality research and unrelated provider credentials were decrypted on startup. Research analysis now filters to `captured_stream` while replay keeps cache rows; credential resolution accepts a provider scope used by the CLI. Focused targeted research, credential and D5 tests passed 140/140; D5 mutations killed 40/40. Final fix pending commit/push.
- 2026-10-04: CI for `de32be5c487bec8130e28ef653728481a427514b` found a six-line service budget overage and one architecture fake missing captured provenance; both are corrected. Focused regression passed 142/142; D5 mutations killed 40/40. New head verification pending after push.
- 2026-10-04: Final PR #88 head is `01fce42993dacbf3b39c3434fd5d8e3f1db46c45`, base remains `3efbf40f0a11cc137faf82a4df414ef5e8d93045`. Exact-head CI run `37212342796` passed; exact-head Codex review found no major issues; all 12 review threads are resolved. Local focused validation passed 142/142 and D5 mutations were killed 40/40. PR is OPEN/MERGEABLE; no merge was attempted. M5a is complete; M5b awaits merge. Stage 6-F remains blocked pending the supervised Paper durable-evidence restart loop.
- 2026-10-05: User explicitly authorized merge-commit merge of PR #88 after exact-head checks. Actual merge SHA `612d940a6d83636c102524bc867556887472381b` equals `origin/main`; PR state is MERGED. Frozen baseline: `STAGE_6_D5_BASELINE = STAGE_6_89_BASE = 612d940a6d83636c102524bc867556887472381b`. D5 implementation is complete; operational data collection remains incomplete; Stage 6-F remains BLOCKED.
- 2026-10-05: Created `feat/stage6d5-paper-canary-readiness-inspector` at the frozen merge baseline and began PR #89 implementation. Inspector prototype focused tests passed 35/35; all final verification is pending.
- 2026-10-05: PR #89 inspector and CLI implemented with offline default, explicit live broker refresh, read-only SQLite stores, canonical authorizer/coverage/evidence/plan validation, and no execution or lifecycle actions. Missing performance table now projects NOT_YET_OBSERVED without a write or false read-failure blocker.
- 2026-10-05: PR #89 opened at `7dcfdaa3b930f10d238f03e89f57f295a4d05f29`, base is the #88 merge SHA. Exact-head Codex review found one P1 and three P2 issues: incomplete target coverage; offline config dependency; corrupt performance DB traceback; Git revision cwd. Fixed all four with regressions, removed one now-redundant mutant, and expanded #89 gate to 43 meaningful mutants.
- 2026-10-05: Updated head review found missing broker balance checks, unsupported `timedelta` JSON conversion, and dirty-checkout revision mismatch. Added positive net liquidation/cash availability projection and blockers, stable duration string serialization, and clean-worktree requirement for runtime revision. Added regressions and expanded the #89 mutation gate to 47 meaningful mutants.
- 2026-10-05: After both review rounds: focused tests 45/45; #89 mutations 47/47; D5 40/40; D2 15/15; 6-E 37/37; full suite 6469 passed, 1 Windows/POSIX-only skip. Doctor, compileall, Ruff on new files, checkpoint validation, and diff check passed. Actual offline CLI remains DATA_COLLECTION, five targets 0/25, both strategies RESEARCH, plan MISSING, broker NOT_RUN, reconciliation NOT_CHECKED, 6-F BLOCKED. Second-round commit and exact-head CI/review remain pending.
- 2026-10-05: Third exact-head review found ancestor-worktree Git revision resolution and stale reconciliation reuse. Revision resolution now checks clean status and that Git top-level equals the resource root. Reconciliation freshness reuses the canonical five-minute snapshot limit and must follow the supplied broker snapshot; no reconciliation algorithm was copied.
- 2026-10-05: After three review rounds: focused inspector tests 48/48; #89 mutations 49/49; D5 40/40; D2 15/15; 6-E 37/37; full suite 6472 passed, 1 Windows/POSIX-only skip. Doctor, compileall, Ruff, checkpoint validation, and diff check passed. Final review fixes remain uncommitted pending the next exact-head remote CI/review.
- 2026-10-05: PR #89 exact head `d9a7e49649563f6070d66802b5f321d136b75c62` passed CI run `37313173642`; exact-head Codex review found no major issues and all 9 review threads are resolved. User authorized a merge commit; PR #89 merged as `dd14f6792eedf999f636721ee3f8c9106bc700da`, equal to `origin/main`. Frozen `STAGE_6_CANARY_READINESS_BASELINE` at that actual merge SHA. Canary readiness is implemented; current offline CLI still reports DATA_COLLECTION with five targets at 0/25. Stage 6-D operational remains incomplete and Stage 6-F remains blocked. Port 4002 is not listening; no real recorder was started.
- 2026-10-05: Added a process-boundary regression for Paper performance: one subprocess writes the evaluation and exits; a fresh subprocess opens the performance store read-only and rehydrates the same canonical evaluation without a broker connection. Its write process uses a deterministic test broker fixture. Focused application tests pass 14/14; real-canary evidence and operational restart proof remain outstanding.
