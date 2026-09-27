# Exec plan: Paper Autonomous Trading v1-B

## Purpose and success criteria
Complete the remaining v1-B autonomy supervisor implementation on the existing feature branch, preserving canonical Paper/Execution/Risk ownership, manual behavior, and fail-closed semantics. Pass the requested safety, mutation, architecture, full Python 3.14, and ancillary checks; create a PR against `main`; wait for Windows/Python 3.14 CI and stop for review without merging.

## Context map
- `src/us_quant/trading/domain/paper_autonomy_supervisor.py` — policy, facts, decisions and supervisor core.
- `src/us_quant/trading/composition/paper_autonomy.py` — current Qt-free composition.
- `src/us_quant/extended_hours.py` — canonical US equity session calendar/classifier.
- `src/us_quant/desktop_v2/orchestration/execution/` — canonical candidate preparation path.
- `src/us_quant/desktop_v2/orchestration/paper/` — canonical Paper lifecycle and launch preflight.
- `src/us_quant/desktop.py` — desktop composition and shutdown wiring.
- `tests/test_paper_autonomy_supervisor*.py` — behavior and architecture guards.
- `docs/TRADING_ARCHITECTURE_V2.md` — ownership and topology documentation.

## Milestones
- [x] M1: Inventory current production implementation and exact remaining gaps.
- [x] M2: Implement canonical schedule adapter and shared preparation seam.
- [x] M3: Implement authorization, facts/startup adapters, Qt host, composition wiring, async completion.
- [x] M4: Add focused behavior/architecture cases and update docs.
- [x] M5: Run requested mutation, historical, FAC, Python 3.14, doctor, compileall, and offscreen checks.
- [ ] M6: Commit and push the reviewed fixes to PR #60; resolve both P1 threads after confirming behavior.
- [ ] M7: Wait for new GitHub CI, update PR evidence, confirm OPEN/zero unresolved threads, and stop without merging.

## Decision log
- 2026-09-27: Continue from exact reviewed HEAD `03841c90f48804f15b5bd6154eadb1f7429a27d4`.
- 2026-09-27: Preserve pre-existing untracked workspace files; stage only task-owned source/docs/tests after inspecting status.
- 2026-09-27: Startup broker safety is established asynchronously through the existing read-only Paper channel probe; no default autonomy schedule is added.
- 2026-09-27: Review follow-up uses explicit `action_day` only for deterministic safety-control identity; canonical `trading_day` remains unknown on calendar failure.
- 2026-09-27: Local Python 3.14.5 suite passes (5,146 tests plus 14 subtests, zero skips); B1 55, A1 26, G2-B 35, E2 12, E3 41, E4 11, and FAC 42 mutations caught.

## Completion record
- Implementation and local verification complete; push, review-thread resolution, and remote CI remain.
