"""Turnaround and customer trends (D-133), from the host's request dates and slim-quality's complaints.
Reads only."""

from datetime import date

from sqlalchemy.orm import Session

from slim_quality.domain.complaints import ComplaintState
from slim_quality.domain.ports import TurnaroundSource
from slim_quality.domain.turnaround import CustomerTrend, TurnaroundReport, customer_trends, turnaround
from slim_quality.persistence import store


def turnaround_report(source: TurnaroundSource, today: date, since: date | None = None) -> TurnaroundReport:
    return turnaround(source.requests(since), today, since)


def complaint_counts(session: Session, since: date | None = None) -> dict[str, tuple[int, int]]:
    """Per customer id: (complaints, still open), raised since `since`."""
    counts: dict[str, list[int]] = {}
    for c in store.list_complaints(session):
        if since is not None and c.raised_at is not None and c.raised_at.date() < since:
            continue
        found = counts.setdefault(c.customer_id, [0, 0])
        found[0] += 1
        found[1] += c.state != ComplaintState.CLOSED
    return {k: (v[0], v[1]) for k, v in counts.items()}


def customers(
    session: Session, source: TurnaroundSource, today: date, since: date | None = None
) -> tuple[CustomerTrend, ...]:
    return customer_trends(source.requests(since), today, complaint_counts(session, since), since)
