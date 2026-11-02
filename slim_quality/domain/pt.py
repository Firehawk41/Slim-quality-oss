"""Proficiency testing (D-128, D-129; ISO 17025 §7.7.2, ISO 13528).

A round's sample is an ordinary TR: it goes through the lab like any sample. The round names it (TR
number and sample, plain text: no key into the host's tables) and its analytes. Then, append-only:

    registered → submitted (the lab's values, snapshotted from the released results: a later correction
                 doesn't change what was sent)
               → scored (the provider's assigned value X and σ_pt per analyte; z = (x − X) / σ_pt)

z is worked out unrounded and classed unrounded (the profile's cut-offs, |z| ≤ 2 satisfactory, 2 < |z| < 3
questionable, ≥ 3 unsatisfactory); it is shown to 0.1, rounded once. A z-score is already standardised,
so its series (per scheme × analyte) is judged on fixed limits, mean 0 and SD 1, by the same tools as QC
(Westgard 1-3s fires beyond 3, 2-2s on two questionable rounds in a row).
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Dec, Frozen, NotAllowed
from slim_quality.domain.enums import Measure
from slim_quality.domain.series import Point, Series, SeriesKey


class PtPolicy(Frozen):
    """The profile's PT cut-offs (D-128)."""

    questionable: Dec = Field(default=Decimal(2), gt=0)
    unsatisfactory: Dec = Field(default=Decimal(3), gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.questionable >= self.unsatisfactory:
            raise ValueError("questionable starts below unsatisfactory")
        return self


class PtClass(IntEnum):
    SATISFACTORY = 1
    QUESTIONABLE = 2
    UNSATISFACTORY = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class PtState(IntEnum):
    REGISTERED = 1
    SUBMITTED = 2
    SCORED = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class PtEntryKind(IntEnum):
    SUBMITTED = 1
    SCORED = 2
    NOTE = 3


class LabValue(Frozen):
    """What the lab sent for one analyte: the released result as it stood."""

    analyte: str = Field(min_length=1, max_length=50)
    value: Dec
    unit: str = Field(default="", max_length=20)
    result_ref: str = Field(min_length=1, max_length=100)  # the host's id for the result and its version


class Assigned(Frozen):
    """The provider's report for one analyte."""

    analyte: str = Field(min_length=1, max_length=50)
    assigned: Dec  # X
    sigma: Dec = Field(gt=0)  # σ_pt
    provider_z: Dec | None = None  # if the provider gives one


class PtEntry(Frozen):
    kind: PtEntryKind
    values: tuple[LabValue, ...] = ()  # SUBMITTED
    assigned: tuple[Assigned, ...] = ()  # SCORED
    report_date: date | None = None  # SCORED: the provider's report
    note: str = Field(default="", max_length=2000)
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        if (self.kind == PtEntryKind.SUBMITTED) != bool(self.values):
            raise ValueError("a submission carries the lab's values, and only it does")
        if (self.kind == PtEntryKind.SCORED) != bool(self.assigned):
            raise ValueError("a score carries the provider's values, and only it does")
        if (self.kind == PtEntryKind.SCORED) != (self.report_date is not None):
            raise ValueError("a score gives the provider's report date")
        if self.kind == PtEntryKind.NOTE and not self.note.strip():
            raise ValueError("write the note")
        for found in (self.values, self.assigned):
            names = [v.analyte for v in found]
            if len(set(names)) != len(names):
                raise ValueError("an analyte is given twice")
        return self


class Score(Frozen):
    analyte: str
    lab: LabValue
    assigned: Assigned
    z: Dec  # unrounded
    klass: PtClass

    @property
    def shown(self) -> Decimal:
        return self.z.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @property
    def provider_differs(self) -> bool:
        """The provider's z, if given, isn't ours as shown: worth a look (a unit, a typo, a σ)."""
        return self.assigned.provider_z is not None and self.assigned.provider_z != self.shown


def z_score(x: Decimal, assigned: Decimal, sigma: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return (x - assigned) / sigma


def classify(z: Decimal, policy: PtPolicy) -> PtClass:
    size = abs(z)
    if size >= policy.unsatisfactory:
        return PtClass.UNSATISFACTORY
    if size > policy.questionable:
        return PtClass.QUESTIONABLE
    return PtClass.SATISFACTORY


class PtRound(Frozen):
    id: int | None = None
    scheme: str = Field(min_length=1, max_length=100)
    round_code: str = Field(min_length=1, max_length=50)
    tr_number: str = Field(min_length=1, max_length=20)
    sample: str = Field(min_length=1, max_length=30)  # the lab sample ID, e.g. TR00042-01
    analytes: tuple[str, ...] = Field(min_length=1)
    due: date  # results to the provider by
    registered_by: QualityActor
    registered_at: AwareDatetime | None = None
    entries: tuple[PtEntry, ...] = ()

    @model_validator(mode="after")
    def _analytes(self) -> Self:
        if len(set(self.analytes)) != len(self.analytes):
            raise ValueError("an analyte is named twice")
        return self

    def _latest(self, kind: PtEntryKind) -> PtEntry | None:
        return next((e for e in reversed(self.entries) if e.kind == kind), None)

    @property
    def submission(self) -> PtEntry | None:
        return self._latest(PtEntryKind.SUBMITTED)

    @property
    def report(self) -> PtEntry | None:
        return self._latest(PtEntryKind.SCORED)

    @property
    def state(self) -> PtState:
        if self.report is not None:
            return PtState.SCORED
        return PtState.SUBMITTED if self.submission is not None else PtState.REGISTERED

    def overdue(self, today: date) -> bool:
        return self.submission is None and today > self.due

    def scores(self, policy: PtPolicy) -> tuple[Score, ...]:
        if self.report is None or self.submission is None:
            return ()
        lab = {v.analyte: v for v in self.submission.values}
        found = []
        for a in self.report.assigned:
            z = z_score(lab[a.analyte].value, a.assigned, a.sigma)
            found.append(
                Score(analyte=a.analyte, lab=lab[a.analyte], assigned=a, z=z, klass=classify(z, policy))
            )
        return tuple(found)

    def worst(self, policy: PtPolicy) -> PtClass | None:
        return max((s.klass for s in self.scores(policy)), default=None)

    def check(self, entry: PtEntry) -> None:
        if entry.kind == PtEntryKind.SUBMITTED:
            if self.submission is not None:
                raise NotAllowed(f"{self.round_code}'s results were submitted")
            given = {v.analyte for v in entry.values}
            if given != set(self.analytes):
                raise NotAllowed(f"submit every analyte of the round: {', '.join(self.analytes)}")
        elif entry.kind == PtEntryKind.SCORED:
            if self.submission is None:
                raise NotAllowed("submit the lab's results before entering the provider's report")
            if self.report is not None:
                raise NotAllowed(f"{self.round_code} is scored")
            if {a.analyte for a in entry.assigned} != set(self.analytes):
                raise NotAllowed(f"give the provider's values for every analyte: {', '.join(self.analytes)}")


def z_series(rounds: Iterable[PtRound]) -> tuple[Series, ...]:
    """The z-scores per scheme × analyte, each at the provider's report date (noon UTC: when the score was
    known), D-129. The scheme stands where a QC series has its instrument."""
    points: dict[SeriesKey, list[Point]] = defaultdict(list)
    for r in rounds:
        report, submission = r.report, r.submission
        if report is None or submission is None:
            continue
        lab = {v.analyte: v for v in submission.values}
        for a in report.assigned:
            key = SeriesKey(measure=Measure.PT_Z, instrument=r.scheme, analyte=a.analyte)
            points[key].append(
                Point(
                    at=datetime.combine(report.report_date, time(12), UTC),
                    value=z_score(lab[a.analyte].value, a.assigned, a.sigma),
                    run_code=r.round_code,
                    ref=f"pt:{r.id}:{a.analyte}",
                )
            )
    return tuple(
        Series(key=key, points=tuple(sorted(found, key=lambda p: p.at)))
        for key, found in sorted(points.items(), key=lambda item: item[0].label)
    )
