"""Competence: who is authorised for what, and who acted outside it (D-132, ISO 17025 §6.2.5, §6.2.6).

The register: a quality manager authorises a person for an act (prep, entering readings, review, release,
signing a report) on a method (an analysis code, or every method), from a day, optionally until one,
with the evidence (training, a witnessed run). Append-only: an authorisation is withdrawn by an entry,
never edited.

The check is after the fact (finding, not blocking): the host hands over every act its modules recorded
(who, which act, which method, which day); slim-quality says which ones no authorisation covered *on that
day*. An authorisation recorded after the act it covers is a finding of its own (backdated): the evidence
may be fine, but the record came later. A report's signature has no method: only an authorisation for
every method covers it.
"""

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed


class Act(IntEnum):
    PREP = 1  # a prep batch (slim-prep)
    ENTER = 2  # entering readings: an import (slim-data)
    REVIEW = 3  # review, accept or record an exception (slim-data)
    RELEASE = 4  # release (slim-data)
    SIGN = 5  # sign and issue a report (D-122)

    @property
    def label(self) -> str:
        return {1: "Prep", 2: "Enter readings", 3: "Review", 4: "Release", 5: "Sign reports"}[self]


class Person(Frozen):
    """A member of staff as the host knows them: its id (what the modules record) and a name."""

    id: str = Field(min_length=1, max_length=100)
    display: str = Field(min_length=1, max_length=200)


class AuthorisationEntryKind(IntEnum):
    WITHDRAWN = 1
    NOTE = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()


class AuthorisationEntry(Frozen):
    kind: AuthorisationEntryKind
    on: date
    note: str = Field(min_length=1, max_length=2000)
    by: QualityActor
    at: AwareDatetime | None = None


class Authorisation(Frozen):
    id: int | None = None
    person: Person
    act: Act
    method: str | None = Field(default=None, min_length=1, max_length=50)  # None: every method
    valid_from: date
    valid_until: date | None = None  # through this day
    evidence: str = Field(min_length=1, max_length=2000)
    granted_by: QualityActor
    granted_at: AwareDatetime | None = None
    entries: tuple[AuthorisationEntry, ...] = ()

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("it ends before it starts")
        if not self.evidence.strip():
            raise ValueError("give the evidence (training, a witnessed run)")
        return self

    @property
    def method_label(self) -> str:
        return self.method or "every method"

    def __str__(self) -> str:
        return f"{self.person.display}: {self.act.label} · {self.method_label}"

    @property
    def withdrawn_on(self) -> date | None:
        return next((e.on for e in self.entries if e.kind == AuthorisationEntryKind.WITHDRAWN), None)

    @property
    def last_day(self) -> date | None:
        """The last day it covers (None: open-ended)."""
        ends = [] if self.valid_until is None else [self.valid_until]
        if self.withdrawn_on is not None:  # withdrawn on a day: not valid that day
            ends.append(self.withdrawn_on - timedelta(days=1))
        return min(ends) if ends else None

    def matches(self, person_id: str, act: Act, method: str | None) -> bool:
        if self.person.id != person_id or self.act != act:
            return False
        return self.method is None or self.method == method

    def valid_on(self, day: date) -> bool:
        last = self.last_day
        return self.valid_from <= day and (last is None or day <= last)

    def check(self, entry: AuthorisationEntry) -> None:
        if not entry.by.is_manager:
            raise NotAllowed("a quality manager keeps the authorisations")
        if self.withdrawn_on is not None and entry.kind == AuthorisationEntryKind.WITHDRAWN:
            raise NotAllowed(f"{self} was withdrawn on {self.withdrawn_on}")


def check_grant(authorisation: Authorisation) -> None:
    if not authorisation.granted_by.is_manager:
        raise NotAllowed("a quality manager grants authorisations")
    if authorisation.granted_by.id == authorisation.person.id:
        raise NotAllowed("nobody authorises themselves")


class ActRecord(Frozen):
    """One act a module recorded, as the host hands it over."""

    person: Person
    act: Act
    method: str | None = Field(default=None, max_length=50)  # the analysis code; None for a signature
    on: date  # the lab's day
    ref: str = Field(min_length=1, max_length=100)  # the host's id for it, e.g. "result:12:v1"
    label: str = Field(min_length=1, max_length=200)  # e.g. "TR00012-01 Cu"
    tr_number: str | None = Field(default=None, max_length=20)


class Finding(Frozen):
    act: ActRecord
    reason: str
    backdated: bool = False  # covered, but only by an authorisation recorded after the act


def outside_authorisation(
    acts: Iterable[ActRecord],
    register: Iterable[Authorisation],
    day_of: Callable[[datetime], date] = datetime.date,
) -> tuple[Finding, ...]:
    """The acts no authorisation covered on their day, oldest first. `day_of` turns the time a grant was
    recorded into the lab's day (the host's clock), so it compares with the acts' days."""
    register = tuple(register)
    found = []
    for act in sorted(acts, key=lambda a: (a.on, a.ref)):
        mine = [r for r in register if r.matches(act.person.id, act.act, act.method)]
        valid = [r for r in mine if r.valid_on(act.on)]
        on_time = [r for r in valid if r.granted_at is None or day_of(r.granted_at) <= act.on]
        if on_time:
            continue
        if valid:
            first = min(day_of(r.granted_at) for r in valid if r.granted_at is not None)
            found.append(
                Finding(
                    act=act, reason=f"covered only by an authorisation recorded on {first}", backdated=True
                )
            )
        else:
            found.append(Finding(act=act, reason=_why(act, mine)))
    return tuple(found)


def _why(act: ActRecord, mine: list[Authorisation]) -> str:
    what = act.act.label.lower() + (f" for {act.method}" if act.method else "")
    if not mine:
        return f"{act.person.display} holds no authorisation to {what}"
    later = [r.valid_from for r in mine if r.valid_from > act.on]
    if later:
        return f"authorised to {what} only from {min(later)}"
    last = max(r.last_day for r in mine if r.last_day is not None)
    return f"the authorisation to {what} ended on {last}"
