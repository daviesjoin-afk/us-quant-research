"""Immutable presentation models for the targeted validation workspace.

Nothing in this module imports Qt, a runtime, a store, a broker or a research
executor.  These are the finished facts a page draws and the page's own draft
inputs; the window and the presenters remain the only producers of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class TargetedWorkspace(IntEnum):
    """The five top-level targeted workspaces, as semantic keys.

    Presentation vocabulary only: these select which panel the page shows and
    carry no workflow meaning.  A caller naming ``EVIDENCE`` is asking to *look*
    at the evidence workspace, never to run an evaluation -- the run stays an
    explicit operator intent through the page's own signals.
    """

    STRATEGY = 0
    POSITIONS = 1
    FILLS = 2
    EVIDENCE = 3
    PREFLIGHT = 4


class TargetedEvidenceWorkspace(IntEnum):
    """The seven evidence sections inside the evidence workspace."""

    REPLAY = 0
    ROBUSTNESS = 1
    WALK_FORWARD = 2
    OVERFIT = 3
    DATA_QUALITY = 4
    EXECUTION_STRESS = 5
    REVIEW = 6


class TargetedRobustnessDetail(IntEnum):
    """The two detail views inside the robustness evidence section."""

    HISTORY = 0
    SCENARIOS = 1


class TargetedReviewDetail(IntEnum):
    """The two detail views inside the review evidence section."""

    HISTORY = 0
    GATES = 1


class TargetedRowTone(str, Enum):
    """Presentation-only colour intent for one table row."""

    NEUTRAL = "neutral"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TargetedStrategyOption:
    version_id: str
    label: str


@dataclass(frozen=True, slots=True)
class TargetedControlView:
    strategy_enabled: bool
    target_enabled: bool
    subscribe_enabled: bool
    shadow_start_enabled: bool
    shadow_stop_enabled: bool
    replay_enabled: bool
    robustness_enabled: bool


@dataclass(frozen=True, slots=True)
class TargetedMetricView:
    value: str
    note: str


@dataclass(frozen=True, slots=True)
class TargetedTableRow:
    key: str | None
    values: tuple[str, ...]
    tone: TargetedRowTone = TargetedRowTone.NEUTRAL
    tooltip: str | None = None


class TargetedPositionRow(TargetedTableRow):
    pass


class TargetedFillRow(TargetedTableRow):
    pass


class TargetPreflightRow(TargetedTableRow):
    pass


class ReplayRow(TargetedTableRow):
    pass


class RobustnessRunRow(TargetedTableRow):
    pass


class RobustnessScenarioRow(TargetedTableRow):
    pass


class WalkForwardRow(TargetedTableRow):
    pass


class OverfitRow(TargetedTableRow):
    pass


class DataQualityRow(TargetedTableRow):
    pass


class ExecutionStressRow(TargetedTableRow):
    pass


class ReviewHistoryRow(TargetedTableRow):
    pass


class ReviewGateRow(TargetedTableRow):
    pass


@dataclass(frozen=True, slots=True)
class TargetPreflightView:
    summary: str
    rows: tuple[TargetPreflightRow, ...]


@dataclass(frozen=True, slots=True)
class TargetedSessionView:
    status: TargetedMetricView
    equity: TargetedMetricView
    realized: TargetedMetricView
    unrealized: TargetedMetricView
    trades: TargetedMetricView
    explanation: str
    positions: tuple[TargetedPositionRow, ...]
    fills: tuple[TargetedFillRow, ...]
    target_status: str
    minute_status: str
    preflight: TargetPreflightView
    controls: TargetedControlView


@dataclass(frozen=True, slots=True)
class TargetedEvidenceView:
    replay_rows: tuple[ReplayRow, ...]
    robustness_summary: str
    robustness_rows: tuple[RobustnessRunRow, ...]
    robustness_scenario_rows: tuple[RobustnessScenarioRow, ...]
    walk_forward_summary: str
    walk_forward_rows: tuple[WalkForwardRow, ...]
    overfit_summary: str
    overfit_rows: tuple[OverfitRow, ...]
    quality_summary: str
    quality_rows: tuple[DataQualityRow, ...]
    stress_summary: str
    stress_rows: tuple[ExecutionStressRow, ...]
    review_summary: str
    review_history_rows: tuple[ReviewHistoryRow, ...]
    review_gate_rows: tuple[ReviewGateRow, ...]
    selected_robustness_run_id: str | None
    selected_review_run_id: str | None


__all__ = [
    "DataQualityRow",
    "ExecutionStressRow",
    "OverfitRow",
    "ReplayRow",
    "ReviewGateRow",
    "ReviewHistoryRow",
    "RobustnessRunRow",
    "RobustnessScenarioRow",
    "TargetPreflightRow",
    "TargetPreflightView",
    "TargetedControlView",
    "TargetedEvidenceView",
    "TargetedEvidenceWorkspace",
    "TargetedFillRow",
    "TargetedMetricView",
    "TargetedPositionRow",
    "TargetedReviewDetail",
    "TargetedRobustnessDetail",
    "TargetedRowTone",
    "TargetedSessionView",
    "TargetedStrategyOption",
    "TargetedTableRow",
    "TargetedWorkspace",
    "WalkForwardRow",
]
