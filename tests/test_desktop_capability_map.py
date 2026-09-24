"""Navigation-index guards for ``docs/DESKTOP_CAPABILITY_MAP.md``.

Deliberately minimal. The map exists so a maintainer can find a capability's
owner without reading ``desktop.py``; the failure mode worth guarding is the map
drifting into fiction, not a formatting mistake. So this checks that the file
stays an index -- present, short, and still naming every capability -- and does
not parse the table into a schema. Turning the map into a structured contract
would make it a thing to learn rather than a thing to read.
"""

from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MAP = _REPO_ROOT / "docs" / "DESKTOP_CAPABILITY_MAP.md"

#: The map must stay a glance, not a design document.
LINE_BUDGET = 150

#: Every capability the desktop has, extracted or not.  A row disappearing means
#: a maintainer's entry point went with it.
#:
#: v2O-C5A split ``Targeted`` into three rows on purpose: the workspace held three
#: unrelated things -- research evidence, the target session/preflight, and the
#: Shadow runtime -- and only the first moved.  One row would have had to describe
#: three owners at once, which is exactly the confusion the extraction removes, so
#: each is listed and each has to keep its own row.
CAPABILITIES = (
    "Market",
    "Account",
    "Universe",
    "History",
    "Scanner",
    "Backtest",
    "Cross Section",
    "Targeted Evidence",
    "Targeted Session / Preflight",
    "Shadow",
    "Paper",
    "System",
)


def test_the_capability_map_exists_and_stays_an_index() -> None:
    assert _MAP.exists()
    count = len(_MAP.read_text(encoding="utf-8").splitlines())
    assert count <= LINE_BUDGET, (
        f"capability map is {count} lines; a note that needs five paragraphs "
        f"belongs in TRADING_ARCHITECTURE_V2 or DESKTOP_DECOMPOSITION"
    )


def test_every_capability_has_a_row() -> None:
    text = _MAP.read_text(encoding="utf-8")
    missing = [name for name in CAPABILITIES if f"**{name}**" not in text]
    assert not missing, missing


def test_the_declared_columns_are_present() -> None:
    """The six facts a reader comes here for."""

    text = _MAP.read_text(encoding="utf-8")
    for column in (
        "Canonical truth owner",
        "Page render owner",
        "Application / service dependency",
        "Public orchestration API",
        "Cross-workflow bridge",
        "Status",
    ):
        assert column in text, column


def test_the_paper_row_names_the_capability_and_claims_the_whole_round() -> None:
    """v2O-E is complete, and the row says exactly what moved.

    Two failures are guarded, in opposite directions, and the pair is why this test
    exists rather than a one-line status check.  A row that still said the session's
    polling, ingress, controls, recovery, finalization or *presentation cache* were the
    window's would send the next maintainer to ``desktop.py`` for facts and sequences that
    are no longer there; a row claiming "v2O-E complete" while any of those was still the
    window's would be the false claim the round exists to retire.

    Since v2O-E4 all four halves have moved -- launch (E1), active run (E2),
    recovery/finalization/shutdown (E3) and presentation/render (E4) -- so the status is
    complete and every retired owner has to be absent from the row.
    """

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line for line in text.splitlines() if line.startswith("| **Paper**")
    )
    assert "PaperOrchestrator" in row
    assert "v2O-E complete" in row
    assert "v2O-E partial" not in row
    # The window must no longer be described as the owner of the launch, of the active
    # session's run, or of the presentation fact the route draws.
    for retired in (
        "MainWindow._start_auto_quant",
        "MainWindow._auto_order_service_connected",
        "MainWindow._poll_auto_quant_orders",
        "MainWindow._pause_auto_quant_entries",
        "MainWindow._resume_auto_quant_entries",
        "MainWindow._stop_auto_quant",
        "MainWindow._paper_render_snapshot",
    ):
        assert retired not in row, retired
    # And the retained presentation fact is named as the capability's, with the reason it
    # exists beside it -- a reader who finds only "the orchestrator owns presentation"
    # cannot tell why the window may not cache it.
    assert "paper_orchestrator.presentation" in row
    assert "finalize_if_safe" in row
    assert "not started" not in row


def test_the_history_bridge_is_not_described_backwards() -> None:
    """Direction is the thing readers get wrong, so it is pinned.

    The only history bridge is ``history_changed -> market scope summary``.
    AutoQuant *calls* History during its preparation; that is AutoQuant ->
    History and must not be written as if History published into AutoQuant.
    """

    text = _MAP.read_text(encoding="utf-8")
    history_row = next(
        line for line in text.splitlines() if line.startswith("| **History**")
    )
    assert "_refresh_market_scope_summary" in history_row
    assert "AutoQuant" not in history_row, history_row


def test_the_market_bridge_separates_its_three_paths() -> None:
    """Snapshot fan-out, shell health and the watchlist command are distinct.

    Collapsing them is the specific error this map was corrected for: the
    watchlist is a Market-page user intent that reads Scanner and Account, not a
    reaction to ``snapshot_changed``.
    """

    text = _MAP.read_text(encoding="utf-8")
    for needle in (
        "_on_market_snapshot_changed",
        "_render_market_shell_health",
        "_apply_intraday_watchlist",
    ):
        assert needle in text, needle


