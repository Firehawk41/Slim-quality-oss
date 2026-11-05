"""The management review (D-136, ISO 17025 §8.9). The pack is the readiness view and the §8.9.2 inputs the
records hold, for a period, frozen when a quality manager signs it with the review's outputs (§8.9.3):
what was shown stays what was shown, however the records move on. Inputs the records can't hold
(external issues, objectives, external assessments, feedback, resources...) are the manager's own words.
Append-only: a new review is a new row.
"""

from datetime import date
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed
from slim_quality.domain.readiness import ClauseReadiness

# §8.9.2's inputs, a…o (paraphrased): those the records fill, and those the manager writes.
INPUTS: dict[str, str] = {
    "a": "Changes in internal and external issues",
    "b": "Fulfilment of objectives",
    "c": "Suitability of policies and procedures",
    "d": "Status of actions from previous reviews",
    "e": "Outcome of recent internal audits",
    "f": "Corrective actions",
    "g": "Assessments by external bodies",
    "h": "Changes in the volume and type of work",
    "i": "Customer and personnel feedback",
    "j": "Complaints",
    "k": "Effectiveness of implemented improvements",
    "l": "Adequacy of resources",
    "m": "Results of risk identification",
    "n": "Outcomes of the assurance of the validity of results",
    "o": "Other relevant factors: monitoring and training",
}
FROM_RECORDS = ("d", "e", "f", "h", "j", "m", "n", "o")
WRITTEN = tuple(k for k in INPUTS if k not in FROM_RECORDS)


class OutputKind(IntEnum):
    """§8.9.3's outputs."""

    EFFECTIVENESS = 1  # the management system and its processes
    IMPROVEMENT = 2  # of the laboratory activities
    RESOURCES = 3
    CHANGE = 4  # any need for change

    @property
    def label(self) -> str:
        return {
            1: "Effectiveness of the management system",
            2: "Improvement of the laboratory activities",
            3: "Resources needed",
            4: "Need for change",
        }[self]


class ReviewOutput(Frozen):
    kind: OutputKind
    text: str = Field(min_length=1, max_length=2000)
    owner: str = Field(default="", max_length=100)
    due: date | None = None


class PackSection(Frozen):
    letter: str
    title: str
    lines: tuple[str, ...] = ()


class ReviewPack(Frozen):
    period_from: date
    period_to: date
    readiness: tuple[ClauseReadiness, ...]
    sections: tuple[PackSection, ...]  # the inputs from the records

    @model_validator(mode="after")
    def _period(self) -> Self:
        if self.period_from > self.period_to:
            raise ValueError("the period ends before it starts")
        return self


class ManagementReview(Frozen):
    id: int | None = None
    pack: ReviewPack
    written: dict[str, str] = {}  # the inputs the manager writes, by letter
    outputs: tuple[ReviewOutput, ...]
    attendees: str = Field(default="", max_length=1000)
    signed_by: QualityActor
    signed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _inputs(self) -> Self:
        unknown = set(self.written) - set(WRITTEN)
        if unknown:
            raise ValueError(f"not an input the manager writes: {', '.join(sorted(unknown))}")
        return self

    @property
    def number(self) -> str:
        return f"MR-{self.id:03d}" if self.id is not None else "MR-new"

    def sections(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """Every input a…o in order, the records' lines or the manager's words."""
        from_records = {s.letter: s.lines for s in self.pack.sections}
        found = []
        for letter, title in INPUTS.items():
            if letter in from_records:
                found.append((letter, title, from_records[letter]))
            else:
                text = self.written.get(letter, "").strip()
                found.append((letter, title, tuple(text.splitlines()) if text else ()))
        return found


def check_review(review: ManagementReview) -> None:
    if not review.signed_by.is_manager:
        raise NotAllowed("a quality manager signs the management review")
    if not review.outputs:
        raise NotAllowed("record the review's outputs (§8.9.3): at least one decision or action")
