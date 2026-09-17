# US Quant Research — Runnability Audit

Date: 2026-09-17

## Goal

Move the repository from a feature-rich research workstation toward a system that can be reproduced, verified, packaged, and operated from a clean Windows machine without relying on hidden developer-machine state.

This audit intentionally prioritizes operational truth over adding more strategies.

## Executive summary

The project already has substantial research, market-data, paper-execution, risk, desktop, and test coverage. The main risk is not missing features; it is that installation, verification, packaging, and runtime ownership are less disciplined than the feature surface.

The immediate target should be:

1. one supported bootstrap path;
2. one deterministic source launcher;
3. one verification command that really executes the complete test suite;
4. clean-clone CI on supported Python versions;
5. reproducible packaging that does not depend on untracked local data;
6. later, split very large UI/broker modules behind stable service interfaces.

## P0 findings

### 1. The previous verification command did not represent the full test suite

`scripts/verify.ps1` used `python -m unittest discover`, while parts of the repository use pytest-style top-level `test_*` functions and pytest fixtures such as `monkeypatch`.

`unittest discover` imports those files but does not execute pytest-style test functions. A green local verification could therefore omit meaningful desktop and workflow tests.

**Action in this refactor:** use pytest as the canonical runner and declare it in the `test` optional dependency group.

### 2. There was no repository CI contract

The repository had many tests but no checked-in GitHub Actions workflow. This made success dependent on the current developer workstation.

**Action in this refactor:** add Windows CI for Python 3.12 and 3.13. CI installs the project from `pyproject.toml` and executes the repository-owned verification script.

### 3. Source startup depended on ambient system Python

The source launcher preferred `.venv313` but otherwise silently fell back to `python`. This can pick an unsupported Python version or an interpreter without PySide6 and create machine-specific behavior.

**Action in this refactor:** `.venv` becomes the canonical environment, `.venv313` remains a compatibility fallback, and source startup refuses unmanaged Python with a clear bootstrap instruction.

### 4. Clean-clone packaging was not deterministic

The packaging script assumed `.venv313` and unconditionally passed local `data/...` folders to PyInstaller even though those folders may not exist in a clean clone.

**Action in this refactor:** detect the managed environment, fail clearly when required packaging dependencies are missing, and only bundle optional data directories when they actually exist.

### 5. Release version metadata had drifted

`pyproject.toml` declared one package version while the Windows packaging script wrote a different hard-coded version into release metadata.

**Action in this refactor:** release metadata now reads the installed package version instead of duplicating it.

## P1 findings

### 6. Runtime boundaries are strong conceptually but spread across large modules

The repository has explicit Paper safety controls, reconciliation, stale-data handling, and execution gates. These are valuable. However, important behavior is concentrated in very large files, including the desktop shell and Paper broker adapter.

Recommended next architecture:

```text
UI (PySide6)
  -> application services
      -> market-data ports
      -> research/backtest services
      -> paper-execution service
      -> persistence ports
      -> risk/preflight service
```

The UI should render state and submit commands; it should not own broker lifecycle, reconciliation state machines, research orchestration, or data-source policy.

### 7. CLI defaults are source-tree oriented

The CLI defaults to paths such as `configs/paper.toml` relative to the current working directory. That is convenient in a repository checkout but is not a complete installed-package contract.

Recommended follow-up: define a single resource resolver for source, editable install, frozen executable, and wheel installation. Package immutable default resources explicitly.

### 8. IBKR API is an external installation boundary

The official IBKR Python API is installed separately from the normal project dependencies. That is reasonable, but it needs to be treated as a named capability rather than an implicit environment fact.

Recommended follow-up: extend `doctor` into capability-oriented preflight output such as `core`, `desktop`, `market-data`, `ibkr-readonly`, and `paper-ordering`, with required/optional checks and actionable remediation.

### 9. Generated research outputs live beside source

Large JSON research outputs are committed into the repository. This is useful for examples, but it can blur the difference between fixtures, reproducible baselines, cached output, and current evidence.

Recommended follow-up: keep small immutable fixtures in git; move generated run artifacts to runtime state or release artifacts and attach a manifest containing source hash, parameters, code revision, and generated-at time.

## P2 architecture refactor roadmap

### Phase A — runnable foundation

Status: started in this branch.

- canonical `.venv` bootstrap;
- pytest as canonical test runner;
- Windows Python 3.12/3.13 CI;
- deterministic launcher;
- desktop startup smoke test;
- clean-clone-aware packaging;
- package version is single-source.

### Phase B — application-service extraction

Extract from the desktop module in this order:

1. `RuntimeSupervisor` — task lifecycle, shutdown and liveness;
2. `MarketDataService` — provider selection, subscription lifecycle, freshness policy;
3. `PaperTradingService` — arm/start/stop/reconcile commands and immutable snapshots;
4. `ResearchService` — scan/backtest/targeted validation orchestration;
5. `SettingsService` — preferences and credential capability state.

The desktop should consume immutable view models/signals rather than call broker/research internals directly.

### Phase C — explicit ports and adapters

Define protocols for:

- `MarketDataProvider`;
- `BrokerReadOnlyGateway`;
- `PaperOrderGateway`;
- `HistoryProvider`;
- `Clock`;
- `StateStore`.

Provide deterministic fake adapters for tests. Integration adapters should be tested separately from strategy logic.

### Phase D — reproducible research contract

Every research/backtest result should identify:

- git revision;
- strategy version and parameter hash;
- data source and dataset hash;
- point-in-time cutoff;
- universe snapshot hash;
- cost/slippage model;
- execution assumptions;
- random seed when relevant.

This is more important than adding another score or indicator.

### Phase E — release engineering

- build the Windows client in CI from a tagged revision;
- archive test report and build manifest;
- verify the packaged executable with `US_QUANT_SELF_TEST=1` or an equivalent packaged smoke mode;
- generate checksums automatically;
- optionally add signing after the build is reproducible.

## Definition of “realistically runnable”

A revision should not be called runnable until a clean Windows runner can:

1. install Python 3.12 or 3.13;
2. install `.[desktop,test]` without undeclared developer-only dependencies;
3. run the complete pytest suite;
4. pass `us-quant doctor`;
5. construct and close the desktop in offscreen self-test mode;
6. compile all Python sources;
7. launch the source client from the documented managed environment.

IBKR connectivity and Paper order acceptance are a separate integration gate because they require local Gateway/TWS state and an actual Paper account.

## Non-goals of the first refactor

- no Live trading enablement;
- no weakening of Paper safety gates;
- no strategy-performance claims;
- no migration of broker logic without tests around the current behavior;
- no cosmetic rewrite merely to reduce line count.
