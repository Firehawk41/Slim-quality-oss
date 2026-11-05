"""Internal audits (D-135, ISO 17025 §8.8): a programme per year, one planned audit per clause or area, each
with an auditor independent of what they audit; recorded as a checklist, line by line.

    planned → (rescheduled …) → conducted (the checklist) | cancelled

A line is conforming, an observation, or a finding; a finding offers an NC in one click (as D-114). Who
counts as involved in a clause is worked out by whoever knows (the host from the acts its modules
recorded; slim-quality from who kept its own records) and passed in: the auditor must not be among them,
when planned and again when the audit is recorded (§8.8.2 b, "auditors shall not audit their own work").
"""

from collections.abc import Collection
from datetime import date
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed
from slim_quality.domain.clauses import check_clause, clause_label
from slim_quality.domain.competence import Person


class AuditState(IntEnum):
    PLANNED = 1
    CONDUCTED = 2
    CANCELLED = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class LineOutcome(IntEnum):
    CONFORMING = 1
    OBSERVATION = 2
    FINDING = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class ChecklistLine(Frozen):
    clause: str = Field(min_length=1, max_length=20)  # the clause or a sub-clause of it, e.g. "7.8.2"
    checked: str = Field(min_length=1, max_length=1000)  # what was looked at
    outcome: LineOutcome
    evidence: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if self.outcome != LineOutcome.CONFORMING and not self.evidence.strip():
            raise ValueError(f"an {self.outcome.label.lower()} gives its evidence")
        return self


class AuditEntryKind(IntEnum):
    RESCHEDULED = 1
    CANCELLED = 2
    CONDUCTED = 3
    NOTE = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()


class AuditEntry(Frozen):
    kind: AuditEntryKind
    on: date | None = None  # RESCHEDULED: the new day; CONDUCTED: the day it was done
    lines: tuple[ChecklistLine, ...] = ()  # CONDUCTED
    note: str = Field(default="", max_length=4000)  # CONDUCTED: the summary
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        dated = (AuditEntryKind.RESCHEDULED, AuditEntryKind.CONDUCTED)
        if (self.kind in dated) != (self.on is not None):
            raise ValueError("a reschedule or the audit itself has a day, and only they do")
        if (self.kind == AuditEntryKind.CONDUCTED) != bool(self.lines):
            raise ValueError("the audit is recorded as its checklist, at least one line")
        if self.kind != AuditEntryKind.RESCHEDULED and not self.note.strip():
            raise ValueError(
                "write the note" if self.kind != AuditEntryKind.CONDUCTED else "write the summary"
            )
        return self


class InternalAudit(Frozen):
    id: int | None = None
    clause: str
    area: str = Field(default="", max_length=200)  # e.g. "the metals bench", optional
    planned_for: date
    auditor: Person
    planned_by: QualityActor
    planned_at: AwareDatetime | None = None
    entries: tuple[AuditEntry, ...] = ()

    @field_validator("clause")
    @classmethod
    def _known(cls, value: str) -> str:
        return check_clause(value)

    @property
    def number(self) -> str:
        return f"IA-{self.planned_for.year}-{self.id:03d}" if self.id is not None else "IA-new"

    @property
    def year(self) -> int:
        return self.planned_for.year

    @property
    def label(self) -> str:
        return clause_label(self.clause) + (f" ({self.area})" if self.area else "")

    def __str__(self) -> str:
        return f"{self.number} {self.label}"

    def _last(self, kind: AuditEntryKind) -> AuditEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == kind), None)

    @property
    def conducted(self) -> AuditEntry | None:
        return self._last(AuditEntryKind.CONDUCTED)

    @property
    def state(self) -> AuditState:
        if self.conducted is not None:
            return AuditState.CONDUCTED
        if self._last(AuditEntryKind.CANCELLED) is not None:
            return AuditState.CANCELLED
        return AuditState.PLANNED

    @property
    def due(self) -> date:
        moved = self._last(AuditEntryKind.RESCHEDULED)
        return self.planned_for if moved is None else moved.on

    def overdue(self, today: date) -> bool:
        return self.state == AuditState.PLANNED and self.due < today

    @property
    def done_on(self) -> date | None:
        return None if self.conducted is None else self.conducted.on

    def lines_of(self, outcome: LineOutcome) -> tuple[tuple[int, ChecklistLine], ...]:
        """(line number, 1 first, line), for the NC a finding offers."""
        if self.conducted is None:
            return ()
        return tuple(
            (n, line) for n, line in enumerate(self.conducted.lines, start=1) if line.outcome == outcome
        )

    def finding_ref(self, line_no: int) -> str:
        return f"internal-audit:{self.id}:{line_no}"

    def check(self, entry: AuditEntry, involved: Collection[str] = ()) -> None:
        state = self.state
        if state != AuditState.PLANNED and entry.kind != AuditEntryKind.NOTE:
            raise NotAllowed(f"{self.number} was {state.label.lower()}")
        if entry.kind == AuditEntryKind.CANCELLED and not entry.by.is_manager:
            raise NotAllowed("a quality manager cancels a planned audit")
        if entry.kind == AuditEntryKind.CONDUCTED:
            check_independent(self.auditor, self.clause, involved)
            for line in entry.lines:
                if line.clause != self.clause and not line.clause.startswith(self.clause + "."):
                    raise NotAllowed(f"line {line.clause} is outside the audit's clause {self.clause}")


def check_independent(auditor: Person, clause: str, involved: Collection[str]) -> None:
    if auditor.id in involved:
        raise NotAllowed(
            f"{auditor.display} had a hand in {clause_label(clause)} in the past year: an auditor doesn't "
            "audit their own work (ISO 17025 §8.8.2)"
        )


def check_plan(audit: InternalAudit, involved: Collection[str] = ()) -> None:
    if not audit.planned_by.is_manager:
        raise NotAllowed("a quality manager plans the audit programme")
    check_independent(audit.auditor, audit.clause, involved)
