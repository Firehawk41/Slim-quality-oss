"""shared_shift@1: the same analyte off on several instruments at once. Over the last `days` days of
each series, the mean z; when `instruments` or more series of one analyte and measure (and material
and level) sit beyond `threshold` SD on the same side, the cause is shared: a reagent, a standard, the
room; not one instrument. Flagged on each such series' latest point."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import ClassVar

from pydantic import Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Severity
from slim_quality.domain.series import SeriesKey
from slim_quality.monitors.base import Track, fmt


class SharedParams(Frozen):
    days: int = Field(default=14, ge=1, le=365)
    threshold: Dec = Field(default=Decimal("1.5"), gt=0)
    instruments: int = Field(default=2, ge=2)


class SharedShift:
    name: ClassVar[str] = "shared_shift"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "Shared shift across instruments"
    codes: ClassVar[dict[str, Severity]] = {"SHARED_SHIFT": Severity.ACTION}
    Params: ClassVar[type[Frozen]] = SharedParams

    def check_all(
        self, latest: Sequence[tuple[SeriesKey, Track]], params: SharedParams
    ) -> list[tuple[SeriesKey, int, str]]:
        """`latest`: each series' newest track. Returns (series, index into that track, message)."""
        groups = defaultdict(list)
        for key, track in latest:
            if track.limits is None or not track.points:
                continue
            since = track.points[-1].at - timedelta(days=params.days)
            recent = [z for p, z in zip(track.points, track.z(), strict=True) if p.at >= since]
            with localcontext() as context:
                context.prec = 28
                mean = sum(recent) / len(recent)
            groups[(key.measure, key.analyte, key.material, key.level)].append((key, track, mean))
        found = []
        for members in groups.values():
            for sign, side in ((1, "above"), (-1, "below")):
                off = [(k, t, m) for k, t, m in members if m * sign > params.threshold]
                if len({k.instrument for k, _, _ in off}) >= params.instruments:
                    names = ", ".join(sorted({k.instrument for k, _, _ in off}))
                    for k, t, m in off:
                        message = f"mean {fmt(m)} SD {side} on {names}: look for a shared cause"
                        found.append((k, len(t.points) - 1, message))
        return found
