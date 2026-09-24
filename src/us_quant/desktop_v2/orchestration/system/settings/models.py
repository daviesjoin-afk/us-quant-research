"""Immutable contracts for the Settings capability's desktop sequencing.

Two value objects, both frozen, neither able to call a service: the *decision*
one credential action came to, and that decision together with the payload it
authorised.  They exist so the rules themselves (``queries.py``) are pure
functions of immutable input -- a credential save that read a widget, a store or
a clock could not be tested without a window, and the four outcomes below are
exactly what the operator sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CredentialSaveOutcome(str, Enum):
    """What a Save-credentials click should do, decided before any write."""

    #: The provider keeps no API key here (IBKR): explain and write nothing.
    NOT_REQUIRED = "not_required"

    #: Every input was blank: this is "no change", not a failed save.
    NO_CHANGE = "no_change"

    #: Only one half of a two-part credential was supplied: refuse, write nothing.
    INCOMPLETE = "incomplete"

    #: A complete credential: write it through the credential service.
    SAVE = "save"


@dataclass(frozen=True, slots=True)
class CredentialSavePlan:
    """One Save-credentials click, resolved into an outcome and its payload.

    ``api_key`` / ``api_secret`` are the *trimmed* values, and they are carried
    on the plan rather than re-read from the draft so the write cannot disagree
    with the decision that authorised it -- the half-filled Alpaca case is
    refused by the same object that would have been written.
    """

    provider: str
    outcome: CredentialSaveOutcome
    api_key: str = ""
    api_secret: str = ""


__all__ = ["CredentialSaveOutcome", "CredentialSavePlan"]
