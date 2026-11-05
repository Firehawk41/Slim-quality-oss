"""Internal audits (D-135): the programme, the audits and who may audit what. Flushes; the host commits.

Who is involved in a clause is the host's (the acts its modules recorded) together with slim-quality's
own: whoever kept the clause's records here in the year before the day (`record_keepers`).
"""

from collections.abc import Collection, Iterable
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.clauses import VERTICAL_AUDIT_CLAUSES, clause_label
from slim_quality.domain.internal_audits import AuditEntry, InternalAudit, LineOutcome, check_plan
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.persistence import store
from slim_quality.service import quality as q


def _since(at: datetime | None, start: date) -> bool:
    return at is not None and at.date() >= start


def record_keepers(session: Session, clause: str, on: date) -> frozenset[str]:
    """Who wrote this clause's records in slim-quality in the year before `on` (entries, signatures,
    registrations): they don't audit it."""
    start = on - timedelta(days=365)
    ids: set[str] = set()

    def entries(items: Iterable, header) -> None:
        for item in items:
            by, at = header(item)
            if _since(at, start):
                ids.add(by.id)
            ids.update(e.by.id for e in item.entries if _since(e.at, start))

    if clause == "6.2":
        entries(store.list_authorisations(session), lambda a: (a.granted_by, a.granted_at))
    elif clause == "6.4":
        entries(store.list_equipment(session), lambda e: (e.registered_by, e.registered_at))
    elif clause == "6.5":
        entries(store.list_lots(session), lambda lot: (lot.registered_by, lot.registered_at))
    elif clause == "7.6":
        ids.update(e.signed_by.id for e in store.uncertainty_estimates(session) if _since(e.signed_at, start))
    elif clause == "7.7":
        entries(store.list_pt_rounds(session), lambda r: (r.registered_by, r.registered_at))
        ids.update(r.by.id for r in store.reviews(session) if _since(r.at, start))
    elif clause == "7.9":
        for c in store.list_complaints(session):
            ids.update(e.by.id for e in c.entries if _since(e.at, start))
    elif clause in ("7.10", "8.7"):
        entries(store.list_ncs(session), lambda nc: (nc.raised_by, nc.raised_at))
    elif clause == "7.11":
        ids.update(s.signed_by.id for s in store.validation_signatures(session) if _since(s.signed_at, start))
    elif clause == "8.5":
        entries(store.list_risks(session), lambda r: (r.raised_by, r.raised_at))
    elif clause == "8.8":
        ids.update(
            a.planned_by.id for a in store.list_internal_audits(session) if _since(a.planned_at, start)
        )
    elif clause == "8.9":
        ids.update(r.signed_by.id for r in store.management_reviews(session) if _since(r.signed_at, start))
    return frozenset(ids)


def plan(session: Session, audit: InternalAudit, involved: Collection[str] = ()) -> InternalAudit:
    """`involved`: the host's people with a hand in the clause; slim-quality adds its own."""
    check_plan(audit, set(involved) | record_keepers(session, audit.clause, audit.planned_for))
    return store.add_internal_audit(session, audit)


def add_entry(
    session: Session, audit_id: int, entry: AuditEntry, involved: Collection[str] = ()
) -> InternalAudit:
    store.lock_internal_audit(session, audit_id)
    audit = store.get_internal_audit(session, audit_id)
    day = entry.on or audit.due
    audit.check(entry, set(involved) | record_keepers(session, audit.clause, day))
    return store.add_internal_audit_entry(session, audit_id, entry)


def programme(session: Session, year: int) -> list[InternalAudit]:
    return store.list_internal_audits(session, year=year)


def last_audited(session: Session) -> dict[str, date]:
    """Per clause, the last day it was audited: an internal audit of it, or a vertical audit (D-113) for
    the clauses one covers."""
    found: dict[str, date] = {}

    def seen(clause: str, day: date) -> None:
        if clause not in found or day > found[clause]:
            found[clause] = day

    for audit in store.list_internal_audits(session):
        if audit.done_on is not None:
            seen(audit.clause, audit.done_on)
    for record in store.audits(session):
        if record.at is not None:
            for clause in VERTICAL_AUDIT_CLAUSES:
                seen(clause, record.at.date())
    return found


def findings_without_nc(session: Session) -> dict[str, list[tuple[InternalAudit, int]]]:
    """Per clause, the internal-audit findings no NC was raised from: (audit, line number)."""
    raised = {nc.source_ref for nc in store.list_ncs(session) if nc.source == NcSource.AUDIT}
    found: dict[str, list[tuple[InternalAudit, int]]] = {}
    for audit in store.list_internal_audits(session):
        for n, _ in audit.lines_of(LineOutcome.FINDING):
            if audit.finding_ref(n) not in raised:
                found.setdefault(audit.clause, []).append((audit, n))
    return found


def finding_nc(session: Session, audit_id: int, line_no: int, actor: QualityActor) -> Nonconformity:
    """An NC raised from a finding, once (as D-114: a visitor's click, never by itself)."""
    audit = store.get_internal_audit(session, audit_id)
    line = next((line for n, line in audit.lines_of(LineOutcome.FINDING) if n == line_no), None)
    if line is None:
        raise store.NotFound(f"{audit.number} has no finding on line {line_no}")
    ref = audit.finding_ref(line_no)
    if any(nc.source_ref == ref for nc in store.list_ncs(session) if nc.source == NcSource.AUDIT):
        raise q.NotAllowed(f"an NC was already raised from {audit.number} line {line_no}")
    return q.raise_nc(
        session,
        f"{audit.number} finding: {line.checked}"[:200],
        f"Internal audit of {clause_label(audit.clause)} on {audit.done_on} by {audit.auditor.display}, "
        f"{line.clause}: {line.checked}. Evidence: {line.evidence}",
        actor,
        NcSource.AUDIT,
        ref,
    )
