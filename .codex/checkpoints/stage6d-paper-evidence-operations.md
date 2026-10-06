# Task checkpoint: PR #93 Paper Evidence Acquisition Operations

## Goal
Unblock the real-data bottleneck with a read-only readiness diagnostic; add no
trading capability and no new authority.

## Constraints
- Work only in `D:\Codex\USQuant-stage6`.
- Branch `feat/stage6d-paper-evidence-operations` based on PR #92's actual merge
  SHA `416bced523297ae226cbdf668c0c4826c7f94d69` (not an old branch).
- Diagnostic is read-only: no recorder start, no database write, no provider
  switch, no config edit, no order.
- No new authority. Authorities stay MarketEvidenceQuality, StrategyLifecycle,
  PaperAuthorization and PortfolioRuntime.
- Stage 6-F stays blocked; Stage 6-D operational stays incomplete.
- Real-environment capture (port 4002) is handled outside the PR.

## Baseline
- PR #92 merged: state=MERGED, mergeCommit=416bced523297ae226cbdf668c0c4826c7f94d69
  = origin/main.
- STAGE_6_D_OPERATIONAL_PROOF_TOOLING_BASELINE = 416bced523297ae226cbdf668c0c4826c7f94d69

## Current phase
PR #93 OPEN at head `3289f216456ad2d3731e4f78d269f227e89011ef`. All local gates
green; waiting on exact-head CI and the review pass.

## Decisions
- Status vocabulary is READY / DATA_COLLECTION / BLOCKED. DATA_COLLECTION is the
  "stream is up, nothing captured yet" case; the CLI renders it as
  BLOCKED_DATA_COLLECTION because it still blocks the operator.
- Live ports 4001/7496 are refused by name, distinct from "not a paper port".
- A health snapshot older than 120 s is treated as a stalled stream.
- Review follow-up: an expected symbol that is not in the realtime symbol list
  blocks, and a future health timestamp fails closed.

## Changed files
- `src/us_quant/trading/application/paper_evidence_readiness.py` — pure projection.
- `src/us_quant/trading/composition/paper_evidence_readiness.py` — read-only wiring.
- `src/us_quant/paper_evidence_readiness.py` — CLI.
- `tests/test_paper_evidence_readiness.py` — R93-01..R93-10 (50 tests).
- `scripts/mutation_paper_evidence_readiness_93.ps1` — 32 mutants.

## Verification (final commit 3289f21)
- Full pytest — 6637 passed, 1 skipped.
- #93 mutation — 32/32 RED, 0 survivors, 0 harness errors.
- #92 mutation — 70/70; #91 20/20; D5 40/40; D2 15/15; 6-E 37/37, all zero
  survivors and zero harness errors.
- doctor, compileall — passed.
- exact-head CI and review — pending.

## Blockers or risks
- `127.0.0.1:4002` is closed locally, so the CLI reports BLOCKED on this machine;
  that is the correct answer, not a defect.
- Mutation gates mutate `src/` in place, so they must run strictly one at a time.
  A parallel run leaves LF-rewritten files behind; `git diff --quiet` still exits
  0 (no content change) under `core.autocrlf=true`.

## Next action
Confirm exact-head CI, resolve review threads, report.
