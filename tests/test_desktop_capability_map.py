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
CAPABILITIES = (
    "Market",
    "Account",
    "Universe",
    "History",
    "Scanner",
    "Backtest",
    "Cross Section",
    "Targeted",
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
