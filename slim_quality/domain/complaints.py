"""Complaints (D-130, ISO 17025 §7.9). Raised by a customer (from their request or report) or logged by
staff on their behalf; an append-only list of entries; the state follows them:

    received → acknowledged → investigated (valid, or not about the lab's work: §7.9.3)
             → outcome approved → closed

Progress notes and internal notes may be added until it is closed. The customer sees the entries meant
for them (acknowledgement, progress marked for them, the outcome, the closure): §7.9.5 without email.

The outcome is approved by a quality manager with no hand in the request's lab work (§7.9.6): the host
works out who had one (entered, reviewed or released its results, prepped its samples, signed its
reports) and passes their ids; slim-quality refuses anyone among them.
"""

from collections.abc import Collection
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed


class ComplaintState(IntEnum):
    RECEIVED = 1
    ACKNOWLEDGED = 2
    INVESTIGATED = 3
    OUTCOME_APPROVED = 4
    CLOSED = 5

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


class ComplaintEntryKind(IntEnum):
    ACKNOWLEDGED = 1
    PROGRESS = 2
    INVESTIGATED = 3
    OUTCOME = 4
    CLOSED = 5
    NOTE = 6  # internal

    @property
    def label(self) -> str:
        return {1: "Acknowledged", 2: "Progress", 3: "Investigated", 4: "Outcome", 5: "Closed", 6: "Note"}[
            self
        ]


ALWAYS_TO_CUSTOMER = (ComplaintEntryKind.ACKNOWLEDGED, ComplaintEntryKind.OUTCOME, ComplaintEntryKind.CLOSED)
NEVER_TO_CUSTOMER = (ComplaintEntryKind.INVESTIGATED, ComplaintEntryKind.NOTE)


class Party(Frozen):
    """Whoever raised it: a customer user, or the staff member who logged it."""

    id: str = Field(min_length=1, max_length=100)
    display: str = Field(min_length=1, max_length=200)


class ComplaintEntry(Frozen):
    kind: ComplaintEntryKind
    note: str = Field(min_length=1, max_length=4000)
    to_customer: bool = False
    valid: bool | None = None  # INVESTIGATED: is it about the lab's work?
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        if (self.kind == ComplaintEntryKind.INVESTIGATED) != (self.valid is not None):
            raise ValueError("an investigation says whether it is valid, and only it does")
        if self.kind in ALWAYS_TO_CUSTOMER and not self.to_customer:
            raise ValueError(f"{self.kind.label.lower()} goes to the customer")
        if self.kind in NEVER_TO_CUSTOMER and self.to_customer:
            raise ValueError(f"{self.kind.label.lower()} is internal")
        return self


class Complaint(Frozen):
    id: int | None = None
    tr_number: str = Field(min_length=1, max_length=20)
    customer_id: str = Field(min_length=1, max_length=50)  # the host's customer, for its scope
    issue_id: int | None = None  # the issued report it is about, if any
    text: str = Field(min_length=1, max_length=4000)
    raised_by: Party
    by_staff: bool = False  # logged by staff on the customer's behalf
    raised_at: AwareDatetime | None = None
    entries: tuple[ComplaintEntry, ...] = ()

    @property
    def number(self) -> str:
        return f"CP-{self.id:04d}" if self.id is not None else "CP-new"

    @property
    def state(self) -> ComplaintState:
        state = ComplaintState.RECEIVED
        order = {
            ComplaintEntryKind.ACKNOWLEDGED: ComplaintState.ACKNOWLEDGED,
            ComplaintEntryKind.INVESTIGATED: ComplaintState.INVESTIGATED,
            ComplaintEntryKind.OUTCOME: ComplaintState.OUTCOME_APPROVED,
            ComplaintEntryKind.CLOSED: ComplaintState.CLOSED,
        }
        for entry in self.entries:
            state = max(state, order.get(entry.kind, state))
        return state

    @property
    def investigation(self) -> ComplaintEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == ComplaintEntryKind.INVESTIGATED), None)

    @property
    def for_customer(self) -> tuple[ComplaintEntry, ...]:
        return tuple(e for e in self.entries if e.to_customer)

    def check(self, entry: ComplaintEntry, involved: Collection[str] = ()) -> None:
        """Refuses an entry the facts don't allow. `involved`: the ids of whoever had a hand in the
        request's lab work (the host's)."""
        self.allows(entry.kind, entry.by, involved)

    def allows(self, kind: ComplaintEntryKind, by: QualityActor, involved: Collection[str] = ()) -> None:
        state = self.state
        if state == ComplaintState.CLOSED:
            raise NotAllowed(f"{self.number} is closed")
        if kind == ComplaintEntryKind.ACKNOWLEDGED and state != ComplaintState.RECEIVED:
            raise NotAllowed(f"{self.number} was acknowledged")
        if kind == ComplaintEntryKind.INVESTIGATED and state not in (
            ComplaintState.ACKNOWLEDGED,
            ComplaintState.INVESTIGATED,
        ):
            raise NotAllowed("acknowledge the complaint before recording the investigation")
        if kind == ComplaintEntryKind.OUTCOME:
            if state != ComplaintState.INVESTIGATED:
                raise NotAllowed("investigate before approving the outcome")
            if not by.is_manager:
                raise NotAllowed("a quality manager approves the outcome")
            if by.id in involved:
                raise NotAllowed(
                    f"{by.display} had a hand in {self.tr_number}'s lab work: someone who didn't approves "
                    "the outcome (ISO 17025 §7.9.6)"
                )
        if kind == ComplaintEntryKind.CLOSED and state != ComplaintState.OUTCOME_APPROVED:
            raise NotAllowed("close it once the outcome is approved and sent")
