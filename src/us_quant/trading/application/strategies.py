"""Strategy application service: the governance rules, in one place.

This is where every strategy *decision* lives.  The repository stores, the
domain describes, and this service decides: whether a transition is legal,
whether the research gate has been passed, whether a clone is allowed, which
versions the default catalogue should contain, and which legacy versions must
be retired.

It knows nothing about storage.  There is no ``sqlite3``, no ``connect_sqlite``
and no concrete adapter import here -- only the ``StrategyRepositoryPort``
protocol and the domain.  That is what lets the composition root hand it a
real store in production and an in-memory fake in tests without this module
changing at all.

Two invariants are worth restating because they are the ones an operator
depends on:

* **nobody self-attests the gate.**  ``register`` refuses ``gate_passed=True``
  outright.  There is no independent gate evaluator yet, so a caller that
  could set it would be able to move its own strategy into ``PAPER_SHADOW``.
* **a retired version is not a version.**  ``LEGACY_INVALIDATED`` is
  permanently terminal and cannot be cloned.  A version whose result was
  falsified cannot be resurrected by making a copy of it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import uuid4

from us_quant.trading.application.strategy_defaults import (
    DEFAULT_STRATEGY_SEEDS,
)
from us_quant.trading.domain.common import ZERO
from us_quant.trading.domain.strategy import (
    ALLOWED_TRANSITIONS,
    MAX_RISK_BUDGET_PCT,
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    parameter_hash_for,
)
from us_quant.trading.domain.strategy_parameters import (
    validate_strategy_parameters,
)
from us_quant.trading.ports.strategy_repository import (
    StrategyAuditEvent,
    StrategyRepositoryNotFound,
    StrategyRepositoryPort,
)

#: Parameters that hard-code the symbol a run happens to use.  Symbols belong
#: to a research run, not to immutable strategy parameters.
EMBEDDED_SYMBOL_KEYS = frozenset({"signal_symbol", "execution_symbol"})

DEFAULT_GATE_REASON = "尚未通过研究晋级门"


class StrategyApplicationError(RuntimeError):
    """Base class for every refused strategy governance operation."""


class StrategyNotFoundError(StrategyApplicationError):
    """The requested version is not in the catalogue."""


class StrategyApplication:
    """Versioned strategy governance with no order execution surface."""

    def __init__(self, repository: StrategyRepositoryPort) -> None:
        self._repository = repository

    # -- queries --------------------------------------------------------

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        """Every governed version, retired ones included."""

        return self._repository.list_versions()

    def get_version(self, version_id: str) -> StrategyVersion:
        """One version, or ``StrategyNotFoundError``."""

        try:
            return self._repository.get_version(version_id)
        except StrategyRepositoryNotFound as error:
            raise StrategyNotFoundError(version_id) from error

    # -- commands -------------------------------------------------------

    def register(
        self,
        *,
        strategy_id: str,
        name: str,
        description: str,
        semver: str,
        parameters: Mapping[str, Any],
        universe_hash: str,
        code_hash: str,
        risk_budget_pct: Decimal | float | str,
        status: StrategyStatus | str = StrategyStatus.RESEARCH,
        mode: StrategyMode | str = StrategyMode.RESEARCH,
        gate_passed: bool = False,
        gate_reason: str = DEFAULT_GATE_REASON,
    ) -> StrategyVersion:
        """Create one immutable version.

        A caller may not declare its own gate pass; see the module docstring.
        """

        resolved_status = _coerce_status(status)
        resolved_mode = _coerce_mode(mode)
        if gate_passed:
            raise StrategyApplicationError(
                "调用方不能自行声明晋级门通过；当前独立 gate evaluator 尚未启用"
            )
        budget = _risk_budget(risk_budget_pct)
        # ``validate_strategy_parameters`` normalises in place, and the
        # normalised form is what gets hashed: ``"50"`` becomes ``"50.0"``.
        # Hashing the raw input would change every version's identity.
        validated = validate_strategy_parameters(strategy_id, parameters)
        now = _now()
        version = StrategyVersion(
            definition=StrategyDefinition(
                strategy_id=strategy_id,
                name=name,
                description=description,
            ),
            identity=StrategyIdentity(
                strategy_id=strategy_id,
                version_id=str(uuid4()),
                parameter_hash=parameter_hash_for(validated),
            ),
            semver=semver,
            status=resolved_status,
            mode=resolved_mode,
            parameters=validated,
            universe_hash=universe_hash,
            code_hash=code_hash,
            risk_budget_pct=budget,
            gate_passed=False,
            gate_reason=gate_reason,
            created_at=now,
            updated_at=now,
        )
        self._repository.insert_version(
            version,
            audit=StrategyAuditEvent(
                strategy_id=strategy_id,
                version_id=version.version_id,
                event="registered",
                detail=f"{semver} / {resolved_status}",
                occurred_at=now,
            ),
        )
        return version

    def clone_version(
        self,
        version_id: str,
        *,
        semver: str,
        parameters: Mapping[str, Any],
    ) -> StrategyVersion:
        """Fork a version under a new semver, back at ``RESEARCH``.

        The clone starts unproven on purpose: changed parameters invalidate the
        evidence the source version accumulated.
        """

        source = self.get_version(version_id)
        if source.status is StrategyStatus.LEGACY_INVALIDATED:
            raise StrategyApplicationError(
                "已失效旧结果只能审计，不能克隆为新策略"
            )
        return self.register(
            strategy_id=source.strategy_id,
            name=source.name,
            description=source.description,
            semver=semver,
            parameters=parameters,
            universe_hash=source.universe_hash,
            code_hash=source.code_hash,
            risk_budget_pct=source.risk_budget_pct,
            status=StrategyStatus.RESEARCH,
            mode=StrategyMode.RESEARCH,
            gate_passed=False,
            gate_reason="参数变化后必须重新研究验证",
        )

    def transition(
        self,
        version_id: str,
        target_status: StrategyStatus | str,
        *,
        reason: str,
    ) -> StrategyVersion:
        """Move a version through the governance state machine."""

        current = self.get_version(version_id)
        target = _coerce_status(target_status)
        if target not in ALLOWED_TRANSITIONS[current.status]:
            raise StrategyApplicationError(
                f"{current.status} cannot transition to {target}"
            )
        if (
            target is StrategyStatus.PAPER_SHADOW
            and not current.gate_passed
        ):
            raise StrategyApplicationError(
                f"research gate blocked: {current.gate_reason}"
            )
        mode = (
            StrategyMode.PAPER_SHADOW
            if target
            in {StrategyStatus.PAPER_SHADOW, StrategyStatus.PAUSED}
            else current.mode
        )
        now = _now()
        self._repository.update_deployment(
            version_id=version_id,
            status=target,
            mode=mode,
            updated_at=now,
            audit=StrategyAuditEvent(
                strategy_id=current.strategy_id,
                version_id=version_id,
                event="transition",
                detail=f"{current.status}->{target}: {reason}",
                occurred_at=now,
            ),
        )
        return self.get_version(version_id)

    # -- catalogue bootstrap --------------------------------------------

    def bootstrap(self) -> int:
        """Ensure the built-in catalogue exists.  Returns how many were added.

        Idempotent: the default set is keyed on ``(strategy_id, semver)``, so
        starting twice creates nothing the second time.  Legacy retirement runs
        first, exactly as it did in the retired registry.
        """

        self.retire_embedded_symbol_versions()
        existing = {
            (version.strategy_id, version.semver)
            for version in self._repository.list_versions()
        }
        created = 0
        for seed in DEFAULT_STRATEGY_SEEDS:
            key = (seed.strategy_id, seed.semver)
            if key in existing:
                continue
            self.register(
                strategy_id=seed.strategy_id,
                name=seed.name,
                description=seed.description,
                semver=seed.semver,
                parameters=seed.parameters,
                universe_hash=seed.universe_hash,
                code_hash=seed.code_hash,
                risk_budget_pct=seed.risk_budget_pct,
                status=seed.status,
                mode=seed.mode,
                gate_passed=seed.gate_passed,
                gate_reason=seed.gate_reason,
            )
            existing.add(key)
            created += 1
        return created

    def retire_embedded_symbol_versions(self) -> int:
        """Retire versions whose parameters hard-code a run-time symbol.

        Each retirement is its own recorded deployment update.  The retired
        registry performed the loop inside one transaction; the port has no
        batch operation, so the difference is that a failure part-way through
        leaves the earlier versions retired and recorded rather than rolling
        the whole sweep back.  Every retirement is individually visible in
        ``strategy_audit``, which is what the operator needs either way.
        """

        candidates = tuple(
            version
            for version in self._repository.list_versions()
            if version.status is not StrategyStatus.LEGACY_INVALIDATED
            and contains_embedded_symbol(version.parameters)
        )
        if not candidates:
            return 0
        now = _now()
        for version in candidates:
            self._repository.update_deployment(
                version_id=version.version_id,
                status=StrategyStatus.LEGACY_INVALIDATED,
                mode=StrategyMode.RESEARCH,
                updated_at=now,
                audit=StrategyAuditEvent(
                    strategy_id=version.strategy_id,
                    version_id=version.version_id,
                    event="legacy_invalidated",
                    detail=(
                        "运行标的曾固化在策略参数中；"
                        "现统一改为每次运行时输入"
                    ),
                    occurred_at=now,
                ),
            )
        return len(candidates)


def contains_embedded_symbol(value: Any) -> bool:
    """Whether ``value`` nests a non-empty ``signal_symbol``/``execution_symbol``.

    Deliberately structural rather than ticker-aware: the migration must not
    privilege or special-case any symbol or historical strategy id.
    """

    if isinstance(value, dict):
        for key, nested in value.items():
            if key in EMBEDDED_SYMBOL_KEYS:
                return isinstance(nested, str) and bool(nested.strip())
            if contains_embedded_symbol(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_embedded_symbol(item) for item in value)
    return False


def _coerce_status(value: StrategyStatus | str) -> StrategyStatus:
    if isinstance(value, StrategyStatus):
        return value
    try:
        return StrategyStatus(value)
    except ValueError as error:
        raise StrategyApplicationError(
            f"unsupported strategy status: {value!r}"
        ) from error


def _coerce_mode(value: StrategyMode | str) -> StrategyMode:
    if isinstance(value, StrategyMode):
        return value
    try:
        return StrategyMode(value)
    except ValueError as error:
        raise StrategyApplicationError(
            "only research and paper_shadow are allowed"
        ) from error


def _risk_budget(value: Decimal | float | str) -> Decimal:
    if isinstance(value, Decimal):
        budget = value
    else:
        try:
            budget = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError(
                "strategy risk budget must be in (0, 10%]"
            ) from error
    if not ZERO < budget <= MAX_RISK_BUDGET_PCT:
        raise ValueError("strategy risk budget must be in (0, 10%]")
    return budget


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_GATE_REASON",
    "EMBEDDED_SYMBOL_KEYS",
    "StrategyApplication",
    "StrategyApplicationError",
    "StrategyNotFoundError",
    "contains_embedded_symbol",
]
