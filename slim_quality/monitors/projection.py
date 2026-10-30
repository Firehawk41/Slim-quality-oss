"""projection@1: time to the limit. A straight line through the last `last` points (least squares, per
day) says when the series will cross its acceptance window, if it keeps going: a drift is flagged
while every point still passes, and maintenance can be planned instead of forced.

Flagged where the crossing first comes within `horizon_days`; a series already outside is the gate's
business, not a forecast."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import ClassVar

from pydantic import Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Severity
from slim_quality.domain.limits import Window
from slim_quality.domain.series import Point
from slim_quality.monitors.base import Hit, Track, fmt, onset

_DAY = Decimal(86400)


class ProjectionParams(Frozen):
    last: int = Field(default=10, ge=3, le=200)
    horizon_days: Dec = Field(default=Decimal("14"), gt=0)


@dataclass(frozen=True)
class Trend:
    slope_per_day: Decimal
    start: datetime  # the fit's first point
    start_value: Decimal
    end: datetime  # its last
    end_value: Decimal
    days_to_limit: Decimal | None  # None: not heading for either end within reach of the line
    limit: Decimal | None


def fit(points: Sequence[Point], window: Window) -> Trend | None:
    if len(points) < 3:
        return None
    t0 = points[0].at
    with localcontext() as context:
        context.prec = 28
        xs = [Decimal((p.at - t0).total_seconds()) / _DAY for p in points]
        ys = [p.value for p in points]
        n = len(points)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
        start_value = my + slope * (xs[0] - mx)
        end_value = my + slope * (xs[-1] - mx)
        days = limit = None
        if slope > 0 and window.high is not None and end_value < window.high:
            limit, days = window.high, (window.high - end_value) / slope
        elif slope < 0 and window.low is not None and end_value > window.low:
            limit, days = window.low, (window.low - end_value) / slope
    return Trend(slope, points[0].at, start_value, points[-1].at, end_value, days, limit)


class Projection:
    name: ClassVar[str] = "projection"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "Time to limit (forecast)"
    codes: ClassVar[dict[str, Severity]] = {"LIMIT_AHEAD": Severity.WARN}
    Params: ClassVar[type[Frozen]] = ProjectionParams

    def check(self, track: Track, params: ProjectionParams) -> list[Hit]:
        if track.window.low is None and track.window.high is None:
            return []
        points = list(track.points)
        trends = [
            fit(points[max(0, i - params.last + 1) : i + 1], track.window) if i + 1 >= params.last else None
            for i in range(len(points))
        ]
        near = [
            t is not None and t.days_to_limit is not None and t.days_to_limit <= params.horizon_days
            for t in trends
        ]
        return [
            Hit(
                "LIMIT_AHEAD", i, f"reaches {trends[i].limit} in about {fmt(trends[i].days_to_limit, 0)} days"
            )
            for i in onset(near)
        ]