def test_the_map_does_not_reference_the_deleted_helper() -> None:
    """The shared AST module was removed; a stale line would misdirect."""

    text = _MAP.read_text(encoding="utf-8")
    assert "desktop_architecture_support" not in text


def test_the_cross_section_row_points_at_the_capability() -> None:
    """v2O-C4: the row must not still describe the window as the owner.

    The failure this guards is a map that keeps saying "not started" while the
    extraction is done, which sends the next maintainer to ``desktop.py`` for
    state that is no longer there.
    """

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line
        for line in text.splitlines()
        if line.startswith("| **Cross Section**")
    )
    assert "CrossSectionOrchestrator._report" in row
    assert "MainWindow.cross_section_report" not in row
    assert "v2O-C4 complete" in row


def test_the_evidence_row_points_at_the_capability() -> None:
    """v2O-C5A: the evidence half moved, and its row must say so."""

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line
        for line in text.splitlines()
        if line.startswith("| **Targeted Evidence**")
    )
    assert "TargetedEvidenceOrchestrator.snapshot" in row
    assert "DesktopTargetedEvidenceService" in row
    assert "v2O-C5A complete" in row
    # The window must not still be described as the evidence owner.
    assert "_selected_robustness_run_id" not in row
    assert "_publish_targeted_view" not in row


def test_the_session_row_points_at_the_capability() -> None:
    """v2O-C5B: the second half of the Targeted split moved, and its row says so.

    The intermediate state C5A recorded -- evidence migrated, session still on the
    window -- is over.  The failure this guards is a map that keeps saying
    "not started" while the extraction is done, which sends the next maintainer to
    ``desktop.py`` for state that is no longer there.
    """

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line
        for line in text.splitlines()
        if line.startswith("| **Targeted Session / Preflight**")
    )
    assert "TargetedSessionOrchestrator.snapshot" in row
    assert "DesktopTargetedSessionService" in row
    assert "v2O-C5B complete" in row
    # The window must not still be described as the session owner.  Checked as
    # `self.`-prefixed attributes, because `refresh_minute_status` legitimately
    # contains `_minute_status` and is the capability's own command.
    for retired in (
        "self._target_status",
        "self._minute_status",
        "self.target_preflight_result",
        "_publish_targeted_session_view",
    ):
        assert retired not in row, retired


def test_the_shadow_row_names_its_capability_owner() -> None:
    """v2O-D landed, so the row must name ``ShadowOrchestrator``, not the window.

    This guard was written the other way round while Shadow was "a later slice":
    it asserted ``MainWindow.shadow_engine`` so a round could not claim the
    runtime had moved when it had not.  It has moved, so the assertion inverts --
    the row must name the capability and must no longer name a window attribute.
    Inverting rather than deleting keeps the same protection in both directions.
    """

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line for line in text.splitlines() if line.startswith("| **Shadow**")
    )
    assert "ShadowOrchestrator.snapshot" in row
    assert "v2O-D complete" in row
    assert "MainWindow.shadow_engine" not in row
    assert "v2O-D next" not in row


def test_the_system_row_names_both_capabilities_and_overclaims_nothing() -> None:
    """v2O-F1 moved Runtime Events; v2O-F2 moved Settings.

    Two failures are guarded, in opposite directions.  A row that still sent the
    maintainer to ``desktop.py`` for the event store write, the coalescing, the
    settings render or the credential sequencing would describe owners that no
    longer exist; a row claiming "System orchestration complete" would be a claim
    neither round may make, because the Gateway/connection probe is still the
    window's and is re-audited before ``v2O-F`` can be closed.
    """

    text = _MAP.read_text(encoding="utf-8")
    row = next(
        line for line in text.splitlines() if line.startswith("| **System**")
    )
    assert "RuntimeEventsOrchestrator" in row
    assert "SettingsOrchestrator" in row
    assert "v2O-F1 complete" in row
    assert "v2O-F2 complete" in row
    assert "v2O-F2 next" not in row
    for retired in (
        "MainWindow._record_runtime_event",
        "MainWindow._refresh_runtime_events",
        "MainWindow._schedule_runtime_events_refresh",
        "MainWindow._resolve_runtime_event",
        "MainWindow._export_terminal_state",
        "MainWindow._last_runtime_export",
        "MainWindow._publish_settings_view",
        "MainWindow._settings_api_provider",
        "MainWindow._connection_settings_enabled",
        "MainWindow._save_user_preferences",
    ):
        assert retired not in row, retired
    assert "System orchestration complete" not in row
    assert "v2O-F complete" not in row


def test_the_shared_research_capital_fact_has_an_entry() -> None:
    """The scalar is not a page capability, so it gets its own section.

    Pinning it here is what stops a later round from quietly re-listing it as
    Cross Section's state -- which would hide its seven other consumers.
    """

    text = _MAP.read_text(encoding="utf-8")
    assert "ResearchScenarioCapitalState" in text
    for consumer in (
        "Scanner",
        "AutoQuant",
        "Targeted",
        "Account presentation",
    ):
        assert consumer in text, consumer
    # And the safety boundary must be stated, not merely implied.
    assert "CapitalAllocator" in text
