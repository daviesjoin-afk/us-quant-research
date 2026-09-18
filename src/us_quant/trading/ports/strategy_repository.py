"""Strategy repository port: versioned strategy governance.

The fields on ``StrategyRecordView`` are exactly the ones the current
``StrategyRegistry.StrategyRecord`` actually carries -- no invented columns
and no speculative metadata.  The view is a ``Protocol`` rather than the
concrete dataclass so that the registry's row type does not leak into the
runtime, and so a later ``trading/adapters/sqlite/strategy_repository.py``
can satisfy it without the runtime importing ``strategy_registry`` or
``sqlite3``.
"""

from __future__ import annotations

from typing import Any, Protocol


class StrategyRecordView(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def version_id(self) -> str: ...

    @property
    def semver(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

    @property
    def parameter_hash(self) -> str: ...

    @property
    def universe_hash(self) -> str: ...

    @property
    def code_hash(self) -> str: ...

    @property
    def risk_budget_pct(self) -> float: ...

    @property
    def gate_passed(self) -> bool: ...

    @property
    def gate_reason(self) -> str: ...

    @property
    def created_at(self) -> str: ...

    @property
    def updated_at(self) -> str: ...


class StrategyRepositoryPort(Protocol):
    def list_strategies(self) -> tuple[StrategyRecordView, ...]: ...

    def get_strategy(self, version_id: str) -> StrategyRecordView: ...
