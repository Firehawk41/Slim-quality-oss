"""Readiness per clause, the management review pack and the validation signature (D-136, D-137). Reads,
except signing (flushes; the host commits).

The host hands over what only it knows, each optional (a None is shown as not checked):
  * `turnaround`: its request dates (`TurnaroundSource`, D-133),
  * `acts`: every act its modules recorded (the competence check, D-132),
  * `versions`: each installed module's version (what the validation pack covers, D-137).
`day_of` turns an instant into the lab's day (the host's clock).
"""

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.competence import ActRecord, outside_authorisation
from slim_quality.domain.complaints import ComplaintState
from slim_quality.domain.equipment import EquipmentState
from slim_quality.domain.internal_audits import AuditState, LineOutcome
from slim_quality.domain.management_review import (
    INPUTS,
    ManagementReview,
    PackSection,
    ReviewOutput,
    ReviewPack,
    check_review,
)
from slim_quality.domain.nonconformity import NcState
from slim_quality.domain.ports import TurnaroundSource
from slim_quality.domain.pt import PtClass, PtState
from slim_quality.domain.readiness import ClauseReadiness, Facts, Rag, readiness
from slim_quality.domain.turnaround import turnaround
from slim_quality.domain.validation import ValidationSignature, check_signature
from slim_quality.domain.validation import status as validation_status
from slim_quality.persistence import store
from slim_quality.service import internal_audits, uncertainty
from slim_quality.service import quality as q

DayOf = Callable[[datetime], date]
ON_TIME_DAYS = 90  # the window readiness's on-time figure looks back over


def _day(at: datetime | None, day_of: DayOf) -> date | None:
    return None if at is None else day_of(at)


def gather(
    session: Session,
    today: date,
    turnaround_source: TurnaroundSource | None = None,
    acts: Iterable[ActRecord] | None = None,
    versions: dict[str, str] | None = None,
    day_of: DayOf = datetime.date,
) -> Facts:
    profile = q.current_profile(session)
    policy = profile.readiness

    def older(at: datetime | None, days: int) -> bool:
        day = _day(at, day_of)
        return day is not None and today - day > timedelta(days=days)

    equipment = Counter(
        i.state(today, profile.equipment.due_within_days) for i in store.list_equipment(session)
    )
    lots = [lot for lot in store.list_lots(session) if lot.withdrawn_on is None]
    estimates = list(uncertainty.current(session).values())
    rounds = store.list_pt_rounds(session)
    scored = [r for r in rounds if r.state == PtState.SCORED and r.report is not None]
    year_ago = today - timedelta(days=365)
    unsatisfactory = sum(
        1
        for r in scored
        if r.report.report_date >= year_ago
        for s in r.scores(profile.pt)
        if s.klass == PtClass.UNSATISFACTORY
    )
    complaints = [c for c in store.list_complaints(session) if c.state != ComplaintState.CLOSED]
    ncs = [nc for nc in store.list_ncs(session) if nc.state != NcState.CLOSED]
    actions_overdue = sum(
        1
        for nc in ncs
        if nc.state == NcState.ACTION_TAKEN
        and nc.latest_action.due is not None
        and nc.latest_action.due < today
    )
    risks = [r for r in store.list_risks(session) if not r.closed]
    audits = [a for a in store.list_internal_audits(session) if a.state != AuditState.CANCELLED]
    reviews = store.management_reviews(session)
    signatures = store.validation_signatures(session)
    if versions is not None:
        rag, signature = validation_status(signatures, versions)
    else:
        rag, signature = (Rag.AMBER, signatures[0]) if signatures else (None, None)
    on_time = None
    if turnaround_source is not None:
        since = today - timedelta(days=ON_TIME_DAYS)
        on_time = turnaround(turnaround_source.requests(since), today, since).overall
    competence = None
    if acts is not None:
        competence = len(outside_authorisation(acts, store.list_authorisations(session), day_of))
    return Facts(
        today=today,
        last_audited=internal_audits.last_audited(session),
        findings_without_nc={k: len(v) for k, v in internal_audits.findings_without_nc(session).items()},
        audits_planned_this_year=sum(1 for a in audits if a.year == today.year),
        audits_overdue=sum(1 for a in audits if a.overdue(today)),
        competence_findings=competence,
        equipment_overdue=equipment[EquipmentState.OVERDUE],
        equipment_due=equipment[EquipmentState.DUE],
        equipment_out=equipment[EquipmentState.OUT_OF_SERVICE],
        lots_expired_in_use=sum(1 for lot in lots if lot.effective_expiry < today),
        lots_expiring=sum(1 for lot in lots if today <= lot.effective_expiry <= today + timedelta(days=30)),
        uncertainty_estimates=len(estimates),
        uncertainty_oldest=min((_day(e.signed_at, day_of) for e in estimates if e.signed_at), default=None),
        pt_last_scored=max((r.report.report_date for r in scored), default=None),
        pt_unsatisfactory=unsatisfactory,
        pt_overdue=sum(1 for r in rounds if r.overdue(today)),
        complaints_open=len(complaints),
        complaints_open_long=sum(1 for c in complaints if older(c.raised_at, policy.complaint_open_days)),
        ncs_open=len(ncs),
        ncs_open_long=sum(1 for nc in ncs if older(nc.raised_at, policy.nc_open_days)),
        nc_actions_overdue=actions_overdue,
        risks_open=len(risks),
        risks_flagged=sum(1 for r in risks if r.flags(today, profile.risk)),
        last_review=_day(reviews[0].signed_at, day_of) if reviews else None,
        validation=rag,
        validation_signed=None if signature is None else _day(signature.signed_at, day_of),
        on_time_percent=None if on_time is None else on_time.on_time_percent,
        on_time_requests=0 if on_time is None else on_time.complete,
    )


