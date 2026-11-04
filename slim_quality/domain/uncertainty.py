"""Measurement uncertainty from the lab's own QC (D-131, ISO 17025 §7.6; Nordtest TR 537).

Per instrument × analyte × conditions, relative (a fraction of the value, as recoveries are):

    u_Rw   = the SD of a check standard's recoveries over the window (within-lab reproducibility: day to
             day, analyst to analyst, recalibration to recalibration)
    bias_i = each CRM recovery − 1, and each PT score's (x − X) / X, over the window
    RMS    = √(Σ bias_i² / n)
    u_bias = √(RMS² + u_Cref²)       u_Cref: the reference values' own uncertainty, entered (a certificate)
    u_c    = √(u_Rw² + u_bias²)      U = k × u_c, k = 2 (about 95 %)

Worked out unrounded, rounded once for display. An estimate is a record a quality manager signs; each
new one for the same key supersedes the last, and every one is kept. It is not on the report: the
report's ± is still the two cups' half-difference (D-55).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Dec, Frozen, NotAllowed
from slim_quality.domain.series import Series


class UncertaintyPolicy(Frozen):
    """The profile's minimums (D-131)."""

    min_precision_points: int = Field(default=20, ge=3, le=1000)
    min_bias_points: int = Field(default=3, ge=1, le=1000)
    k: Dec = Field(default=Decimal(2), gt=0)


class UncertaintyKey(Frozen):
    instrument: str = Field(min_length=1, max_length=100)
    analyte: str = Field(min_length=1, max_length=50)
    conditions: str | None = Field(default=None, max_length=50)

    @property
    def slug(self) -> str:
        return "|".join((self.instrument, self.analyte, self.conditions or ""))

    @property
    def label(self) -> str:
        mode = f" ({self.conditions})" if self.conditions else ""
        return f"{self.instrument} · {self.analyte}{mode}"


class BiasPoint(Frozen):
    source: Literal["crm", "pt"]
    ref: str = Field(min_length=1, max_length=100)  # the point's or the PT score's id
    at: AwareDatetime
    label: str = Field(min_length=1, max_length=200)  # e.g. "CRM-1 @ 5" or "Demo PT Metals DPT-02"
    bias: Dec  # relative: recovery − 1, or (x − X) / X


class UncertaintyEstimate(Frozen):
    id: int | None = None
    key: UncertaintyKey
    window_from: AwareDatetime
    window_to: AwareDatetime
    precision_series: str = Field(min_length=1, max_length=400)  # the check standard series' slug
    precision_label: str = Field(min_length=1, max_length=400)
    n_precision: int = Field(ge=2)
    u_rw: Dec = Field(ge=0)
    bias_points: tuple[BiasPoint, ...] = Field(min_length=1)
    rms_bias: Dec = Field(ge=0)
    u_cref: Dec = Field(ge=0)
    u_bias: Dec = Field(ge=0)
    u_c: Dec = Field(ge=0)
    k: Dec = Field(gt=0)
    expanded: Dec = Field(ge=0)  # U, relative
    basis: str = Field(min_length=1, max_length=2000)  # where u_Cref came from, and anything else to say
    signed_by: QualityActor | None = None
    signed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.window_from >= self.window_to:
            raise ValueError("the window ends after it starts")
        return self

    @staticmethod
    def percent(value: Decimal) -> Decimal:
        """A relative figure as a percentage, rounded once to 0.1."""
        return (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @property
    def shown(self) -> Decimal:
        return self.percent(self.expanded)


def bias_from_recovery(recovery: Decimal) -> Decimal:
    return recovery - 1


def bias_from_pt(lab: Decimal, assigned: Decimal) -> Decimal:
    if assigned == 0:
        raise ValueError("an assigned value of 0 has no relative bias")
    with localcontext() as context:
        context.prec = 28
        return (lab - assigned) / assigned


def work_out(
    key: UncertaintyKey,
    precision: Series,
    bias: Sequence[BiasPoint],
    u_cref: Decimal,
    basis: str,
    start: datetime,
    end: datetime,
    policy: UncertaintyPolicy,
) -> UncertaintyEstimate:
    """The estimate from the check series' judged points and the bias points inside [start, end].
    Refuses too few points (the profile's minimums): a figure from three runs is not an estimate."""
    if not precision.key.measure.is_recovery:
        raise NotAllowed("precision comes from a recovery series (a check standard)")
    values = [p.value for p in precision.judged if start <= p.at <= end]
    if len(values) < policy.min_precision_points:
        raise NotAllowed(
            f"{len(values)} check points in the window; the profile asks for at least "
            f"{policy.min_precision_points}"
        )
    found = tuple(b for b in bias if start <= b.at <= end)
    if len(found) < policy.min_bias_points:
        raise NotAllowed(
            f"{len(found)} CRM or PT results in the window; the profile asks for at least "
            f"{policy.min_bias_points} to estimate the bias"
        )
    with localcontext() as context:
        context.prec = 28
        mean = sum(values) / len(values)
        u_rw = (sum((v - mean) ** 2 for v in values) / (len(values) - 1)).sqrt()
        rms = (sum(b.bias**2 for b in found) / len(found)).sqrt()
        u_bias = (rms**2 + u_cref**2).sqrt()
        u_c = (u_rw**2 + u_bias**2).sqrt()
        expanded = policy.k * u_c
    return UncertaintyEstimate(
        key=key,
        window_from=start,
        window_to=end,
        precision_series=precision.key.slug,
        precision_label=precision.key.label,
        n_precision=len(values),
        u_rw=u_rw,
        bias_points=found,
        rms_bias=rms,
        u_cref=u_cref,
        u_bias=u_bias,
        u_c=u_c,
        k=policy.k,
        expanded=expanded,
        basis=basis.strip(),
    )
