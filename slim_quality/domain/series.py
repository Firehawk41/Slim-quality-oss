"""A series: one thing measured again and again under the same conditions, in time order.

Keyed generically (instrument × analyte × conditions × measure × QC material × level), so a trace
lab, a mining lab and an environmental lab key their series the same way.
"""

from urllib.parse import quote, unquote

from pydantic import AwareDatetime, Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Measure

_SEP = "|"  # quote() escapes it in the parts (it leaves "~" alone)


class SeriesKey(Frozen):
    measure: Measure
    instrument: str = Field(min_length=1, max_length=100)
    analyte: str = Field(default="", max_length=50)  # "" for the internal standard (one per QC row)
    conditions: str | None = Field(default=None, max_length=50)  # e.g. the plasma mode
    material: str | None = Field(default=None, max_length=100)  # the check standard or CRM
    level: Dec | None = None  # its expected value, in the reading's unit

    @property
    def slug(self) -> str:
        """A stable text id, for URLs and the database."""
        parts = (
            str(int(self.measure)),
            self.instrument,
            self.analyte,
            self.conditions or "",
            self.material or "",
            "" if self.level is None else str(self.level),
        )
        return _SEP.join(quote(p, safe="") for p in parts)

    @classmethod
    def from_slug(cls, slug: str) -> "SeriesKey":
        parts = [unquote(p) for p in slug.split(_SEP)]
        if len(parts) != 6:
            raise ValueError(f"not a series id: {slug!r}")
        measure, instrument, analyte, conditions, material, level = parts
        return cls(
            measure=Measure(int(measure)),
            instrument=instrument,
            analyte=analyte,
            conditions=conditions or None,
            material=material or None,
            level=level or None,
        )

    @property
    def label(self) -> str:
        what = " ".join(p for p in (self.analyte, f"({self.conditions})" if self.conditions else "") if p)
        level = f" @ {self.level}" if self.level is not None else ""
        material = f" {self.material}" if self.material else ""
        parts = (self.instrument, what, f"{self.measure.label}{material}{level}")
        return " · ".join(p for p in parts if p)


class Point(Frozen):
    at: AwareDatetime  # when the run was measured
    value: Dec
    run_code: str = Field(min_length=1, max_length=50)
    sequence: int | None = None  # the line in the run (run order)
    ref: str = Field(min_length=1, max_length=100)  # the source's id for it, e.g. "reading:12"
    batch: str | None = None
    analyst: str | None = None
    excluded: bool = False  # an analyst marked it bad: drawn, never judged


class Series(Frozen):
    key: SeriesKey
    points: tuple[Point, ...]  # oldest first
    unit: str = ""  # of the values ("" for a recovery, a fraction)

    @property
    def judged(self) -> tuple[Point, ...]:
        return tuple(p for p in self.points if not p.excluded)
