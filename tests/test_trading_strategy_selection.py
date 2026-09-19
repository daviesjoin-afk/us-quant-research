"""Strategy selection tests.

Selection is the answer to "which version does this purpose run?".  It used to
be ``QComboBox.currentData()`` plus a filter written inline in the window, which
meant the answer depended on which tab was on screen and was unavailable to
anything that was not that widget.

These tests pin the properties that make the service an improvement rather than
a relocation:

* eligibility is declared policy, so a wrong family or a wrong status is
  refused with a reason;
* the default is the *newest* eligible version, not whichever row the store
  happened to return first;
* a version that stops being eligible stops being selectable, and the service
  moves on rather than continuing to hand back something un-runnable.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.backtest_workspace import STRATEGY_SPECS
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_selection import (
    AUTO_ROTATION_STRATEGY_IDS,
    BACKTEST_STRATEGY_IDS,
    RUNNABLE_STATUSES,
    SELECTION_POLICIES,
    StrategyNotFoundForSelection,
    StrategySelectionError,
    StrategySelectionPolicy,
    StrategySelectionPurpose,
    StrategySelectionService,
    TARGETED_SHADOW_STRATEGY_IDS,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.trading.ports.strategy_repository import (
    StrategyRepositoryNotFound,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _InMemoryRepository:
    def __init__(self, versions: tuple[StrategyVersion, ...] = ()) -> None:
        self.versions = list(versions)

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        return tuple(self.versions)

    def get_version(self, version_id: str) -> StrategyVersion:
        for version in self.versions:
            if version.version_id == version_id:
                return version
        raise StrategyRepositoryNotFound(version_id)

    def insert_version(self, version, *, audit) -> None:
        self.versions.append(version)

    def update_deployment(
        self, *, version_id, status, mode, updated_at, audit
    ) -> None:
        for index, version in enumerate(self.versions):
            if version.version_id == version_id:
                self.versions[index] = replace(
                    version,
                    status=status,
                    mode=mode,
                    updated_at=updated_at,
                )
                return
        raise StrategyRepositoryNotFound(version_id)


def _version(
    strategy_id: str,
    semver: str,
    *,
    status: StrategyStatus = StrategyStatus.RESEARCH,
    created_at: datetime = BASE,
) -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name=f"{strategy_id} {semver}",
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=f"{strategy_id}:{semver}",
            parameter_hash=f"hash-{semver}",
        ),
        semver=semver,
        status=status,
        mode=(
            StrategyMode.PAPER_SHADOW
            if status is StrategyStatus.PAPER_SHADOW
            else StrategyMode.RESEARCH
        ),
        parameters={"whole_shares": True},
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.1"),
        gate_passed=status is StrategyStatus.PAPER_SHADOW,
        gate_reason="test",
        created_at=created_at,
        updated_at=created_at,
    )


def _service(
    *versions: StrategyVersion,
) -> tuple[StrategySelectionService, _InMemoryRepository]:
    repository = _InMemoryRepository(tuple(versions))
    return (
        StrategySelectionService(StrategyApplication(repository)),
        repository,
    )


# -- policy ---------------------------------------------------------------


def test_the_backtest_policy_mirrors_the_backtest_workspace() -> None:
    """The declared ids must equal the executable factory's ids.

    The set is spelled out in the service rather than imported, so the
    application layer keeps no dependency on the research workspace.  This is
    the assertion that stops the mirror from drifting.
    """

    assert BACKTEST_STRATEGY_IDS == {
        spec.strategy_id for spec in STRATEGY_SPECS
    }


def test_the_three_purposes_have_the_expected_policies() -> None:
    assert set(SELECTION_POLICIES) == {
        StrategySelectionPurpose.BACKTEST,
        StrategySelectionPurpose.TARGETED_SHADOW,
        StrategySelectionPurpose.AUTO_ROTATION,
    }
    assert SELECTION_POLICIES[
        StrategySelectionPurpose.BACKTEST
    ] == StrategySelectionPolicy(
        strategy_ids=BACKTEST_STRATEGY_IDS,
        statuses=frozenset({StrategyStatus.RESEARCH}),
    )
    assert SELECTION_POLICIES[
        StrategySelectionPurpose.TARGETED_SHADOW
    ].strategy_ids == TARGETED_SHADOW_STRATEGY_IDS
    assert SELECTION_POLICIES[
        StrategySelectionPurpose.AUTO_ROTATION
    ].strategy_ids == AUTO_ROTATION_STRATEGY_IDS
    assert RUNNABLE_STATUSES == frozenset(
        {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
    )


def test_sector_momentum_is_not_backtestable() -> None:
    """It is governed, but the backtest factory cannot execute it."""

    assert "sector-momentum" not in BACKTEST_STRATEGY_IDS


def test_an_unknown_purpose_is_refused() -> None:
    with pytest.raises(StrategySelectionError):
        StrategySelectionService.policy("nope")  # type: ignore[arg-type]


# -- options --------------------------------------------------------------


def test_options_are_newest_first() -> None:
    newer = _version("buy-hold", "2", created_at=BASE + timedelta(days=1))
    older = _version("buy-hold", "1", created_at=BASE)
    service, _ = _service(older, newer)

    assert [
        version.semver
        for version in service.options(StrategySelectionPurpose.BACKTEST)
    ] == ["2", "1"]


def test_options_only_include_the_purposes_family() -> None:
    service, _ = _service(
        _version("buy-hold", "1"),
        _version("intraday-auto-rotation", "1"),
        _version("intraday-targeted-t", "1"),
    )
    assert {
        version.strategy_id
        for version in service.options(StrategySelectionPurpose.BACKTEST)
    } == {"buy-hold"}


@pytest.mark.parametrize(
    "status",
    [
        StrategyStatus.STOPPED,
        StrategyStatus.LEGACY_INVALIDATED,
        StrategyStatus.PAUSED,
    ],
)
def test_unrunnable_statuses_are_not_options(status) -> None:
    service, _ = _service(_version("intraday-auto-rotation", "1", status=status))
    assert service.options(StrategySelectionPurpose.AUTO_ROTATION) == ()


def test_backtest_accepts_only_research() -> None:
    """A shadow version must not silently become a backtest subject."""

    service, _ = _service(
        _version("buy-hold", "1"),
        _version("buy-hold", "2", status=StrategyStatus.PAPER_SHADOW),
    )
    assert [
        version.semver
        for version in service.options(StrategySelectionPurpose.BACKTEST)
    ] == ["1"]


# -- defaults -------------------------------------------------------------


def test_the_default_is_the_newest_eligible_version() -> None:
    service, _ = _service(
        _version("intraday-auto-rotation", "1.0.0", created_at=BASE),
        _version(
            "intraday-auto-rotation",
            "1.2.0",
            created_at=BASE + timedelta(days=2),
        ),
        _version(
            "intraday-auto-rotation",
            "1.1.0",
            created_at=BASE + timedelta(days=1),
        ),
    )
    chosen = service.restore_or_default(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert chosen is not None
    assert chosen.semver == "1.2.0"


def test_the_default_does_not_depend_on_repository_order() -> None:
    """The store's row order is not a selection rule.

    The fake returns the oldest first; a service that took the first eligible
    row without sorting would pick 1.0.0 here.
    """

    service, _ = _service(
        _version("intraday-auto-rotation", "1.0.0", created_at=BASE),
        _version(
            "intraday-auto-rotation",
            "1.1.0",
            created_at=BASE + timedelta(days=1),
        ),
    )
    chosen = service.restore_or_default(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert chosen is not None and chosen.semver == "1.1.0"


def test_ties_break_on_version_id_for_stability() -> None:
    """Two versions created in the same instant still order deterministically."""

    first = _version("buy-hold", "1", created_at=BASE)
    second = _version("buy-hold", "2", created_at=BASE)
    service, _ = _service(first, second)

    chosen = service.restore_or_default(StrategySelectionPurpose.BACKTEST)
    assert chosen is not None
    assert chosen.version_id == max(
        (first, second),
        key=lambda version: (version.created_at, version.version_id),
    ).version_id


def test_restore_or_default_returns_none_when_nothing_is_eligible() -> None:
    service, _ = _service(_version("buy-hold", "1", status=StrategyStatus.STOPPED))
    assert (
        service.restore_or_default(StrategySelectionPurpose.BACKTEST) is None
    )


# -- explicit selection ---------------------------------------------------


def test_a_selection_is_remembered() -> None:
    version = _version("buy-hold", "1")
    service, _ = _service(version)

    service.select(StrategySelectionPurpose.BACKTEST, version.version_id)
    assert (
        service.selected(StrategySelectionPurpose.BACKTEST).version_id
        == version.version_id
    )


def test_a_fresh_service_has_no_selection() -> None:
    """Selection state is in memory and starts empty."""

    service, _ = _service(_version("buy-hold", "1"))
    assert service.selected(StrategySelectionPurpose.BACKTEST) is None


def test_a_wrong_strategy_family_is_refused() -> None:
    """An auto-rotation slot must not run a benchmark."""

    benchmark = _version("buy-hold", "1")
    service, _ = _service(benchmark)
    with pytest.raises(StrategySelectionError, match="does not accept"):
        service.select(
            StrategySelectionPurpose.AUTO_ROTATION, benchmark.version_id
        )


def test_a_rotation_version_is_refused_for_targeted_shadow() -> None:
    rotation = _version("intraday-auto-rotation", "1")
    service, _ = _service(rotation)
    with pytest.raises(StrategySelectionError, match="does not accept"):
        service.select(
            StrategySelectionPurpose.TARGETED_SHADOW, rotation.version_id
        )


@pytest.mark.parametrize(
    "status",
    [
        StrategyStatus.STOPPED,
        StrategyStatus.LEGACY_INVALIDATED,
        StrategyStatus.PAUSED,
    ],
)
def test_an_ineligible_status_is_refused(status) -> None:
    version = _version("intraday-auto-rotation", "1", status=status)
    service, _ = _service(version)
    with pytest.raises(StrategySelectionError, match="does not accept status"):
        service.select(
            StrategySelectionPurpose.AUTO_ROTATION, version.version_id
        )


def test_an_unknown_version_is_refused() -> None:
    service, _ = _service()
    with pytest.raises(StrategyNotFoundForSelection):
        service.select(
            StrategySelectionPurpose.AUTO_ROTATION, "absent"
        )


def test_a_paper_shadow_version_is_selectable_for_rotation() -> None:
    version = _version(
        "intraday-auto-rotation", "1", status=StrategyStatus.PAPER_SHADOW
    )
    service, _ = _service(version)
    assert (
        service.select(
            StrategySelectionPurpose.AUTO_ROTATION, version.version_id
        ).status
        is StrategyStatus.PAPER_SHADOW
    )


# -- a version that stops being eligible ----------------------------------


def test_a_stopped_version_loses_its_selection() -> None:
    version = _version("intraday-auto-rotation", "1")
    service, repository = _service(version)

    service.select(StrategySelectionPurpose.AUTO_ROTATION, version.version_id)
    repository.versions[0] = replace(
        version, status=StrategyStatus.STOPPED
    )

    assert service.selected(StrategySelectionPurpose.AUTO_ROTATION) is None
    assert (
        service.restore_or_default(StrategySelectionPurpose.AUTO_ROTATION)
        is None
    )


def test_a_stopped_version_is_replaced_by_the_newest_survivor() -> None:
    older = _version("intraday-auto-rotation", "1", created_at=BASE)
    newer = _version(
        "intraday-auto-rotation",
        "2",
        created_at=BASE + timedelta(days=1),
    )
    service, repository = _service(older, newer)

    service.select(
        StrategySelectionPurpose.AUTO_ROTATION, newer.version_id
    )
    repository.versions[1] = replace(
        newer, status=StrategyStatus.STOPPED
    )

    replacement = service.restore_or_default(
        StrategySelectionPurpose.AUTO_ROTATION
    )
    assert replacement is not None
    assert replacement.version_id == older.version_id


def test_a_deleted_version_loses_its_selection() -> None:
    version = _version("intraday-auto-rotation", "1")
    service, repository = _service(version)
    service.select(StrategySelectionPurpose.AUTO_ROTATION, version.version_id)
    repository.versions.clear()
    assert service.selected(StrategySelectionPurpose.AUTO_ROTATION) is None


def test_restore_or_default_keeps_an_eligible_selection() -> None:
    first = _version("buy-hold", "1", created_at=BASE)
    second = _version(
        "buy-hold", "2", created_at=BASE + timedelta(days=5)
    )
    service, _ = _service(first, second)
    service.select(StrategySelectionPurpose.BACKTEST, first.version_id)

    restored = service.restore_or_default(StrategySelectionPurpose.BACKTEST)
    assert restored is not None and restored.version_id == first.version_id
