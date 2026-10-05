# Checkpoint: Stage 6-D.5 / PR #89

## Goal
Complete PR #89 as a read-only supervised Paper canary readiness inspector.

## Constraints
Work only in `D:\Codex\USQuant-stage6`; no extra worktree and do not use `D:\Codex\USQuant`. Keep Stage 6-F blocked until real supervised Paper evidence survives process restart and semantic evaluation matches.

## Current phase
PR #89 open; Codex review findings fixed in a follow-up commit; final exact-head CI/review pending.

## Frozen state
- PR #88 merged; exact feature head `01fce42993dacbf3b39c3434fd5d8e3f1db46c45`; actual merge SHA and `origin/main`: `612d940a6d83636c102524bc867556887472381b`.
- `STAGE_6_D5_BASELINE = STAGE_6_89_BASE = 612d940a6d83636c102524bc867556887472381b`.
- PR #89 branch `feat/stage6d5-paper-canary-readiness-inspector` starts at that SHA.
- PR #89: https://github.com/daviesjoin-afk/us-quant-research/pull/89; initial head `7dcfdaa3b930f10d238f03e89f57f295a4d05f29`; OPEN/MERGEABLE, based on `main` at the frozen baseline. Exact-head review reported one P1 (every version needs a target) and three P2s (offline config, corrupt performance DB, Git cwd); all four are fixed and covered. Updated exact-head CI/review pending after push.
- D5 implementation complete; operational capture incomplete; Stage 6-F BLOCKED.

## Decisions
- Use read-only SQLite composition; missing performance table means NOT_YET_OBSERVED, without schema writes.
- Reuse canonical evidence readiness, coverage validator, PaperLaunchAuthorizer, and portfolio plan validation. Offline cannot claim READY_FOR_CANARY; reconciliation is NOT_CHECKED when safely unavailable.
- Keep Stage 6-F blocked; no operational evidence or restart proof exists.

## Changed or inspected files
- Application projection, read-only composition, offline-default CLI, explicit opt-in read-only broker refresh, and 40-mutant script are added.
- Existing portfolio plan application is isolated in `trading/application/portfolio_plan.py` so inspection avoids PortfolioRuntime import.
- Relevant read-only adapters and architecture guards updated; 37 inspector tests and 40-mutant gate added.

## Verification (2026-10-05)
- PR #89 focused tests: 41 passed. Mutation gates: #89 43/43, D5 40/40, D2 15/15, 6-E 37/37; no survivors or harness errors.
- Full suite after review fixes: 6465 passed, 1 skipped (POSIX file-mode check on Windows). Doctor, compileall, Ruff on new files, and `git diff --check` passed.
- Actual offline CLI: DATA_COLLECTION; both versions RESEARCH; all five targets 0/25 COLLECTING; performance NOT_YET_OBSERVED; plan MISSING; broker NOT_RUN; reconciliation NOT_CHECKED; Stage 6-F blocker present.
- Local IBKR Paper port `127.0.0.1:4002` was previously not listening; quote DB had no captured rows.

## Blockers or risks
Real market capture, governed Paper strategies, portfolio plan, live broker preflight, canonical reconciliation, and supervised restart proof remain outstanding; 6-F is BLOCKED.

## Next action
Commit/push the review fixes, then wait for updated exact-head CI and Codex review; inspect comments and unresolved threads. Do not merge without all user-specified gates. Operational collection and restart proof remain outstanding.