def view(session: Session, facts: Facts) -> tuple[ClauseReadiness, ...]:
    return readiness(facts, q.current_profile(session).readiness)


# --- the management review pack (§8.9.2) -----------------------------------------------------------------


def _in(day: date | None, start: date, end: date) -> bool:
    return day is not None and start <= day <= end


def pack(
    session: Session,
    period_from: date,
    period_to: date,
    facts: Facts,
    turnaround_source: TurnaroundSource | None = None,
    acts: Iterable[ActRecord] | None = None,
    day_of: DayOf = datetime.date,
) -> ReviewPack:
    """What the records say for the period: the readiness view on `facts.today` and the inputs the
    records hold. Worked out each time; frozen only when signed."""
    profile = q.current_profile(session)

    def within(at: datetime | None) -> bool:
        return _in(_day(at, day_of), period_from, period_to)

    sections: dict[str, list[str]] = {letter: [] for letter in ("d", "e", "f", "h", "j", "m", "n", "o")}
    earlier = [r for r in store.management_reviews(session) if _day(r.signed_at, day_of) <= period_to]
    if earlier:
        last = earlier[0]
        sections["d"].append(
            f"{last.number}, signed {_day(last.signed_at, day_of)} by {last.signed_by.display}:"
        )
        for out in last.outputs:
            due = f", due {out.due}" if out.due else ""
            owner = f" ({out.owner}{due})" if out.owner else due
            sections["d"].append(f"{out.kind.label}: {out.text}{owner}")
    else:
        sections["d"].append("No earlier management review.")

    for audit in store.list_internal_audits(session):
        if _in(audit.done_on, period_from, period_to):
            counts = Counter(line.outcome for line in audit.conducted.lines)
            sections["e"].append(
                f"{audit} on {audit.done_on} by {audit.auditor.display}: "
                f"{counts[LineOutcome.CONFORMING]} conforming, "
                f"{counts[LineOutcome.OBSERVATION]} observations, "
                f"{counts[LineOutcome.FINDING]} findings"
            )
        elif audit.state == AuditState.CANCELLED and within(audit.planned_at):
            sections["e"].append(f"{audit}: cancelled")
    vertical = [a for a in store.audits(session) if within(a.at)]
    if vertical:
        with_findings = sum(1 for a in vertical if a.findings.strip())
        sections["e"].append(f"{len(vertical)} vertical audits of reports, {with_findings} with findings")
    if not sections["e"]:
        sections["e"].append("No audit recorded in the period.")

    ncs = store.list_ncs(session)
    raised = [nc for nc in ncs if within(nc.raised_at)]
    closed = [nc for nc in ncs if nc.state == NcState.CLOSED and within(nc.entries[-1].at)]
    sections["f"].append(f"{len(raised)} nonconformities raised, {len(closed)} closed in the period.")
    sections["f"] += [
        f"{nc.number} {nc.title}: {nc.state.label.lower()}" for nc in ncs if nc.state != NcState.CLOSED
    ]

    if turnaround_source is not None:
        report = turnaround(
            [r for r in turnaround_source.requests(period_from) if r.received <= period_to],
            period_to,
            period_from,
        )
        sections["h"] += [f"{t.label}: {t.requests} requests, {t.samples} samples" for t in report.by_month]
        overall = report.overall
        if overall.on_time_percent is not None:
            sections["h"].append(
                f"{overall.on_time_percent} % of {overall.complete} complete requests on time."
            )
    if not sections["h"]:
        sections["h"].append(
            "No request dates handed over." if turnaround_source is None else "No requests received."
        )

    complaints = [c for c in store.list_complaints(session) if within(c.raised_at)]
    for c in complaints:
        verdict = c.investigation
        valid = "" if verdict is None else (", valid" if verdict.valid else ", not about the lab's work")
        sections["j"].append(f"{c.number} on {c.tr_number}: {c.state.label.lower()}{valid}")
    if not complaints:
        sections["j"].append("No complaint in the period.")

    for risk in store.list_risks(session):
        if risk.closed:
            continue
        band = risk.band(profile.risk)
        scored = f"score {risk.score} ({band.label})" if band is not None else "opportunity"
        flags = risk.flags(period_to, profile.risk)
        sections["m"].append(f"{risk}: {scored}" + (f"; {'; '.join(flags)}" if flags else ""))
    if not sections["m"]:
        sections["m"].append("No risk or opportunity registered.")

    for r in store.list_pt_rounds(session):
        if r.state == PtState.SCORED and _in(r.report.report_date, period_from, period_to):
            worst = r.worst(profile.pt)
            sections["n"].append(
                f"PT {r.scheme} {r.round_code}: {worst.label.lower() if worst else 'not scored'}"
            )
    for review in store.reviews(session):
        if within(review.at):
            sections["n"].append(
                f"QC review {review.period_from} to {review.period_to}, by {review.by.display}"
            )
    if not sections["n"]:
        sections["n"].append("No PT round scored and no QC review signed in the period.")

    if acts is not None:
        found = outside_authorisation(acts, store.list_authorisations(session), day_of)
        sections["o"].append(f"{len(found)} acts outside an authorisation.")
    granted = [a for a in store.list_authorisations(session) if within(a.granted_at)]
    sections["o"].append(f"{len(granted)} authorisations granted in the period.")
    sections["o"].append(
        f"Equipment on {facts.today}: {facts.equipment_overdue} overdue, {facts.equipment_due} due soon, "
        f"{facts.equipment_out} out of service."
    )
    return ReviewPack(
        period_from=period_from,
        period_to=period_to,
        readiness=view(session, facts),
        sections=tuple(
            PackSection(letter=letter, title=INPUTS[letter], lines=tuple(lines))
            for letter, lines in sections.items()
        ),
    )


def sign_review(
    session: Session,
    review_pack: ReviewPack,
    written: dict[str, str],
    outputs: Iterable[ReviewOutput],
    attendees: str,
    actor: QualityActor,
) -> ManagementReview:
    """The pack is worked out again by the caller at signing (never taken from a form), then frozen."""
    review = ManagementReview(
        pack=review_pack,
        written={k: v for k, v in written.items() if v.strip()},
        outputs=tuple(outputs),
        attendees=attendees,
        signed_by=actor,
    )
    check_review(review)
    return store.add_management_review(session, review)


# --- the validation pack's signature (D-137) -------------------------------------------------------------


def sign_validation(
    session: Session, versions: dict[str, str], validation_pack: dict, note: str, actor: QualityActor
) -> ValidationSignature:
    signature = ValidationSignature(versions=versions, pack=validation_pack, note=note, signed_by=actor)
    check_signature(signature)
    return store.add_validation_signature(session, signature)


def validation(session: Session, versions: dict[str, str]) -> tuple[Rag | None, ValidationSignature | None]:
    return validation_status(store.validation_signatures(session), versions)
