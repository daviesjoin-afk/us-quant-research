"""The targeted research procedures and their artifact boundary, without Qt.

``MainWindow`` used to hold the whole targeted evidence procedure inline: two
task closures that read the minute store, chose a provider, grouped sessions,
ran seven research executors in a fixed order, wrote seven artifact families and
re-read all seven on startup.  This module owns that, the same way
``DesktopCrossSectionService`` and ``DesktopMarketScanService`` own theirs.

What it is: **one real, already-existing research procedure**.  The replay
procedure and the robustness suite are not two capabilities that happen to share
a file -- the suite *contains* the replay (its baseline scenario is a per-session
replay) and the review gate reads the other five results.  So the cohesion is
the domain's, not an accident of extraction, and the two entry points below are
the two things an operator can actually ask for.

Three rules are frozen and asserted by tests, because each is a research-safety
property rather than a style choice:

* **the provider selection is unchanged, verbatim-equivalent.**  Rows are
  grouped by ``provider``; the winner is the group with the most rows, ties
  broken by lexical ``provider`` order.  Data quality then reads the *same*
  provider's raw (including unusable) rows.  Mixing providers would silently
  splice two feeds into one purported session set;
* **the replay session is the latest regular session of the selected provider**,
  by ``group_regular_sessions`` then ``sessions[-1]``.  Not a calendar-date
  algorithm, not a row-count threshold, not a fallback;
* **the pipeline order and every save timing are unchanged**: robustness, save;
  overfit, save; data quality, save; walk-forward when ``usable_sessions >= 20``,
  save; execution stress, save; review, save.  This round is not a research
  algorithm optimisation, so nothing runs concurrently and nothing is skipped.

The two timing rules that *are* this module's contract are opposites, and both
are inherited rather than invented:

* the run inputs -- symbol, strategy identity, scenario capital -- arrive
  **frozen**, from the capability, which froze them on the UI thread.  This
  boundary never reads a strategy selection or a capital widget, and it never
  re-derives one from the other: the replay and the robustness suite run on the
  same ``TargetedEvidenceRunInputs`` value;
* the minute evidence is read **when the task executes**, because the operator
  records market data between clicking and the worker actually starting, and the
  run should use what has landed.  That is why the store is a constructor
  dependency that both entry points read, rather than a value captured here.

What is deliberately *not* here: Qt, dialogs, the page, selection state, the
research route, the runtime event store, ``MainWindow``, the Shadow engine, the
market stream, the broker account and the target preflight.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRunInputs,
    TargetedEvidenceSnapshot,
    TargetedRobustnessBundle,
)
from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.targeted_data_quality import (
    load_targeted_data_quality,
    run_targeted_data_quality,
    save_targeted_data_quality,
)
from us_quant.targeted_execution_stress import (
    load_targeted_execution_stress,
    run_targeted_execution_stress,
    save_targeted_execution_stress,
)
from us_quant.targeted_overfit import (
    load_targeted_overfits,
    run_targeted_overfit_diagnostics,
    save_targeted_overfit,
)
from us_quant.targeted_replay import (
    TargetedReplayResult,
    load_targeted_replays,
    run_targeted_replay,
    save_targeted_replay,
)
from us_quant.targeted_review import (
    load_targeted_reviews,
    run_targeted_review,
    save_targeted_review,
)
from us_quant.targeted_robustness import (
    TargetedRobustnessResult,
    group_regular_sessions,
    load_targeted_robustness,
    run_targeted_robustness,
    save_targeted_robustness,
)
from us_quant.targeted_validation import (
    load_targeted_walk_forwards,
    run_targeted_walk_forward,
    save_targeted_walk_forward,
)

#: The one artifact directory per result family, under the research results root.
#: Spelled once here so the seven writes and the seven reads cannot drift apart.
REPLAY_DIRECTORY = "targeted_replays"
ROBUSTNESS_DIRECTORY = "targeted_robustness"
WALK_FORWARD_DIRECTORY = "targeted_walk_forward"
OVERFIT_DIRECTORY = "targeted_overfit"
DATA_QUALITY_DIRECTORY = "targeted_data_quality"
EXECUTION_STRESS_DIRECTORY = "targeted_execution_stress"
REVIEW_DIRECTORY = "targeted_review"

#: The minimum number of usable sessions before the time-isolation validation can
#: run at all: below it there are not enough independent sessions to hold a test
#: set out, and the suite reports no walk-forward rather than a meaningless one.
MINIMUM_WALK_FORWARD_SESSIONS = 20


def _choose_provider(
    records: tuple[MinuteQuoteRecord, ...],
) -> tuple[str, tuple[MinuteQuoteRecord, ...]] | None:
    """Choose one provider: most rows wins, ties broken by lexical provider name.

    All-or-nothing by construction -- one provider's rows come back, never a
    union -- because a replay that spliced two feeds would compute a return for
    a market that never existed.  ``None`` means there was no usable row at all.

    The rule is unchanged from the inline handler, tie-break included: the winner
    is the group with the most rows, and among equals the lexically *greatest*
    provider name -- that is what ``max`` over ``(row_count, provider)`` selects.
    It is deliberately not re-expressed as a preference list or as dict-insertion
    order, either of which would change which feed the research runs on.
    """

    groups: dict[str, list[MinuteQuoteRecord]] = {}
    for record in records:
        groups.setdefault(record.provider, []).append(record)
    if not groups:
        return None
    provider, selected = max(
        groups.items(),
        key=lambda item: (len(item[1]), item[0]),
    )
    return provider, tuple(selected)


class DesktopTargetedEvidenceService:
    """Runs the targeted replay and robustness procedures; reads their artifacts.

    The constructor performs no I/O and reads no store: it remembers the minute
    store and the artifact root.  Both are things a run needs to *reach*, not
    values that could go stale, so neither needs a provider indirection -- and
    the minute store in particular must not be snapshotted, because the evidence
    it holds changes between the click and the worker's first line.
    """

    def __init__(
        self,
        *,
        minute_store: MinuteQuoteStore,
        results_root: Path,
    ) -> None:
        self._store = minute_store
        self._root = Path(results_root)

    def _directory(self, name: str) -> Path:
        return self._root / name

    # -- replay ------------------------------------------------------------

    def run_replay(
        self,
        inputs: TargetedEvidenceRunInputs,
        *,
        progress: Callable[[str], None],
    ) -> TargetedReplayResult:
        """Replay one session and save the artifact.

        The order is read -> choose -> group -> take the latest -> replay ->
        save, unchanged from the inline handler.  A failing read or a failing
        run must not write anything and must not report success: both exceptions
        propagate, which keeps the old ``TaskThread.failed`` behaviour and leaves
        the previous artifacts on disk intact.
        """

        symbol = inputs.symbol
        progress(f"读取 {symbol} 的本地 fresh 分钟 bid/ask…")
        chosen = _choose_provider(self._store.load(symbol))
        if chosen is None:
            raise ValueError(
                f"{symbol} 尚无可用分钟数据；请先订阅实时行情并录制"
            )
        provider, selected = chosen
        sessions = group_regular_sessions(selected)
        if not sessions:
            raise ValueError(
                f"{symbol} 在纽约常规交易时段内没有可回放分钟"
            )
        session_date, session_rows = sessions[-1]
        progress(
            f"使用 {provider} 的最近独立会话 {session_date}，"
            f"共 {len(session_rows)} 行分钟证据执行回放…"
        )
        result = run_targeted_replay(
            session_rows,
            strategy_version_id=inputs.strategy_version_id,
            strategy_semver=inputs.strategy_semver,
            parameter_hash=inputs.parameter_hash,
            parameters=inputs.parameters_mapping(),
            initial_equity=inputs.initial_equity,
        )
        save_targeted_replay(result, self._directory(REPLAY_DIRECTORY))
        return result

    # -- robustness --------------------------------------------------------

    def run_robustness(
        self,
        inputs: TargetedEvidenceRunInputs,
        *,
        progress: Callable[[str], None],
    ) -> TargetedRobustnessBundle:
        """Run the multi-day robustness suite and save every family it produced.

        The suite is one procedure that returns one bundle, because a review
        whose sibling diagnostics were never committed would be evidence about a
        run nobody can inspect.  Both the usable and the raw record sets come
        from the *same* chosen provider -- see ``_choose_provider``.
        """

        symbol = inputs.symbol
        progress(f"按行情源读取 {symbol} 的独立分钟会话…")
        chosen = _choose_provider(self._store.load(symbol))
        if chosen is None:
            raise ValueError(
                f"{symbol} 尚无可用分钟数据；请先录制多个交易日"
            )
        provider, selected = chosen
        raw_selected = tuple(
            row
            for row in self._store.load(symbol, usable_only=False)
            if row.provider == provider
        )
        progress(
            f"使用单一行情源 {provider}；按纽约交易日分组并运行"
            "基准及四组参数扰动…"
        )
        result = run_targeted_robustness(
            selected,
            strategy_version_id=inputs.strategy_version_id,
            strategy_semver=inputs.strategy_semver,
            parameter_hash=inputs.parameter_hash,
            parameters=inputs.parameters_mapping(),
            initial_equity=inputs.initial_equity,
        )
        save_targeted_robustness(
            result, self._directory(ROBUSTNESS_DIRECTORY)
        )
        progress("按固定候选集运行 CSCV/PBO 与 DSR 过拟合诊断…")
        overfit = run_targeted_overfit_diagnostics(result)
        save_targeted_overfit(overfit, self._directory(OVERFIT_DIRECTORY))
        progress("检查 346 个预期分钟、连续缺口、报价年龄和一档数量…")
        data_quality = run_targeted_data_quality(result, raw_selected)
        save_targeted_data_quality(
            data_quality, self._directory(DATA_QUALITY_DIRECTORY)
        )
        validation = None
        if result.usable_sessions >= MINIMUM_WALK_FORWARD_SESSIONS:
            progress(
                "有效会话达到 20；执行仅训练集选参、"
                "验证门和未触碰测试集…"
            )
            validation = run_targeted_walk_forward(
                result,
                selected,
                parameters=inputs.parameters_mapping(),
                initial_equity=inputs.initial_equity,
            )
            save_targeted_walk_forward(
                validation, self._directory(WALK_FORWARD_DIRECTORY)
            )
        progress("运行配置成本、5bps、10bps+双倍佣金执行压力…")
        execution_stress = run_targeted_execution_stress(
            result,
            selected,
            parameters=inputs.parameters_mapping(),
            initial_equity=inputs.initial_equity,
        )
        save_targeted_execution_stress(
            execution_stress, self._directory(EXECUTION_STRESS_DIRECTORY)
        )
        progress("汇总证据身份、真实流来源、序列相关性与晋级硬门…")
        review = run_targeted_review(
            result,
            validation,
            overfit,
            parameters=inputs.parameters_mapping(),
            data_quality=data_quality,
            execution_stress=execution_stress,
        )
        save_targeted_review(review, self._directory(REVIEW_DIRECTORY))
        return TargetedRobustnessBundle(
            robustness=result,
            walk_forward=validation,
            overfit=overfit,
            data_quality=data_quality,
            execution_stress=execution_stress,
            review=review,
        )

    # -- startup restoration ----------------------------------------------

    def load_saved(self) -> TargetedEvidenceSnapshot:
        """Read all seven artifact families back, newest first per family.

        This is one call rather than seven because the seven are one snapshot:
        the window used to make seven hand-written loader calls on startup, and
        a family added to the snapshot but missed here would render as
        permanently empty rather than failing.  The selections stay ``None``:
        startup has never auto-selected a run, and this round does not change
        that.

        Each family's loader tolerates a malformed file by skipping it, so a
        half-written artifact cannot stop the desktop from starting -- the same
        tolerance the window had when it parsed those files itself.
        """

        return TargetedEvidenceSnapshot(
            replay_results=load_targeted_replays(
                self._directory(REPLAY_DIRECTORY)
            ),
            robustness_results=load_targeted_robustness(
                self._directory(ROBUSTNESS_DIRECTORY)
            ),
            walk_forward_results=load_targeted_walk_forwards(
                self._directory(WALK_FORWARD_DIRECTORY)
            ),
            overfit_results=load_targeted_overfits(
                self._directory(OVERFIT_DIRECTORY)
            ),
            data_quality_results=load_targeted_data_quality(
                self._directory(DATA_QUALITY_DIRECTORY)
            ),
            execution_stress_results=load_targeted_execution_stress(
                self._directory(EXECUTION_STRESS_DIRECTORY)
            ),
            review_results=load_targeted_reviews(
                self._directory(REVIEW_DIRECTORY)
            ),
        )


__all__ = [
    "DATA_QUALITY_DIRECTORY",
    "EXECUTION_STRESS_DIRECTORY",
    "MINIMUM_WALK_FORWARD_SESSIONS",
    "OVERFIT_DIRECTORY",
    "REPLAY_DIRECTORY",
    "REVIEW_DIRECTORY",
    "ROBUSTNESS_DIRECTORY",
    "WALK_FORWARD_DIRECTORY",
    "DesktopTargetedEvidenceService",
]
