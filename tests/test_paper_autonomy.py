"""Paper autonomy v1-A behaviour: the control plane, exercised end to end.

Each test here corresponds to one of the properties the control plane has to
have before an unattended process is allowed to read it.  They are grouped by
the question an operator would ask:

* what does a system nobody has configured do?  (A)
* does each transition mean what it says?  (B-G)
* can the kill switch be walked around?  (H-J)
* what happens when two surfaces disagree?  (K, N)
* what survives a restart, and what happens when the record is corrupt?  (L, M)
* can the decision trail be read back?  (O)
* is the operator CLI a surface rather than a second authority?  (P)
* is a transition one commit, or two writes with a gap between them?  (Q, R)
* is a transition that did nothing recorded as one that did?  (S)
* is a corrupt *trail* as unreadable as a corrupt row?  (T, U)

The Q-U group is the half that a two-transaction implementation cannot pass.
Q forces the audit write to fail inside real SQLite and asserts the intent went
with it; R refuses a pair of arguments that disagree with each other; S asserts
that clearing a latch that is not set is not a transition; T and U assert that a
damaged history is reported rather than shortened.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pathlib
import sqlite3
import threading

import pytest

from us_quant.cli import paper_autonomy_status, paper_autonomy_transition
from us_quant.trading.adapters.sqlite.paper_autonomy_repository import (
    SQLitePaperAutonomyRepository,
)
from us_quant.trading.application.paper_autonomy import (
    PaperAutonomyApplication,
    PaperAutonomyRefused,
)
from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
    PaperAutonomyEventKind,
    PaperAutonomyIntent,
    PaperAutonomyMode,
    initial_intent,
)
from us_quant.trading.ports.paper_autonomy_repository import (
    PaperAutonomyConflict,
    PaperAutonomyEvent,
    PaperAutonomyRepositoryError,
    PaperAutonomyStoreUnreadable,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CLI_PATH = _REPO_ROOT / "src" / "us_quant" / "cli.py"
_PAPER_CONFIG = _REPO_ROOT / "configs" / "paper.toml"

#: A clock that advances one second per call, so the stored timestamps are
#: deterministic *and* ordered without any test sleeping.
_BASE_INSTANT = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


class _FixedClock:
    def __init__(self) -> None:
        self._calls = 0

    def __call__(self) -> datetime:
        moment = _BASE_INSTANT + timedelta(seconds=self._calls)
        self._calls += 1
        return moment


def _store(tmp_path: pathlib.Path) -> SQLitePaperAutonomyRepository:
    return SQLitePaperAutonomyRepository(tmp_path / "paper_autonomy.sqlite3")


def _application(
    store: SQLitePaperAutonomyRepository,
) -> PaperAutonomyApplication:
    return PaperAutonomyApplication(store, clock=_FixedClock())


def _reopen(
    store: SQLitePaperAutonomyRepository,
) -> PaperAutonomyApplication:
    """A brand-new repository and application over the same file.

    This is the restart: nothing is carried over in memory, so whatever comes
    back came out of the database.
    """

    return _application(SQLitePaperAutonomyRepository(store.path))


def _execute(path: pathlib.Path, statement: str, parameters: tuple = ()) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def _revisions(store: SQLitePaperAutonomyRepository) -> list[int]:
    return [event.revision for event in store.recent_events(50)]


def _kinds(store: SQLitePaperAutonomyRepository) -> list[str]:
    return [event.kind.value for event in store.recent_events(50)]


def _event_for(
    intent: PaperAutonomyIntent, kind: PaperAutonomyEventKind
) -> PaperAutonomyEvent:
    """The audit event a transition to ``intent`` must carry.

    Built from the intent rather than alongside it so a test that hands the
    store a manually assembled transition hands it a *coherent* one; the tests
    that deliberately hand it an incoherent pair build the disagreement
    explicitly.
    """

    return PaperAutonomyEvent(
        revision=intent.revision,
        kind=kind,
        detail=intent.reason,
        occurred_at=intent.updated_at,
    )


def _stored_event_revisions(path: pathlib.Path) -> list[int]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT revision FROM paper_autonomy_events ORDER BY revision"
        ).fetchall()
    finally:
        connection.close()
    return [int(row[0]) for row in rows]


# =====================================================================
# A. The default
# =====================================================================


def test_a_a_store_nobody_configured_is_disabled(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)

    intent = store.load_intent()
    assert intent == initial_intent()
    assert intent.mode is PaperAutonomyMode.DISABLED
    assert intent.kill_switch_latched is False
    assert intent.revision == INITIAL_REVISION
    assert intent.allows_autonomous_work is False
    assert store.recent_events(10) == ()


# =====================================================================
# B-G. The transitions
# =====================================================================


def test_b_enable_advances_one_revision_and_records_one_event(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)

    intent = application.enable(INITIAL_REVISION, "run Paper unattended today")

    assert intent.mode is PaperAutonomyMode.ENABLED
    assert intent.revision == INITIAL_REVISION + 1
    assert intent.reason == "run Paper unattended today"
    assert intent.allows_autonomous_work is True

    events = store.recent_events(10)
    assert len(events) == 1
    assert events[0].revision == intent.revision
    assert events[0].kind is PaperAutonomyEventKind.ENABLED
    assert events[0].detail == "run Paper unattended today"
    assert events[0].occurred_at == intent.updated_at


def test_c_pause_moves_an_enabled_intent_to_paused(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")

    paused = application.pause(enabled.revision, "no new entries for now")

    assert paused.mode is PaperAutonomyMode.PAUSED
    assert paused.revision == enabled.revision + 1
    # Paused means "no new work", not "no permission at all".
    assert paused.allows_autonomous_work is False
    assert paused.kill_switch_latched is False


def test_d_a_paused_intent_can_be_enabled_again(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    paused = application.pause(enabled.revision, "pause")

    resumed = application.enable(paused.revision, "resume")

    assert resumed.mode is PaperAutonomyMode.ENABLED
    assert resumed.revision == paused.revision + 1
    assert resumed.allows_autonomous_work is True


def test_e_disable_withdraws_the_authorisation(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")

    disabled = application.disable(enabled.revision, "operator withdraws")

    assert disabled.mode is PaperAutonomyMode.DISABLED
    assert disabled.kill_switch_latched is False
    assert disabled.allows_autonomous_work is False


def test_f_killing_an_enabled_intent_disables_it(tmp_path: pathlib.Path) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")

    latched = application.engage_kill_switch(enabled.revision, "market anomaly")

    assert latched.mode is PaperAutonomyMode.DISABLED
    assert latched.kill_switch_latched is True
    assert latched.allows_autonomous_work is False
    assert latched.revision == enabled.revision + 1


def test_g_killing_a_paused_intent_disables_it(tmp_path: pathlib.Path) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    paused = application.pause(enabled.revision, "pause")

    latched = application.engage_kill_switch(paused.revision, "market anomaly")

    assert latched.mode is PaperAutonomyMode.DISABLED
    assert latched.kill_switch_latched is True


# =====================================================================
# H-J. The kill switch
# =====================================================================


def test_h_enabling_while_latched_is_refused_and_writes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    latched = application.engage_kill_switch(enabled.revision, "kill")

    before = store.load_intent()
    with pytest.raises(PaperAutonomyRefused):
        application.enable(latched.revision, "try to get around the kill")

    assert store.load_intent() == before
    assert _revisions(store) == [enabled.revision, latched.revision]


def test_i_clearing_the_latch_does_not_re_enable(tmp_path: pathlib.Path) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    latched = application.engage_kill_switch(enabled.revision, "kill")

    cleared = application.clear_kill_switch(latched.revision, "clearing the latch")

    assert cleared.kill_switch_latched is False
    assert cleared.mode is PaperAutonomyMode.DISABLED
    assert cleared.allows_autonomous_work is False
    assert cleared.revision == latched.revision + 1


def test_j_re_enabling_after_a_kill_is_a_second_decision(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    latched = application.engage_kill_switch(enabled.revision, "kill")
    cleared = application.clear_kill_switch(latched.revision, "clear")

    re_enabled = application.enable(cleared.revision, "re-authorise autonomy")

    assert re_enabled.mode is PaperAutonomyMode.ENABLED
    assert re_enabled.kill_switch_latched is False
    assert _kinds(store) == [
        "AUTONOMY_ENABLED",
        "AUTONOMY_KILL_LATCHED",
        "AUTONOMY_KILL_CLEARED",
        "AUTONOMY_ENABLED",
    ]


# =====================================================================
# K, N. Two writers
# =====================================================================


def test_k_a_stale_writer_is_refused_and_changes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)
    first = application.enable(INITIAL_REVISION, "authorise autonomy")

    # This surface still believes the store is at revision 0.
    with pytest.raises(PaperAutonomyConflict):
        application.enable(INITIAL_REVISION, "a stale surface authorises autonomy")

    assert store.load_intent() == first
    assert _revisions(store) == [first.revision]

    # And the store refuses it on its own account, without the application's
    # help: this is the assertion a last-write-wins adapter fails, because the
    # application's read and the store's write are two moments, and only the
    # store can arbitrate between them.
    with pytest.raises(PaperAutonomyConflict):
        store.commit_transition(
            expected_revision=INITIAL_REVISION,
            replacement=first,
            event=_event_for(first, PaperAutonomyEventKind.ENABLED),
        )
    assert store.load_intent() == first
    assert _revisions(store) == [first.revision]


def test_n_concurrent_writers_produce_exactly_one_transition(
    tmp_path: pathlib.Path,
) -> None:
    """Eight surfaces, one revision: the store is the arbiter, not the last one.

    Each worker builds its own repository and application, which is what a
    desktop shell, an operator CLI and a scheduler actually are: separate
    objects over one file.  The barrier makes them race rather than queue, so a
    last-write-wins store fails this instead of passing it by luck.
    """

    path = tmp_path / "paper_autonomy.sqlite3"
    SQLitePaperAutonomyRepository(path)
    workers = 8
    barrier = threading.Barrier(workers)

    def attempt(_index: int) -> str:
        application = _application(SQLitePaperAutonomyRepository(path))
        barrier.wait()
        try:
            application.enable(INITIAL_REVISION, "concurrent authorisation")
        except PaperAutonomyConflict:
            return "conflict"
        return "accepted"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(attempt, range(workers)))

    assert outcomes.count("accepted") == 1
    assert outcomes.count("conflict") == workers - 1

    store = SQLitePaperAutonomyRepository(path)
    final = store.load_intent()
    assert final.revision == INITIAL_REVISION + 1
    assert _revisions(store) == [INITIAL_REVISION + 1]

    # The winner's transition is the *whole* transition: one event, and one that
    # describes the intent that survived.  A store that committed the intent and
    # then raced to append the event would have nowhere to put the loser's, and
    # the trail would stop describing the value beside it.
    assert _kinds(store) == ["AUTONOMY_ENABLED"]
    event = store.recent_events(10)[0]
    assert event.detail == final.reason
    assert event.occurred_at == final.updated_at


# =====================================================================
# L, M. Restart and a corrupt record
# =====================================================================


def test_l_a_restart_restores_the_stored_intent(tmp_path: pathlib.Path) -> None:
    store = _store(tmp_path)
    application = _application(store)
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    latched = application.engage_kill_switch(enabled.revision, "kill")

    restarted = _reopen(store)

    assert restarted.snapshot() == latched
    assert restarted.snapshot().revision == latched.revision
    assert restarted.snapshot().kill_switch_latched is True
    # The latch survives the restart, so a restarted process cannot start
    # trading on the strength of a permission it cannot see any more.
    assert restarted.snapshot().allows_autonomous_work is False
    assert _kinds(store) == ["AUTONOMY_ENABLED", "AUTONOMY_KILL_LATCHED"]


@pytest.mark.parametrize(
    "column, value",
    (
        ("mode", "ENABLED"),
        ("mode", "enabled "),
        ("mode", "running"),
        ("mode", ""),
        ("kill_switch_latched", "7"),
        ("kill_switch_latched", "yes"),
        ("revision", "-1"),
        ("revision", "many"),
        ("updated_at", "not a timestamp"),
        ("updated_at", "2026-03-02T14:30:00"),
        ("reason", "   "),
    ),
)
def test_m_a_corrupt_stored_intent_is_never_read_as_enabled(
    tmp_path: pathlib.Path, column: str, value: str
) -> None:
    """Fail closed, and fail *loudly* -- never "close enough, call it enabled".

    ``ENABLED`` is in this table twice on purpose, spelled the way a tampering
    hand would spell it: the stored value of ``PaperAutonomyMode.ENABLED`` is
    ``"enabled"`` in lower case, and accepting a different spelling would mean
    this store had invented a normalisation rule for a value nobody wrote.
    """

    store = _store(tmp_path)
    application = _application(store)
    application.enable(INITIAL_REVISION, "authorise autonomy")
    _execute(
        store.path,
        f"UPDATE paper_autonomy_intent SET {column} = ?",
        (value,),
    )

    with pytest.raises(PaperAutonomyStoreUnreadable):
        store.load_intent()
    with pytest.raises(PaperAutonomyStoreUnreadable):
        _reopen(store).snapshot()


# =====================================================================
# O. The decision trail
# =====================================================================


def test_o_the_event_trail_replays_the_revisions_in_order(
    tmp_path: pathlib.Path,
) -> None:
    store = _store(tmp_path)
    application = _application(store)

    state = application.enable(INITIAL_REVISION, "first authorisation")
    state = application.pause(state.revision, "hold new work")
    state = application.enable(state.revision, "resume")
    state = application.engage_kill_switch(state.revision, "kill")
    state = application.clear_kill_switch(state.revision, "clear the latch")

    assert _revisions(store) == [1, 2, 3, 4, 5]
    assert _kinds(store) == [
        "AUTONOMY_ENABLED",
        "AUTONOMY_PAUSED",
        "AUTONOMY_ENABLED",
        "AUTONOMY_KILL_LATCHED",
        "AUTONOMY_KILL_CLEARED",
    ]

    # The trail survives the restart, and a bounded read returns the *newest*
    # events in chronological order rather than a reversed window.
    restarted = SQLitePaperAutonomyRepository(store.path)
    assert [event.revision for event in restarted.recent_events(2)] == [4, 5]
    assert restarted.recent_events(0) == ()
    assert restarted.load_intent() == state

    # And the whole trail, not a window: the intent's revision *is* the number
    # of accepted transitions, so the two can never disagree.  Read the whole
    # sequence rather than the first five so this cannot pass by construction.
    whole = restarted.recent_events(50)
    assert [event.revision for event in whole] == list(
        range(1, state.revision + 1)
    )
    assert len(whole) == state.revision
    assert whole[-1].detail == state.reason
    assert whole[-1].occurred_at == state.updated_at


# =====================================================================
# Q, R. One transition, one commit
# =====================================================================


def test_q_a_failed_audit_write_rolls_the_intent_back(
    tmp_path: pathlib.Path,
) -> None:
    """The intent and its event are one commit, proved against a real database.

    The failure is forced inside SQLite rather than by patching the repository.
    A patched method would only show that the application makes one *call*, which
    is a different and much weaker claim than "a refused event write leaves no
    intent behind" -- the defect this test exists for is a store that commits
    the intent and then cannot append the event, and only a real failing write
    can produce that state.

    A trigger is used because it fails at exactly the moment the second half of
    the transition is written, which is the only moment that matters here.
    """

    store = _store(tmp_path)
    application = _application(store)
    _execute(
        store.path,
        """
        CREATE TRIGGER fail_autonomy_event
        BEFORE INSERT ON paper_autonomy_events
        BEGIN
            SELECT RAISE(ABORT, 'forced event failure');
        END
        """,
    )

    with pytest.raises(PaperAutonomyRepositoryError) as caught:
        application.enable(INITIAL_REVISION, "authorise autonomy")

    # A storage failure, not a refusal and not a stale write: those two send the
    # operator looking for a policy problem that does not exist.
    assert not isinstance(
        caught.value, (PaperAutonomyConflict, PaperAutonomyStoreUnreadable)
    )
    # And it does not leak the statement or the file path it failed on.
    assert "INSERT" not in str(caught.value)
    assert str(store.path) not in str(caught.value)

    # Nothing survived the failure -- neither half of the transition.  This is
    # the assertion a two-transaction store fails: it leaves revision 1 behind,
    # with the operator holding an error and the canonical intent already moved.
    assert store.load_intent() == initial_intent()
    assert store.load_intent().revision == INITIAL_REVISION
    assert store.recent_events(10) == ()
    assert _stored_event_revisions(store.path) == []


def test_r_the_store_refuses_a_pair_that_is_not_one_transition(
    tmp_path: pathlib.Path,
) -> None:
    """Revision, instant and reason have to describe the same transition.

    No production caller can build these -- the authority derives all three from
    one decision -- which is exactly why they are asserted here: a store that
    accepted them would hold an intent and a trail that disagree, and a reader
    reconstructs each from the other.

    The mismatched-event case names a revision the intent never reaches, rather
    than one that is merely wrong.  A colliding revision would also be stopped by
    the revision index, which would make this test pass for a reason that has
    nothing to do with the check under it -- measured, not assumed: an earlier
    version of this case used a colliding revision and let a mutant that removes
    the check survive.
    """

    store = _store(tmp_path)
    application = _application(store)
    first = application.enable(INITIAL_REVISION, "authorise autonomy")
    good = _event_for(first, PaperAutonomyEventKind.ENABLED)
    later = first.updated_at + timedelta(seconds=1)

    incoherent = {
        "a revision jump": (replace(first, revision=3), good),
        "a revision that does not advance": (first, good),
        "an event naming a revision the intent never reaches": (
            replace(first, revision=2),
            replace(good, revision=3),
        ),
        "an event from another instant": (
            replace(first, revision=2),
            replace(good, revision=2, occurred_at=later),
        ),
        "an event recording another reason": (
            replace(first, revision=2),
            replace(good, revision=2, detail="a different decision"),
        ),
    }

    for label, (replacement, event) in incoherent.items():
        with pytest.raises(PaperAutonomyRepositoryError):
            store.commit_transition(
                expected_revision=first.revision,
                replacement=replacement,
                event=event,
            )
        assert store.load_intent() == first, label
        assert _revisions(store) == [first.revision], label

    # And the coherent version of the same call still goes through, so the
    # checks above cannot be passing because the store refuses everything.
    second = application.pause(first.revision, "stop opening new work")
    assert second.revision == first.revision + 1
    assert _revisions(store) == [first.revision, second.revision]


def test_v_the_schema_allows_one_event_per_revision(
    tmp_path: pathlib.Path,
) -> None:
    """One accepted transition, one event -- enforced by the store, not by habit.

    Written through SQL, because the store itself cannot produce a duplicate:
    that is the point.  The constraint has to hold against something reaching
    past the store, or it is only a convention the store happens to follow.
    """

    store = _store(tmp_path)
    application = _application(store)
    application.enable(INITIAL_REVISION, "authorise autonomy")

    with pytest.raises(sqlite3.IntegrityError):
        _execute(
            store.path,
            """
            INSERT INTO paper_autonomy_events(
                revision, event, detail, occurred_at
            ) VALUES (1, 'AUTONOMY_PAUSED', 'a second event for revision 1',
                      '2026-03-02T14:30:00+00:00')
            """,
        )

    assert _stored_event_revisions(store.path) == [1]


def test_v2_a_pre_existing_duplicate_revision_is_not_repaired(
    tmp_path: pathlib.Path,
) -> None:
    """A store written before the index is reported, not silently rebuilt.

    The database is assembled by hand so it can hold what the current schema
    forbids: the same revision twice.  Opening it has to fail rather than pick
    one of the two rows, and both rows have to still be there afterwards --
    deleting one, or regenerating the trail from the revision count, would be
    this module inventing the history it exists to report.
    """

    path = tmp_path / "paper_autonomy.sqlite3"
    _execute(
        path,
        """
        CREATE TABLE paper_autonomy_intent(
            key TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            mode TEXT NOT NULL,
            kill_switch_latched INTEGER NOT NULL,
            reason TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    )
    _execute(
        path,
        """
        CREATE TABLE paper_autonomy_events(
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            revision INTEGER NOT NULL,
            event TEXT NOT NULL,
            detail TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """,
    )
    _execute(
        path,
        """
        INSERT INTO paper_autonomy_intent(
            key, revision, mode, kill_switch_latched, reason, updated_at
        ) VALUES ('paper', 1, 'enabled', 0, 'legacy', '2026-03-02T14:30:00+00:00')
        """,
    )
    for detail in ("first", "duplicate"):
        _execute(
            path,
            """
            INSERT INTO paper_autonomy_events(
                revision, event, detail, occurred_at
            ) VALUES (1, 'AUTONOMY_ENABLED', ?, '2026-03-02T14:30:00+00:00')
            """,
            (detail,),
        )
    assert _stored_event_revisions(path) == [1, 1]

    with pytest.raises(PaperAutonomyStoreUnreadable):
        SQLitePaperAutonomyRepository(path)

    assert _stored_event_revisions(path) == [1, 1]


