"""Reference materials (D-127, ISO 17025 §6.5): the purchased lots the lab makes its standards from, each
with its certificate. slim-prep reads them through the host (its `StockLookup` port, P-28) instead of
typing a stock in; a lot is usable through its effective expiry: the earlier of the certificate's expiry
and, once opened, the opened day plus its shelf life once opened.
"""

from datetime import date, timedelta
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Dec, Frozen, NotAllowed


class LotEntryKind(IntEnum):
    OPENED = 1
    WITHDRAWN = 2  # no longer to be used (contaminated, recalled, used up)
    NOTE = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class LotEntry(Frozen):
    kind: LotEntryKind
    on: date
    note: str = Field(default="", max_length=2000)
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _reason(self) -> Self:
        if self.kind != LotEntryKind.OPENED and not self.note.strip():
            raise ValueError("write the reason")
        return self


class ReferenceLot(Frozen):
    id: int | None = None
    name: str = Field(min_length=1, max_length=100)  # e.g. "Multi-element stock A"
    lot: str = Field(min_length=1, max_length=50)
    supplier: str = Field(min_length=1, max_length=200)
    certificate: str = Field(min_length=1, max_length=100)  # the certificate's number
    traceability: str = Field(default="", max_length=500)  # e.g. "NIST SRM 3114"
    value: Dec = Field(gt=0)  # the certified concentration of each element
    unit: str = Field(min_length=1, max_length=20)  # as slim-prep's units: ppm, ppb, ppt
    uncertainty: Dec | None = Field(default=None, gt=0)  # expanded, same unit
    elements: tuple[str, ...] = Field(min_length=1)
    expires: date  # usable through this day
    shelf_life_opened_days: int | None = Field(default=None, ge=1, le=3650)
    registered_by: QualityActor
    registered_at: AwareDatetime | None = None
    entries: tuple[LotEntry, ...] = ()

    @model_validator(mode="after")
    def _elements(self) -> Self:
        if len(set(self.elements)) != len(self.elements):
            raise ValueError("an element is named twice")
        return self

    def __str__(self) -> str:
        return f"{self.name} lot {self.lot}"

    def _first(self, kind: LotEntryKind) -> date | None:
        return next((e.on for e in self.entries if e.kind == kind), None)

    @property
    def opened_on(self) -> date | None:
        return self._first(LotEntryKind.OPENED)

    @property
    def withdrawn_on(self) -> date | None:
        return self._first(LotEntryKind.WITHDRAWN)

    @property
    def effective_expiry(self) -> date:
        if self.opened_on is not None and self.shelf_life_opened_days is not None:
            return min(self.expires, self.opened_on + timedelta(days=self.shelf_life_opened_days))
        return self.expires

    def unusable_on(self, day: date) -> str | None:
        """Why the lot can't be used on `day`, or None."""
        if self.withdrawn_on is not None and self.withdrawn_on <= day:
            return f"withdrawn on {self.withdrawn_on}"
        if day > self.effective_expiry:
            return f"expired on {self.effective_expiry}"
        return None

    def check(self, entry: LotEntry) -> None:
        if self.withdrawn_on is not None and entry.kind != LotEntryKind.NOTE:
            raise NotAllowed(f"{self} was withdrawn")
        if entry.kind == LotEntryKind.OPENED and self.opened_on is not None:
            raise NotAllowed(f"{self} was opened on {self.opened_on}")
