"""exceedance@1: one-sided, for values that only go wrong upwards (blanks, BEC, LOD, RSD), where
contamination skews the data and a mean ± SD chart misleads.

The limit: the limits' `upper` (centre + 3 SD; robust limits keep it from being widened by the
very spikes it should catch), else the window's high end.
ABOVE_LIMIT on each point over it; EXCEEDANCE_RATE where `count` of the last `last` points are."""

from typing import ClassVar

from pydantic import Field

from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import Severity
from slim_quality.monitors.base import Hit, Track, onset


class ExceedanceParams(Frozen):
    last: int = Field(default=20, ge=2, le=200)
    count: int = Field(default=3, ge=1)


class Exceedance:
    name: ClassVar[str] = "exceedance"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "One-sided limit and exceedance rate"
    codes: ClassVar[dict[str, Severity]] = {"ABOVE_LIMIT": Severity.WARN, "EXCEEDANCE_RATE": Severity.ACTION}
    Params: ClassVar[type[Frozen]] = ExceedanceParams

    def check(self, track: Track, params: ExceedanceParams) -> list[Hit]:
        limit = track.limits.upper if track.limits is not None else None
        limit = track.window.high if limit is None else limit
        if limit is None:
            return []
        above = [p.value > limit for p in track.points]
        hits = [
            Hit("ABOVE_LIMIT", i, f"{p.value} above {limit}") for i, p in enumerate(track.points) if above[i]
        ]
        rate = [sum(above[max(0, i - params.last + 1) : i + 1]) >= params.count for i in range(len(above))]
        for i in onset(rate):
            message = f"{params.count} or more of the last {params.last} points above {limit}"
            hits.append(Hit("EXCEEDANCE_RATE", i, message))
        return sorted(hits, key=lambda h: (h.index, h.code))