# =====================================================================
# S. A transition that did nothing is not a transition
# =====================================================================


def test_s_clear_kill_is_refused_when_the_latch_is_not_set(
    tmp_path: pathlib.Path,
) -> None:
    """Clearing a latch that is not set is refused, and writes no audit entry.

    Allowing it would put a ``KILL_CLEARED`` event in the trail for a kill that
    never happened, in the one table an incident review trusts.  The trail would
    then say two clears for one kill, which is a lie about the operator's
    actions rather than a harmless no-op.
    """

    store = _store(tmp_path)
    application = _application(store)

    # The store nobody configured.
    with pytest.raises(PaperAutonomyRefused):
        application.clear_kill_switch(
            INITIAL_REVISION, "release a latch that is not set"
        )
    assert store.load_intent() == initial_intent()
    assert store.recent_events(10) == ()

    # And an enabled system, which has no latch either.
    enabled = application.enable(INITIAL_REVISION, "authorise autonomy")
    with pytest.raises(PaperAutonomyRefused):
        application.clear_kill_switch(
            enabled.revision, "release a latch that is not set"
        )
    assert store.load_intent() == enabled
    assert _kinds(store) == ["AUTONOMY_ENABLED"]

    # Latched is the one state where it means something.
    latched = application.engage_kill_switch(enabled.revision, "kill")
    cleared = application.clear_kill_switch(latched.revision, "release it")
    assert cleared.kill_switch_latched is False
    assert cleared.mode is PaperAutonomyMode.DISABLED
    assert cleared.revision == latched.revision + 1
    assert _kinds(store) == [
        "AUTONOMY_ENABLED",
        "AUTONOMY_KILL_LATCHED",
        "AUTONOMY_KILL_CLEARED",
    ]

    # A second clear is refused too, and writes nothing at all -- no revision
    # and no event.
    with pytest.raises(PaperAutonomyRefused):
        application.clear_kill_switch(cleared.revision, "release it again")
    assert store.load_intent() == cleared
    assert _kinds(store).count("AUTONOMY_KILL_CLEARED") == 1

    # ``engage_kill_switch`` stays total.  A second press of a kill switch is a
    # real operator action and is recorded as one; absorbing it as a no-op would
    # lose the fact that the operator pressed it.
    again = application.engage_kill_switch(cleared.revision, "kill again")
    assert again.kill_switch_latched is True
    assert again.mode is PaperAutonomyMode.DISABLED
    assert again.revision == cleared.revision + 1
    assert _kinds(store).count("AUTONOMY_KILL_LATCHED") == 2


