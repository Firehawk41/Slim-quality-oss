"""Nonconformities (D-114, ISO 17025 §7.10, §8.7): raised by hand or from a flag, an event or an audit;
an append-only list of entries; the state follows the entries, never set by hand.

    open → assessed (a manager: a decision per released result the window touched)
         → action taken (root cause, action, owner, due)
         → effective (or not: back to action taken, a new action is needed)
         → closed (a manager, only once effective)

A note can be added at any time before it is closed. A re-assessment is allowed until it is closed and
doesn't move the state back. slim-quality never changes a result: "amend" is a decision recorded here,
done in slim-data as a correction.
"""

from datetime import date
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen
from slim_quality.domain.changes import NotAllowed


class NcSource(IntEnum):
    MANUAL = 1
    FLAG = 2
    EVENT = 3
    AUDIT = 4

    @property
    def label(self) -> str:
        return {1: "Raised by hand", 2: "From a flag", 3: "From an event", 4: "From an audit"}[self]


class NcState(IntEnum):
    OPEN = 1
    ASSESSED = 2
    ACTION_TAKEN = 3
    EFFECTIVE = 4
    CLOSED = 5

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


class EntryKind(IntEnum):
    ASSESSED = 1
    ACTION = 2
    EFFECTIVENESS = 3
    CLOSED = 4
    NOTE = 5


class ImpactDecision(IntEnum):
    NO_ACTION = 1
    NOTIFY_CUSTOMER = 2
    AMEND_REPORT = 3

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


class ImpactLine(Frozen):
    """One released result the window touched, and what the manager decided for it."""

    ref: str = Field(min_length=1, max_length=100)  # the host's id, e.g. "TR00008-01|1|Cu|2"
    label: str = Field(min_length=1, max_length=300)  # as shown when decided
    decision: ImpactDecision
    note: str = Field(default="", max_length=500)


class NcEntry(Frozen):
    kind: EntryKind
    note: str = Field(default="", max_length=4000)
    impact: tuple[ImpactLine, ...] = ()  # ASSESSED
    root_cause: str = Field(default="", max_length=2000)  # ACTION
    action: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=100)
    due: date | None = None
    effective: bool | None = None  # EFFECTIVENESS
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        action_fields = bool(self.root_cause or self.action or self.owner or self.due)
        if self.kind == EntryKind.ACTION:
            if not (self.root_cause.strip() and self.action.strip() and self.owner.strip()):
                raise ValueError("an action needs its root cause, the action and an owner")
        elif action_fields:
            raise ValueError("root cause, action, owner and due belong to an action")
        if (self.kind == EntryKind.EFFECTIVENESS) != (self.effective is not None):
            raise ValueError("an effectiveness check says effective or not, and only it does")
        if self.impact and self.kind != EntryKind.ASSESSED:
            raise ValueError("impact decisions belong to an assessment")
        if self.kind in (EntryKind.NOTE, EntryKind.CLOSED) and not self.note.strip():
            raise ValueError("write the note")
        if self.kind == EntryKind.ASSESSED and not self.impact and not self.note.strip():
            raise ValueError("an assessment with no results touched says why in a note")
        refs = [line.ref for line in self.impact]
        if len(set(refs)) != len(refs):
            raise ValueError("a result is decided twice")
        return self


class Nonconformity(Frozen):
    id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    source: NcSource = NcSource.MANUAL
    source_ref: str = Field(default="", max_length=200)
    instrument: str | None = Field(default=None, max_length=100)
    analyte: str | None = Field(default=None, max_length=50)  # None: every analyte on the instrument
    window_from: AwareDatetime | None = None
    window_to: AwareDatetime | None = None
    raised_by: QualityActor
    raised_at: AwareDatetime | None = None
    entries: tuple[NcEntry, ...] = ()

    @model_validator(mode="after")
    def _window(self) -> Self:
        if (self.window_from is None) != (self.window_to is None):
            raise ValueError("a window has both ends, or none")
        if self.window_from is not None and self.window_from > self.window_to:
            raise ValueError("the window ends before it starts")
        if self.window_from is not None and not self.instrument:
            raise ValueError("a window is on an instrument")
        if self.analyte and not self.instrument:
            raise ValueError("an analyte is on an instrument")
        return self

    @property
    def number(self) -> str:
        return f"NC-{self.id:04d}" if self.id is not None else "NC-new"

    @property
    def state(self) -> NcState:
        state = NcState.OPEN
        for entry in self.entries:
            if entry.kind == EntryKind.ASSESSED:
                state = max(state, NcState.ASSESSED)
            elif entry.kind == EntryKind.ACTION:
                state = NcState.ACTION_TAKEN
            elif entry.kind == EntryKind.EFFECTIVENESS:
                state = NcState.EFFECTIVE if entry.effective else NcState.ACTION_TAKEN
            elif entry.kind == EntryKind.CLOSED:
                state = NcState.CLOSED
        return state

    @property
    def assessment(self) -> NcEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == EntryKind.ASSESSED), None)

    @property
    def latest_action(self) -> NcEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == EntryKind.ACTION), None)

    def check(self, entry: NcEntry) -> None:
        """Refuses an entry the facts don't allow yet (or any more)."""
        self.allows(entry.kind, entry.by)

    def allows(self, kind: EntryKind, by: QualityActor) -> None:
        """Whether `by` may add an entry of this kind now; ask before reading a form, so a refusal is
        a refusal, not a complaint about the form."""
        state = self.state
        if state == NcState.CLOSED:
            raise NotAllowed(f"{self.number} is closed")
        if kind in (EntryKind.ASSESSED, EntryKind.CLOSED) and not by.is_manager:
            raise NotAllowed("a quality manager assesses and closes a nonconformity")
        if kind == EntryKind.ACTION and state == NcState.OPEN:
            raise NotAllowed("assess the impact before the action")
        if kind == EntryKind.EFFECTIVENESS and state != NcState.ACTION_TAKEN:
            raise NotAllowed("an effectiveness check follows an action")
        if kind == EntryKind.CLOSED and state != NcState.EFFECTIVE:
            raise NotAllowed("close it once the action is shown effective")
