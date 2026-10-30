"""What the quality team records by hand: events on an instrument, notes on a point, signed QC reviews."""

from datetime import date
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import EventKind


class QualityEvent(Frozen):
    """Something done to an instrument (or a new lot): drawn on its charts, a reason to re-baseline."""

    id: int | None = None
    kind: EventKind
    instrument: str = Field(min_length=1, max_length=100)
    analyte: str | None = Field(default=None, max_length=50)  # None: the whole instrument
    at: AwareDatetime
    note: str = Field(min_length=1, max_length=1000)
    by: QualityActor


class PointNote(Frozen):
    id: int | None = None
    series: str = Field(min_length=1, max_length=400)  # the series' slug
    ref: str = Field(min_length=1, max_length=100)  # the point's
    note: str = Field(min_length=1, max_length=1000)
    by: QualityActor
    at: AwareDatetime | None = None


class QcReview(Frozen):
    """The periodic QC review (ISO 17025 §7.7): the manager looked at the period's charts and signed."""

    id: int | None = None
    period_from: date
    period_to: date
    summary: dict[str, int] = {}  # flags by severity name, as the manager saw them
    comment: str = Field(min_length=1, max_length=4000)
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.period_from > self.period_to:
            raise ValueError("the period ends before it starts")
        if not self.by.is_manager:
            raise ValueError("only a quality manager signs a QC review")
        return self
