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
- No new authority. The authorities stay MarketEvidenceQuality,
  StrategyLifecycle, PaperAuthorization and PortfolioRuntime.
- Stage 6-F stays blocked; Stage 6-D operational stays incomplete.
- Real-environment capture (port 4002) is handled outside the PR.

## Baseline
- PR #92 merged: state=MERGED, mergeCommit=416bced523297ae226cbdf668c0c4826c7f94d69
  = origin/main.
- STAGE_6_D_OPERATIONAL_PROOF_TOOLING_BASELINE = 416bced523297ae226cbdf668c0c4826c7f94d69

## Current phase
Implementation, tests and mutation gate complete and committed; running the full
verification chain before opening the PR.

## Decisions
- Status vocabulary is READY / DATA_COLLECTION / BLOCKED. DATA_COLLECTION is the
  "stream is up, nothing captured yet" case; the CLI renders it as
  BLOCKED_DATA_COLLECTION because it still blocks the operator.
- Live ports 4001/7496 are refused by name, distinct from "not a paper port".
- A health snapshot older than 120 s is treated as a stalled stream, so a stale
  "realtime" flag cannot read as READY.

## Changed files
- `src/us_quant/trading/application/paper_evidence_readiness.py` — pure projection.
- `src/us_quant/trading/composition/paper_evidence_readiness.py` — read-only wiring.
- `src/us_quant/paper_evidence_readiness.py` — CLI.
- `tests/test_paper_evidence_readiness.py` — R93-01..R93-10 (47 tests).
- `scripts/mutation_paper_evidence_readiness_93.ps1` — 30 mutants.

## Verification
- Focused: `tests/test_paper_evidence_readiness.py` — 47 passed.
- #93 mutation — 30/30 RED, 0 survivors, 0 harness errors.
- Architecture gates — 242 passed.
- doctor, compileall — passed.
- Full suite, #92/#91/D5/D2/6-E mutation, exact-head CI and review — pending.

## Blockers or risks
- `127.0.0.1:4002` is closed locally, so the CLI reports BLOCKED on this machine;
  that is the correct answer, not a defect.
- Mutation gates mutate `src/` in place, so they must run strictly one at a time.

## Next action
Finish the verification chain, push, open PR #93, run exact-head CI and review,
resolve every thread, then report.
