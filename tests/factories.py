"""Test helpers: points from plain numbers, actors, keys."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.enums import LimitMethod, Measure, QualityRole
from slim_quality.domain.limits import Limits, Window
from slim_quality.domain.series import Point, Series, SeriesKey
from slim_quality.monitors.base import Track

T0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
MANAGER = QualityActor(id="qm", display="Quinn Manager", role=QualityRole.MANAGER)
OTHER_MANAGER = QualityActor(id="qm2", display="Morgan Manager", role=QualityRole.MANAGER)
USER = QualityActor(id="qu", display="Uma User", role=QualityRole.USER)
UNIT = Limits(method=LimitMethod.HISTORY, center=Decimal(0), sd=Decimal(1))


def key(instrument="ICP-1", measure=Measure.CHECK_RECOVERY, analyte="Cu", **kw) -> SeriesKey:
    return SeriesKey(measure=measure, instrument=instrument, analyte=analyte, **kw)


def points(*values, start=T0, step=timedelta(days=1), excluded=()) -> tuple[Point, ...]:
    return tuple(
        Point(
            at=start + i * step,
            value=Decimal(str(v)),
            run_code=f"R{i}",
            sequence=1,
            ref=f"p{i}",
            excluded=i in excluded,
        )
        for i, v in enumerate(values)
    )


def series(*values, k=None, **kw) -> Series:
    return Series(key=k or key(), points=points(*values, **kw))


def track(*values, limits=UNIT, window=None) -> Track:
    return Track(points=points(*values), limits=limits, window=window or Window())
