# Changelog

## 0.20.0 — 2026-09-18

This release closes the first large desktop/application-boundary refactor and establishes a cleaner baseline for the next architecture phase.

### Highlights

- Improved the Windows run/install/verify path and CI coverage.
- Introduced a UI-independent runtime supervisor for startup/shutdown coordination.
- Moved market-data provider construction and lifecycle ownership behind `MarketDataService`.
- Added a dedicated Paper-trading application boundary with explicit order-service ownership.
- Split Paper order DTOs, the SQLite journal, and the IBKR callback transport into separate modules.
- Moved Qt worker adapters and reusable presentation widgets out of the main desktop window.
- Extracted settings persistence, credential management, the Settings panel, history jobs, official-universe refresh, manual market scan, and desktop backtest batch orchestration into focused components.
- Kept existing trading-safety rules intact while reducing the amount of broker and application logic owned directly by the desktop UI.

### Desktop architecture

The desktop window is no longer the sole owner of every subsystem. Major responsibilities now have dedicated boundaries for:

- background Qt workers;
- reusable presentation widgets and quote-table models;
- settings save/apply transactions;
- credential storage and resolution;
- history queue operations;
- universe refresh orchestration;
- manual market scanning;
- backtest batch execution.

This is an intermediate architecture milestone. The next phase will build the new Trading Core and Desktop UI foundations, then migrate market data, account state, strategy selection, risk, and execution into that structure.

### Market data

- Provider selection and stream construction are centralized behind `MarketDataService`.
- Supported market-data sources retain their existing routing, freshness, and fail-closed behavior.
- Live configuration changes are refused when they would invalidate an active stream.
- Settings persistence and runtime configuration updates now follow an explicit preflight → persist → apply sequence.

### Paper trading and broker integration

- Paper order-service lifecycle ownership is separated from the desktop UI.
- Candidate broker connections and the active Paper connection are managed independently.
- Paper order models and persistence are separated from the IBKR adapter.
- The IBKR callback transport forwards raw callback data without applying trading decisions.
- Reconciliation and finalization semantics remain explicit and fail closed.

### Strategy, research, and backtesting

- Strategy version governance remains independent from broker execution.
- Manual market scans and backtest batches now run through focused application services.
- Research and backtest persistence semantics remain unchanged.
- Auto-quant and Paper execution paths remain intentionally conservative pending the next Trading Core migration.

### Safety invariants

The release does **not** enable live trading.

The existing safety model remains in force:

- Paper environment only;
- local Paper Gateway connection only;
- Paper Gateway port remains fixed to the configured Paper endpoint;
- whole-share orders only;
- limit orders only;
- no short selling;
- no fractional-share orders;
- no margin borrowing;
- Live trading remains disabled;
- halted Paper sessions require explicit manual reconciliation before resuming.

### Verification

Release-candidate verification completed on Windows with Python 3.12 and Python 3.13.

- Full automated suite: **1134 passed**
- Publish-safety/doctor checks remain consistent with the Paper-only safety configuration.

### Privacy and release-note hygiene

Public release notes intentionally omit:

- account identifiers;
- broker client identifiers;
- API keys, secrets, tokens, or credential values;
- local usernames and machine-specific filesystem paths;
- portfolio balances, positions, or other account-linked private data.

Only architecture, behavior, public configuration constraints, and non-sensitive verification results are documented here.
