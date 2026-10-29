"""The QC records a host hands over, and how they become series. Generic: any LIMS that knows its
blanks, check standards, CRMs and calibrations can feed slim-quality.

* CHECK / CRM → recovery (measured ÷ expected) per instrument × analyte × conditions × material ×
  level, and the check standard's replicate RSD;
* BLANK → the blank's level per instrument × analyte × conditions;
* the internal standard's recovery, once per QC row (it is the row's, not an analyte's);
* each run's calibration → LOD, BEC and r per instrument × analyte × conditions.
"""

from collections import defaultdict
from collections.abc import Iterable
from decimal import localcontext
from typing import Literal

from pydantic import AwareDatetime, Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Measure
from slim_quality.domain.series import Point, Series, SeriesKey


class QcRecord(Frozen):
    kind: Literal["blank", "check", "crm"]
    ref: str = Field(min_length=1, max_length=100)  # the host's id for it
    instrument: str
    at: AwareDatetime
    run_code: str
    sequence: int | None = None
    analyte: str
    conditions: str | None = None
    measured: Dec  # what the solution read, after any dilution
    unit: str
    expected: Dec | None = None  # a check's or CRM's
    material: str | None = None
    rsd: Dec | None = None
    istd_recovery: Dec | None = None
    batch: str | None = None
    analyst: str | None = None
    excluded: bool = False


class CalibrationRecord(Frozen):
    instrument: str
    at: AwareDatetime
    run_code: str
    analyte: str
    conditions: str | None = None
    unit: str
    correlation: Dec | None = None
    lod: Dec | None = None
    bec: Dec | None = None


def to_series(
    records: Iterable[QcRecord], calibrations: Iterable[CalibrationRecord] = ()
) -> tuple[Series, ...]:
    points: dict[SeriesKey, list[Point]] = defaultdict(list)
    units: dict[SeriesKey, str] = {}

    def add(key: SeriesKey, value, unit: str, record, ref: str) -> None:
        point = Point(
            at=record.at,
            value=value,
            run_code=record.run_code,
            sequence=getattr(record, "sequence", None),
            ref=ref,
            batch=getattr(record, "batch", None),
            analyst=getattr(record, "analyst", None),
            excluded=getattr(record, "excluded", False),
        )
        points[key].append(point)
        units[key] = unit

    istd_rows = set()
    for r in records:
        base = dict(instrument=r.instrument, analyte=r.analyte, conditions=r.conditions)
        if r.kind == "blank":
            add(SeriesKey(measure=Measure.BLANK_LEVEL, **base), r.measured, r.unit, r, r.ref)
        elif r.expected is not None and r.expected > 0:
            measure = Measure.CHECK_RECOVERY if r.kind == "check" else Measure.CRM_RECOVERY
            qc = dict(base, material=r.material, level=r.expected)
            with localcontext() as context:
                context.prec = 28
                recovery = r.measured / r.expected
            add(SeriesKey(measure=measure, **qc), recovery, "", r, r.ref)
            if r.kind == "check" and r.rsd is not None:
                add(SeriesKey(measure=Measure.CHECK_RSD, **qc), r.rsd, "%", r, r.ref)
        row = (r.instrument, r.run_code, r.sequence)
        if r.istd_recovery is not None and r.sequence is not None and row not in istd_rows:
            istd_rows.add(row)
            add(
                SeriesKey(measure=Measure.ISTD_RECOVERY, instrument=r.instrument),
                r.istd_recovery,
                "%",
                r,
                r.ref,
            )
    for c in calibrations:
        base = dict(instrument=c.instrument, analyte=c.analyte, conditions=c.conditions)
        ref = f"cal:{c.run_code}:{c.analyte}"
        for measure, value, unit in (
            (Measure.CAL_LOD, c.lod, c.unit),
            (Measure.CAL_BEC, c.bec, c.unit),
            (Measure.CAL_R, c.correlation, ""),
        ):
            if value is not None:
                add(SeriesKey(measure=measure, **base), value, unit, c, ref)
    return tuple(
        Series(key=key, points=tuple(sorted(found, key=lambda p: (p.at, p.sequence or 0))), unit=units[key])
        for key, found in sorted(points.items(), key=lambda item: item[0].label)
    )
