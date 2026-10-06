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
PR #93 OPEN at head `1bfcc25`. All local gates green; awaiting exact-head CI.

## Decisions
- Status vocabulary is READY / DATA_COLLECTION / BLOCKED. DATA_COLLECTION is the
  "stream is up, nothing captured yet" case; the CLI renders it as
  BLOCKED_DATA_COLLECTION because it still blocks the operator.
- Live ports 4001/7496 are refused by name, distinct from "not a paper port".
- A health snapshot older than 120 s, or with a future timestamp, is stalled.
- Realtime membership is judged against the requested targets, not the
  recorder's own expected list.
- The evidence store blocker is independent of connectivity and always reported.
- Health transport is explicit (`--health-log` file, or `--health-stdin` as a
  bounded snapshot) because the recorder only prints health to stdout.

## Changed files
- `src/us_quant/trading/application/paper_evidence_readiness.py` — pure projection.
- `src/us_quant/trading/composition/paper_evidence_readiness.py` — read-only wiring.
- `src/us_quant/paper_evidence_readiness.py` — CLI.
- `tests/test_paper_evidence_readiness.py` — R93-01..R93-10 (59 tests).
- `scripts/mutation_paper_evidence_readiness_93.ps1` — 39 mutants; the table is
  JSON parsed with ConvertFrom-Json so no anchor needs PowerShell escaping.

## Verification (commit 1bfcc25)
- Full pytest — 6646 passed, 1 skipped.
- #93 mutation — 39/39 RED, 0 survivors, 0 harness errors.
- #92 mutation — 70/70; #91 20/20; D5 40/40; D2 15/15; 6-E 37/37, all zero
  survivors and zero harness errors.
- doctor, compileall — passed.
- Real environment: port 4002 CLOSED; readiness CLI reports BLOCKED with
  IBKR_SOCKET_UNREACHABLE + MARKET_STREAM_NOT_OBSERVED and 0/25 for every target.
- exact-head CI — pending on 1bfcc25.

## Blockers or risks
- `127.0.0.1:4002` is closed locally; BLOCKED is the correct answer, not a defect.
- Mutation gates rewrite `src/` in place and must run strictly one at a time.

## Next action
Confirm exact-head CI, resolve any new review threads, report.
