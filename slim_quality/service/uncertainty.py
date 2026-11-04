"""Uncertainty from pooled QC (D-131): which estimates can be made, the working, and the signed record.
Flushes; the host commits. Inputs are taken again from the records when an estimate is signed, never from
the form: what is signed is what the records give."""

from collections.abc import Sequence
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.enums import Measure
from slim_quality.domain.ports import QcSource
from slim_quality.domain.pt import PtRound
from slim_quality.domain.series import Series, SeriesKey
from slim_quality.domain.uncertainty import (
    BiasPoint,
    UncertaintyEstimate,
    UncertaintyKey,
    bias_from_pt,
    bias_from_recovery,
    work_out,
)
from slim_quality.persistence import store
from slim_quality.service import quality as q


def key_of(series_key: SeriesKey) -> UncertaintyKey:
    return UncertaintyKey(
        instrument=series_key.instrument, analyte=series_key.analyte, conditions=series_key.conditions
    )


def precision_series(found: Sequence[Series]) -> list[Series]:
    """The check standard recovery series an estimate can start from, by label."""
    return sorted(
        (s for s in found if s.key.measure == Measure.CHECK_RECOVERY and s.key.analyte),
        key=lambda s: s.key.label,
    )


def bias_points(found: Sequence[Series], rounds: Sequence[PtRound], key: UncertaintyKey) -> list[BiasPoint]:
    """The key's CRM recoveries (same instrument, analyte, conditions) and every scored PT result for its
    analyte (a PT scheme names no instrument: the lab's result stands for the method)."""
    points = []
    for s in found:
        if s.key.measure != Measure.CRM_RECOVERY or key_of(s.key) != key:
            continue
        what = " @ ".join(str(p) for p in (s.key.material or "CRM", s.key.level) if p is not None)
        points += [
            BiasPoint(source="crm", ref=p.ref, at=p.at, label=what, bias=bias_from_recovery(p.value))
            for p in s.judged
        ]
    for r in rounds:
        if r.report is None or r.submission is None:
            continue
        lab = {v.analyte: v for v in r.submission.values}
        for a in r.report.assigned:
            if a.analyte != key.analyte or a.assigned == 0:
                continue
            points.append(
                BiasPoint(
                    source="pt",
                    ref=f"pt:{r.id}:{a.analyte}",
                    at=datetime.combine(r.report.report_date, time(12), UTC),
                    label=f"{r.scheme} {r.round_code}",
                    bias=bias_from_pt(lab[a.analyte].value, a.assigned),
                )
            )
    return sorted(points, key=lambda b: (b.at, b.ref))


def estimate(
    session: Session,
    source: QcSource,
    slug: str,
    start: datetime,
    end: datetime,
    u_cref: Decimal,
    basis: str,
) -> UncertaintyEstimate:
    """The working, unsigned: what the quality manager looks at before signing."""
    found = source.series()
    precision = next((s for s in precision_series(found) if s.key.slug == slug), None)
    if precision is None:
        raise NotAllowed("choose a check standard series")
    key = key_of(precision.key)
    bias = bias_points(found, store.list_pt_rounds(session), key)
    policy = q.current_profile(session).uncertainty
    return work_out(key, precision, bias, u_cref, basis or "—", start, end, policy)


def sign(
    session: Session,
    source: QcSource,
    slug: str,
    start: datetime,
    end: datetime,
    u_cref: Decimal,
    basis: str,
    actor: QualityActor,
) -> UncertaintyEstimate:
    if not actor.is_manager:
        raise NotAllowed("a quality manager signs an uncertainty estimate")
    if not basis.strip():
        raise NotAllowed("say where u(Cref) comes from (the certificate, the PT report)")
    found = estimate(session, source, slug, start, end, u_cref, basis)
    return store.add_uncertainty(session, found.model_copy(update={"signed_by": actor}))


def current(session: Session, as_of: datetime | None = None) -> dict[str, UncertaintyEstimate]:
    """The latest signed estimate per key, by key slug."""
    latest: dict[str, UncertaintyEstimate] = {}
    for e in store.uncertainty_estimates(session, as_of=as_of):  # newest first
        latest.setdefault(e.key.slug, e)
    return latest
