"""ewma@1: an exponentially weighted moving average of z. It weighs the newest point by λ and the
past by 1 − λ, so a small, sustained shift builds up where single points stay inside their limits.
Limits ±L × σ_EWMA, with σ_EWMA = √(λ ÷ (2 − λ) × (1 − (1 − λ)^2t)). Flagged where it crosses."""

from decimal import Decimal, localcontext
from typing import ClassVar

from pydantic import Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import Severity
from slim_quality.monitors.base import Hit, Track, fmt, onset


class EwmaParams(Frozen):
    weight: Dec = Field(default=Decimal("0.2"), gt=0, le=1)  # λ
    width: Dec = Field(default=Decimal("3"), gt=0)  # L


class Ewma:
    name: ClassVar[str] = "ewma"
    version: ClassVar[int] = 1
    title: ClassVar[str] = "EWMA (small sustained shifts)"
    codes: ClassVar[dict[str, Severity]] = {"EWMA_HIGH": Severity.WARN, "EWMA_LOW": Severity.WARN}
    Params: ClassVar[type[Frozen]] = EwmaParams

    def check(self, track: Track, params: EwmaParams) -> list[Hit]:
        if track.limits is None:
            return []
        lam, width = params.weight, params.width
        high, low, averages = [], [], []
        with localcontext() as context:
            context.prec = 28
            average = Decimal(0)
            for t, zi in enumerate(track.z(), start=1):
                average = lam * zi + (1 - lam) * average
                sigma = (lam / (2 - lam) * (1 - (1 - lam) ** (2 * t))).sqrt()
                averages.append(average)
                high.append(average > width * sigma)
                low.append(average < -width * sigma)
        hits = [Hit("EWMA_HIGH", i, f"EWMA {fmt(averages[i])} SD above the centre") for i in onset(high)]
        hits += [Hit("EWMA_LOW", i, f"EWMA {fmt(averages[i])} SD below the centre") for i in onset(low)]
        return sorted(hits, key=lambda h: h.index)
