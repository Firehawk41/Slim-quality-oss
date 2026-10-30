"""cusum@1: tabular CUSUM of z. Each side adds up how far points sit beyond k SD and forgets the
rest; past h it flags, and that side starts again from zero."""

from decimal import Decimal
from typing import ClassVar

from pydantic import Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Severity
from slim_quality.monitors.base import Hit, Track, fmt


class CusumParams(Frozen):
    slack: Dec = Field(default=Decimal("0.5"), ge=0)  # k, in SD
    limit: Dec = Field(default=Decimal("5"), gt=0)  # h, in SD


class Cusum:
    name: ClassVar[str] = "cusum"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "CUSUM (persistent bias)"
    codes: ClassVar[dict[str, Severity]] = {"CUSUM_HIGH": Severity.WARN, "CUSUM_LOW": Severity.WARN}
    Params: ClassVar[type[Frozen]] = CusumParams

    def check(self, track: Track, params: CusumParams) -> list[Hit]:
        if track.limits is None:
            return []
        hits = []
        upper = lower = Decimal(0)
        for i, zi in enumerate(track.z()):
            upper = max(Decimal(0), upper + zi - params.slack)
            lower = max(Decimal(0), lower - zi - params.slack)
            if upper > params.limit:
                hits.append(Hit("CUSUM_HIGH", i, f"cumulative {fmt(upper)} SD above the centre"))
                upper = Decimal(0)
            if lower > params.limit:
                hits.append(Hit("CUSUM_LOW", i, f"cumulative {fmt(lower)} SD below the centre"))
                lower = Decimal(0)
        return hits
