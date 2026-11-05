"""Risks and opportunities (D-134, ISO 17025 §8.5): a register, each row with an append-only list of entries.

A risk is scored likelihood × severity (each 1–5); the profile's bands say what is low, medium or high
(change-controlled, as every threshold). An opportunity is not scored: it is pursued or not. Entries:

    assessed (a risk re-scored) · action planned (what, who, by when) · action done (which one)
    · reviewed (and the next review date) · closed (a manager, with no action open) · note

Flagged: a high risk with no action planned since it was last scored, and anything past its review date.
"""

from datetime import date
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed


class RiskKind(IntEnum):
    RISK = 1
    OPPORTUNITY = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()


class RiskSource(IntEnum):
    PROCESS = 1
    EQUIPMENT = 2
    IMPARTIALITY = 3
    CUSTOMER = 4
    IT = 5
    OTHER = 6

    @property
    def label(self) -> str:
        return "IT" if self == RiskSource.IT else self.name.capitalize()


class RiskBand(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def label(self) -> str:
        return self.name.lower()


class RiskPolicy(Frozen):
    """The profile's score bands (D-134): a score from `medium_from` is medium, from `high_from` high."""

    medium_from: int = Field(default=5, ge=2, le=25)
    high_from: int = Field(default=10, ge=2, le=25)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.medium_from >= self.high_from:
            raise ValueError("medium starts below high")
        return self

    def band(self, score: int) -> RiskBand:
        if score >= self.high_from:
            return RiskBand.HIGH
        return RiskBand.MEDIUM if score >= self.medium_from else RiskBand.LOW


Score = Field(default=None, ge=1, le=5)


class RiskEntryKind(IntEnum):
    ASSESSED = 1
    ACTION_PLANNED = 2
    ACTION_DONE = 3
    REVIEWED = 4
    CLOSED = 5
    NOTE = 6

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


class RiskEntry(Frozen):
    kind: RiskEntryKind
    note: str = Field(default="", max_length=2000)
    likelihood: int | None = Score  # ASSESSED
    severity: int | None = Score
    action: str = Field(default="", max_length=2000)  # ACTION_PLANNED
    owner: str = Field(default="", max_length=100)
    due: date | None = None
    action_no: int | None = Field(default=None, ge=1)  # ACTION_DONE: which planned action, 1 first
    review_by: date | None = None  # REVIEWED (required), ASSESSED (optional): the next review
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        k = self.kind
        if (k == RiskEntryKind.ASSESSED) != (self.likelihood is not None and self.severity is not None):
            raise ValueError("an assessment gives the likelihood and the severity, and only it does")
        if k != RiskEntryKind.ASSESSED and (self.likelihood is not None or self.severity is not None):
            raise ValueError("only an assessment scores")
        planned = bool(self.action.strip() or self.owner.strip() or self.due)
        if k == RiskEntryKind.ACTION_PLANNED:
            if not (self.action.strip() and self.owner.strip()):
                raise ValueError("an action says what is done and who owns it")
        elif planned:
            raise ValueError("action, owner and due belong to a planned action")
        if (k == RiskEntryKind.ACTION_DONE) != (self.action_no is not None):
            raise ValueError("an action done names which action, and only it does")
        if k == RiskEntryKind.REVIEWED and self.review_by is None:
            raise ValueError("a review sets the next review date")
        if self.review_by is not None and k not in (RiskEntryKind.REVIEWED, RiskEntryKind.ASSESSED):
            raise ValueError("only a review or an assessment sets the next review date")
        if k in (RiskEntryKind.REVIEWED, RiskEntryKind.CLOSED, RiskEntryKind.NOTE) and not self.note.strip():
            raise ValueError("write the note")
        return self


class Action(Frozen):
    no: int
    planned: RiskEntry
    done: RiskEntry | None = None


class Risk(Frozen):
    id: int | None = None
    kind: RiskKind = RiskKind.RISK
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    source: RiskSource
    owner: str = Field(min_length=1, max_length=100)
    likelihood: int | None = Score  # as first scored; a risk only
    severity: int | None = Score
    review_by: date
    raised_by: QualityActor
    raised_at: AwareDatetime | None = None
    entries: tuple[RiskEntry, ...] = ()

    @model_validator(mode="after")
    def _scored_if_a_risk(self) -> Self:
        scored = self.likelihood is not None and self.severity is not None
        if self.kind == RiskKind.RISK and not scored:
            raise ValueError("a risk is scored: give the likelihood and the severity")
        if self.kind == RiskKind.OPPORTUNITY and (self.likelihood is not None or self.severity is not None):
            raise ValueError("an opportunity is not scored")
        return self

    @property
    def number(self) -> str:
        prefix = "R" if self.kind == RiskKind.RISK else "O"
        return f"{prefix}-{self.id:03d}" if self.id is not None else f"{prefix}-new"

    def __str__(self) -> str:
        return f"{self.number} {self.title}"

    def _last(self, kind: RiskEntryKind) -> RiskEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == kind), None)

    @property
    def score(self) -> int | None:
        """Likelihood × severity as last scored; None for an opportunity."""
        last = self._last(RiskEntryKind.ASSESSED)
        if last is not None:
            return last.likelihood * last.severity
        return None if self.likelihood is None else self.likelihood * self.severity

    @property
    def scored(self) -> tuple[int, int] | None:
        last = self._last(RiskEntryKind.ASSESSED)
        if last is not None:
            return last.likelihood, last.severity
        return None if self.likelihood is None else (self.likelihood, self.severity)

    def band(self, policy: RiskPolicy) -> RiskBand | None:
        return None if self.score is None else policy.band(self.score)

    @property
    def review_due(self) -> date:
        for entry in reversed(self.entries):
            if entry.review_by is not None:
                return entry.review_by
        return self.review_by

    @property
    def closed(self) -> bool:
        return self._last(RiskEntryKind.CLOSED) is not None

    @property
    def actions(self) -> tuple[Action, ...]:
        planned = [e for e in self.entries if e.kind == RiskEntryKind.ACTION_PLANNED]
        done = {e.action_no: e for e in self.entries if e.kind == RiskEntryKind.ACTION_DONE}
        return tuple(Action(no=n, planned=e, done=done.get(n)) for n, e in enumerate(planned, start=1))

    @property
    def open_actions(self) -> tuple[Action, ...]:
        return tuple(a for a in self.actions if a.done is None)

    def flags(self, today: date, policy: RiskPolicy) -> tuple[str, ...]:
        if self.closed:
            return ()
        found = []
        if self.band(policy) == RiskBand.HIGH:
            last_scored = max(
                (n for n, e in enumerate(self.entries) if e.kind == RiskEntryKind.ASSESSED), default=-1
            )
            if not any(e.kind == RiskEntryKind.ACTION_PLANNED for e in self.entries[last_scored + 1 :]):
                found.append("high, with no action planned since it was scored")
        if self.review_due < today:
            found.append(f"review overdue since {self.review_due}")
        return tuple(found)

    def check(self, entry: RiskEntry) -> None:
        if self.closed:
            raise NotAllowed(f"{self.number} is closed")
        if entry.kind == RiskEntryKind.ASSESSED and self.kind == RiskKind.OPPORTUNITY:
            raise NotAllowed("an opportunity is not scored")
        if entry.kind == RiskEntryKind.ACTION_DONE:
            action = next((a for a in self.actions if a.no == entry.action_no), None)
            if action is None:
                raise NotAllowed(f"{self.number} has no action {entry.action_no}")
            if action.done is not None:
                raise NotAllowed(
                    f"action {entry.action_no} was done on {action.done.at or 'an earlier entry'}"
                )
        if entry.kind == RiskEntryKind.CLOSED:
            if not entry.by.is_manager:
                raise NotAllowed("a quality manager closes a risk")
            if self.open_actions:
                raise NotAllowed(f"{self.number} has {len(self.open_actions)} action(s) still open")