# =====================================================================
# T, U. A damaged trail is reported, not shortened
# =====================================================================


@pytest.mark.parametrize(
    "column, value",
    (
        ("event", "AUTONOMY_SOMETHING_ELSE"),
        ("event", ""),
        ("detail", "   "),
        ("occurred_at", "not a timestamp"),
        ("occurred_at", "2026-03-02T14:30:00"),
        ("revision", "-1"),
        ("revision", "many"),
    ),
)
def test_t_a_corrupt_audit_entry_is_never_read_as_a_valid_trail(
    tmp_path: pathlib.Path, column: str, value: str
) -> None:
    """A damaged entry is refused, for the same reason a damaged row is.

    The cases split in two.  A bad kind, reason or timestamp damages the *entry*,
    so reading the trail has to refuse even though the revision arithmetic still
    adds up.  A bad revision damages the *sequence*, so the intent beside it
    stops being readable at all.
    """

    store = _store(tmp_path)
    application = _application(store)
    application.enable(INITIAL_REVISION, "authorise autonomy")
    _execute(
        store.path,
        f"UPDATE paper_autonomy_events SET {column} = ? WHERE revision = 1",
        (value,),
    )

    with pytest.raises(PaperAutonomyStoreUnreadable):
        store.recent_events(10)


def test_u_a_gap_in_the_audit_trail_is_not_a_readable_intent(
    tmp_path: pathlib.Path,
) -> None:
    """A missing event is a missing record, not a shorter history.

    Built as a real gap -- the intent at revision 3 with events 1 and 3 -- so
    the refusal cannot be passing because the count happened to disagree for
    some other reason.  The events are deleted through SQL because the store
    itself has no way to remove one, which is the point: a gap can only be
    created by something other than this store reaching into it.
    """

    store = _store(tmp_path)
    application = _application(store)
    state = application.enable(INITIAL_REVISION, "authorise autonomy")
    state = application.pause(state.revision, "hold new work")
    state = application.enable(state.revision, "resume")
    assert state.revision == 3

    _execute(store.path, "DELETE FROM paper_autonomy_events WHERE revision = 2")
    assert _stored_event_revisions(store.path) == [1, 3]

    with pytest.raises(PaperAutonomyStoreUnreadable):
        store.load_intent()
    with pytest.raises(PaperAutonomyStoreUnreadable):
        application.snapshot()


