"""Durable operator-authored portfolio operating plan values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from us_quant.trading.domain.portfolio import PortfolioCapitalPolicy


@dataclass(frozen=True, slots=True)
class PortfolioOperatingPlan:
    plan_id: str
    revision: int
    selected_version_ids: tuple[str, ...]
    policy: PortfolioCapitalPolicy
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be nonblank")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(self.selected_version_ids, tuple) or not self.selected_version_ids:
            raise ValueError("selected_version_ids must be a non-empty tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.selected_version_ids):
            raise ValueError("selected version IDs must be nonblank")
        if len(set(self.selected_version_ids)) != len(self.selected_version_ids):
            raise ValueError("selected version IDs must be unique")
        if not isinstance(self.policy, PortfolioCapitalPolicy):
            raise TypeError("policy must be PortfolioCapitalPolicy")
        for name, value in (("created_at", self.created_at), ("updated_at", self.updated_at)):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot predate created_at")


@dataclass(frozen=True, slots=True)
class PortfolioPlanAuditEvent:
    plan_id: str
    revision: int
    changed_at: datetime
    selected_version_ids: tuple[str, ...]
    policy_limits: tuple[str, ...]
    allocation_limits: tuple[tuple[str, str, str, str, bool], ...]
    operator_reason: str

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or self.revision < 1:
            raise ValueError("audit identity is invalid")
        if self.changed_at.tzinfo is None or self.changed_at.utcoffset() is None:
            raise ValueError("changed_at must be timezone-aware")
        if len(self.policy_limits) != 8 or any(not isinstance(item, str) for item in self.policy_limits):
            raise ValueError("policy_limits must capture all eight portfolio hard limits")
        if not self.operator_reason.strip():
            raise ValueError("operator_reason is required")
        if re.search(r"(?i)password|credential|secret|api[_ -]?key|token|\bDU\d{6,}\b", self.operator_reason):
            raise ValueError("operator_reason cannot contain credentials or raw account identifiers")
