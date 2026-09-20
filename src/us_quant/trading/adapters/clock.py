"""The one timestamp formatter the execution adapters record with.

Order stores and broker event queues stamp rows with an ISO-8601 UTC string.
That format is load bearing: reconciliation compares stored timestamps, and
databases written by earlier builds must keep reading back.  A second
implementation of "now, in the stored format" would be a second definition of
what the stored text means, so it lives here rather than in each adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """The current instant, in the text format the order store records."""

    return datetime.now(timezone.utc).isoformat()


def to_stored_text(moment: datetime) -> str:
    """One ``datetime``, in that same stored format."""

    return moment.isoformat()


def from_stored_text(value: str | None) -> datetime | None:
    """Read a stored timestamp back; ``None`` when it cannot be parsed.

    Returning ``None`` rather than substituting "now" keeps an unreadable
    timestamp distinguishable from a real one: a caller that fabricated the
    current time here would hide a corrupt row behind a plausible value.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None