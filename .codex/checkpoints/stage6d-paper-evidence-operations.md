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
PR #93 OPEN at head `edd4ffb`. All local gates green; awaiting exact-head CI on
this head and final review resolution.

## Decisions
- Status vocabulary is READY / DATA_COLLECTION / BLOCKED. DATA_COLLECTION is the
  "stream is up, nothing captured yet" case; the CLI renders it as
  BLOCKED_DATA_COLLECTION because it still blocks the operator.
- Live ports 4001/7496 are refused by name, distinct from "not a paper port".
- A health snapshot older than 120 s, or with a future timestamp, is stalled.
- Realtime membership is judged against the requested targets, not against the
  recorder's own expected list, so a narrow recorder cannot certify targets it
  never subscribed to.
- The evidence store is independent of connectivity: its blocker is reported
  even when the socket and stream also failed.
- The recorder prints health JSON to stdout and owns no log file, so the
  transport is explicit: `--health-log <redirected file>` or `--health-stdin`.

## Changed files
- `src/us_quant/trading/application/paper_evidence_readiness.py` — pure projection.
- `src/us_quant/trading/composition/paper_evidence_readiness.py` — read-only wiring.
- `src/us_quant/paper_evidence_readiness.py` — CLI.
- `tests/test_paper_evidence_readiness.py` — R93-01..R93-10 (57 tests).
- `scripts/mutation_paper_evidence_readiness_93.ps1` — 37 mutants.

## Verification (commit edd4ffb)
- Full pytest — 6644 passed, 1 skipped.
- #93 mutation — 37/37 RED, 0 survivors, 0 harness errors.
- #92 mutation — 70/70; #91 20/20; D5 40/40; D2 15/15; 6-E 37/37, all zero
  survivors and zero harness errors.
- doctor, compileall — passed.
- Real environment: port 4002 CLOSED; readiness CLI reports BLOCKED with
  IBKR_SOCKET_UNREACHABLE + MARKET_STREAM_NOT_OBSERVED and 0/25 for every target.
- exact-head CI — pending on edd4ffb.

## Blockers or risks
- `127.0.0.1:4002` is closed locally, so the CLI reports BLOCKED on this machine;
  that is the correct answer, not a defect. Real capture stays blocked until an
  operator brings the Gateway up.
- Mutation gates mutate `src/` in place, so they must run strictly one at a time.

## Next action
Confirm exact-head CI, resolve the two new review threads, report.
