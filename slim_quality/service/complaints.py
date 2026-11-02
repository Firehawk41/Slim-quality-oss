"""Complaints (D-130). Flushes; the host commits. Whoever had a hand in the request's lab work is the
host's to work out (it composes every module's read API, as for the vertical audit); slim-quality refuses
them the outcome."""

from collections.abc import Collection

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.complaints import Complaint, ComplaintEntry, Party
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.persistence import store
from slim_quality.service import quality as q


def raise_complaint(
    session: Session,
    tr_number: str,
    customer_id: str,
    text: str,
    raised_by: Party,
    by_staff: bool = False,
    issue_id: int | None = None,
) -> Complaint:
    complaint = Complaint(
        tr_number=tr_number,
        customer_id=customer_id,
        issue_id=issue_id,
        text=text.strip(),
        raised_by=raised_by,
        by_staff=by_staff,
    )
    return store.add_complaint(session, complaint)


def add_entry(
    session: Session, complaint_id: int, entry: ComplaintEntry, involved: Collection[str] = ()
) -> Complaint:
    store.lock_complaint(session, complaint_id)
    store.get_complaint(session, complaint_id).check(entry, involved)
    return store.add_complaint_entry(session, complaint_id, entry)


def complaint_nc(session: Session, complaint_id: int, actor: QualityActor) -> Nonconformity:
    complaint = store.get_complaint(session, complaint_id)
    return q.raise_nc(
        session,
        f"Complaint {complaint.number} on {complaint.tr_number}",
        complaint.text,
        actor,
        NcSource.COMPLAINT,
        f"complaint:{complaint.id}",
    )