# =====================================================================
# P. The operator CLI
# =====================================================================


def test_p_the_cli_is_a_surface_and_not_a_second_authority(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command reads and writes through the application, and nowhere else.

    Two halves, because either alone would be weak.  The structural half says
    the CLI contains no call to the store's methods and never imports the
    adapter, so a CLI bug cannot produce a write the application would have
    refused.  The behavioural half says the command actually reaches the store
    -- through the application -- and reports the state it produced.
    """

    tree = ast.parse(_CLI_PATH.read_text(encoding="utf-8"))
    store_methods = {
        "load_intent",
        "compare_and_swap_intent",
        "append_event",
        "recent_events",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert calls & store_methods == set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert "us_quant.trading.adapters.sqlite.paper_autonomy_repository" not in modules
    # The command names the builder and the error vocabulary, and never the
    # authority itself -- the same shape the rest of this CLI already has, where
    # an operator surface reaches an application through its composition root.
    assert "us_quant.trading.application.paper_autonomy" not in modules
    assert "us_quant.trading.composition.paper_autonomy" in modules
    assert "us_quant.trading.ports.paper_autonomy_repository" in modules

    database = tmp_path / "paper_autonomy.sqlite3"
    assert (
        paper_autonomy_status(
            config_path=_PAPER_CONFIG, database_path=database
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "disabled"
    assert payload["revision"] == INITIAL_REVISION
    assert payload["allows_autonomous_work"] is False

    assert (
        paper_autonomy_transition(
            config_path=_PAPER_CONFIG,
            database_path=database,
            action="enable",
            reason="operator authorises autonomy",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["mode"] == "enabled"
    assert payload["revision"] == INITIAL_REVISION + 1
    assert payload["recent_events"][0]["event"] == "AUTONOMY_ENABLED"

    assert (
        paper_autonomy_transition(
            config_path=_PAPER_CONFIG,
            database_path=database,
            action="kill",
            reason="operator kill",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kill_switch_latched"] is True
    assert payload["mode"] == "disabled"
    # The kill command says exactly what it did.  It latched a permission; it
    # did not touch a session, an order or a position, and claiming otherwise
    # would be the worst kind of reassurance.
    assert "autonomous runner will refuse new work" in payload["effect"]
    assert "flattened" not in json.dumps(payload)
    assert "session stopped" not in json.dumps(payload)

    # A refused transition exits non-zero, reports nothing applied, and leaves
    # the stored intent alone.
    assert (
        paper_autonomy_transition(
            config_path=_PAPER_CONFIG,
            database_path=database,
            action="enable",
            reason="try to get around the kill",
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["conflict"] is False

    restarted = SQLitePaperAutonomyRepository(database)
    assert restarted.load_intent().revision == INITIAL_REVISION + 2
    assert _kinds(restarted) == ["AUTONOMY_ENABLED", "AUTONOMY_KILL_LATCHED"]


def test_p2_the_cli_requires_a_reason_and_covers_every_verb(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator surface's own guards.

    A blank reason is refused by the application even when the caller bypasses
    the parser, and the parser itself makes ``--reason`` mandatory.  The verb-set
    assertion is the non-vacuous one: the transition table and the help table
    have to describe the same verbs, so a command cannot exist with no
    implementation or an implementation with no command.
    """

    from us_quant.cli import (
        _OPERATOR_HINTS,
        _OPERATOR_TRANSITIONS,
        build_parser,
    )

    assert set(_OPERATOR_HINTS) == set(_OPERATOR_TRANSITIONS) == {
        "enable",
        "pause",
        "disable",
        "kill",
        "clear-kill",
    }

    database = tmp_path / "paper_autonomy.sqlite3"
    assert (
        paper_autonomy_transition(
            config_path=_PAPER_CONFIG,
            database_path=database,
            action="enable",
            reason="   ",
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert "reason" in payload["error"]
    assert SQLitePaperAutonomyRepository(database).recent_events(5) == ()

    # clear-kill against a switch that is not latched: refused, non-zero, and
    # with no audit entry for a kill that never happened.
    assert (
        paper_autonomy_transition(
            config_path=_PAPER_CONFIG,
            database_path=database,
            action="clear-kill",
            reason="release a latch that is not set",
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert SQLitePaperAutonomyRepository(database).recent_events(5) == ()

    for verb in _OPERATOR_TRANSITIONS:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["paper-autonomy", verb])
