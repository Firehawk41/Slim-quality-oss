"""Control limits: a centre line and a spread, from a baseline or entered (a certificate).

Statistics are worked out once, when the limits are set, never as data arrives: limits that follow
the data absorb a drift, so it never trips a rule.
"""

from collections.abc import Sequence
from decimal import Decimal, localcontext
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import LimitMethod
from slim_quality.domain.series import SeriesKey

MAD_TO_SD = Decimal("1.4826")  # for normal data, SD ≈ 1.4826 × MAD


class Window(Frozen):
    """The acceptance window a value must stay in (e.g. 0.75–1.25 recovery): the hard gate, policy data."""

    low: Dec | None = None
    high: Dec | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise ValueError("the window's low end must be below its high end")
        return self


class Limits(Frozen):
    method: LimitMethod
    center: Dec
    sd: Dec = Field(gt=0)
    upper: Dec | None = (
        None  # one-sided limit (centre + 3 SD), for blanks and the like; entered limits may set it
    )
    n: int = Field(default=0, ge=0)  # baseline points; 0 when entered
    basis: str = Field(default="", max_length=300)  # e.g. "baseline: 20 points, 2026-05-01 to 2026-05-28"
    provisional: bool = False  # worked out on the fly from the first points: nobody has approved it

    def z(self, value: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 28
            return (value - self.center) / self.sd


class LimitSet(Frozen):
    """Approved limits for one series, in force from `effective_from` until the next set."""

    id: int | None = None
    series: SeriesKey
    limits: Limits
    effective_from: AwareDatetime


def baseline(values: Sequence[Decimal], method: LimitMethod, basis: str = "") -> Limits:
    """Limits from baseline values. Refuses fewer than 3 values or a baseline with no spread."""
    if method == LimitMethod.CERTIFICATE:
        raise ValueError("certificate limits are entered, not worked out from a baseline")
    if len(values) < 3:
        raise ValueError("a baseline needs at least 3 points")
    ordered = sorted(values)
    with localcontext() as context:
        context.prec = 28
        if method == LimitMethod.HISTORY:
            center = sum(ordered) / len(ordered)
            variance = sum((v - center) ** 2 for v in ordered) / (len(ordered) - 1)
            sd = variance.sqrt()
        else:
            center = _median(ordered)
            sd = MAD_TO_SD * _median(sorted(abs(v - center) for v in ordered))
        if sd <= 0:
            raise ValueError("the baseline has no spread: every point is the same")
        upper = center + 3 * sd
    return Limits(method=method, center=center, sd=sd, upper=upper, n=len(ordered), basis=basis)


def _median(ordered: Sequence[Decimal]) -> Decimal:
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
