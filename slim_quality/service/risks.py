"""Risks and opportunities (D-134): the register. Flushes; the host commits."""

from datetime import date

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.domain.risks import Risk, RiskEntry
from slim_quality.persistence import store
from slim_quality.service import quality as q


def raise_risk(session: Session, risk: Risk) -> Risk:
    return store.add_risk(session, risk)


def add_entry(session: Session, risk_id: int, entry: RiskEntry) -> Risk:
    store.lock_risk(session, risk_id)
    store.get_risk(session, risk_id).check(entry)
    return store.add_risk_entry(session, risk_id, entry)


def register(
    session: Session, today: date, include_closed: bool = False
) -> list[tuple[Risk, tuple[str, ...]]]:
    """Each risk with its flags; flagged first, then by score, highest first."""
    policy = q.current_profile(session).risk
    rows = [(r, r.flags(today, policy)) for r in store.list_risks(session) if include_closed or not r.closed]
    rows.sort(key=lambda row: (row[0].closed, not row[1], -(row[0].score or 0), row[0].id))
    return rows


def risk_nc(session: Session, risk_id: int, actor: QualityActor) -> Nonconformity:
    """An NC a visitor raises when a risk happened, never by itself (as D-114)."""
    risk = store.get_risk(session, risk_id)
    return q.raise_nc(
        session,
        f"{risk.number} happened: {risk.title}"[:200],
        f"The registered {risk.kind.label.lower()} {risk.number} ({risk.source.label}, owner {risk.owner}) "
        f"happened. {risk.description} What did it touch, and why did the planned actions not stop "
        "it?",
        actor,
        NcSource.RISK,
        f"risk:{risk.id}",
    )
