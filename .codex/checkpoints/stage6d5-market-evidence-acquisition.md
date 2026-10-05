# Checkpoint: Stage 6-D.5 / PR #89

## Goal
Complete PR #89 as a read-only supervised Paper canary readiness inspector.

## Constraints
Work only in `D:\Codex\USQuant-stage6`; no extra worktree and do not use `D:\Codex\USQuant`. Keep Stage 6-F blocked until real supervised Paper evidence survives process restart and semantic evaluation matches.

## Current phase
PR #89 merged by merge commit `dd14f6792eedf999f636721ee3f8c9106bc700da`; a process-exit/restart regression now persists a Paper performance evaluation in one interpreter and rehydrates it read-only in a fresh interpreter. The real-evidence line is waiting for IBKR Paper port 4002 to return.

## Frozen state
- PR #88 actual merge SHA: `612d940a6d83636c102524bc867556887472381b`.
- `STAGE_6_D5_BASELINE = STAGE_6_89_BASE = 612d940a6d83636c102524bc867556887472381b`.
- PR #89 `https://github.com/daviesjoin-afk/us-quant-research/pull/89` merged; exact feature head `d9a7e49649563f6070d66802b5f321d136b75c62`; actual merge SHA and `origin/main`: `dd14f6792eedf999f636721ee3f8c9106bc700da`.
- `STAGE_6_CANARY_READINESS_BASELINE = dd14f6792eedf999f636721ee3f8c9106bc700da`.
- Stage 6-D.5 implementation and canary readiness inspector are complete. Market evidence is still 0/25; Stage 6-D operational is incomplete; Stage 6-F remains BLOCKED.
- IBKR Paper port `127.0.0.1:4002` is currently not listening; no recorder was started.
- The new subprocess regression is engineering coverage only: the evaluator writes a durable SQLite performance record using deterministic test broker truth, then a fresh process rehydrates only that record read-only. It is not real-canary evidence or an operational restart pass.

## Decisions
- Use read-only SQLite composition; missing performance table means NOT_YET_OBSERVED, without schema writes.
- Reuse canonical evidence readiness, coverage validator, PaperLaunchAuthorizer, and portfolio plan validation. Offline cannot claim READY_FOR_CANARY; reconciliation is NOT_CHECKED when safely unavailable.
- Keep Stage 6-F blocked; no operational evidence or restart proof exists.

## Changed or inspected files
- Application projection, read-only composition, offline-default CLI, explicit opt-in read-only broker refresh, and 40-mutant script are added.
- Existing portfolio plan application is isolated in `trading/application/portfolio_plan.py` so inspection avoids PortfolioRuntime import.
- Relevant read-only adapters and architecture guards updated; 37 inspector tests and 40-mutant gate added.

## Verification (2026-10-05)
- PR #89 focused tests: 48 passed. Mutation gates: #89 49/49, D5 40/40, D2 15/15, 6-E 37/37; no survivors or harness errors.
- Full suite after three review rounds: 6472 passed, 1 skipped (POSIX file-mode check on Windows). Doctor, compileall, Ruff on new files, and `git diff --check` passed.
- Actual offline CLI: DATA_COLLECTION; both versions RESEARCH; all five targets 0/25 COLLECTING; performance NOT_YET_OBSERVED; plan MISSING; broker NOT_RUN; reconciliation NOT_CHECKED; Stage 6-F blocker present.
- Local IBKR Paper port `127.0.0.1:4002` was previously not listening; quote DB had no captured rows.

## Blockers or risks
Real market capture, governed Paper strategies, portfolio plan, live broker preflight, canonical reconciliation, and supervised restart proof remain outstanding; 6-F is BLOCKED.

## Next action
Inspect how production broker account/open-order observations can participate in a durable restart proof without treating a test fixture as operational truth. In parallel, recheck port 4002; start the real evidence recorder only after the Paper API is reachable and never switch providers as a fallback. Keep Stage 6-F BLOCKED until real supervised Paper evidence survives restart with the same semantic evaluation.
