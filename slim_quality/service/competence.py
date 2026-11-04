"""Competence (D-132): the authorisation register and the after-the-fact check. Flushes; the host commits.
The host gathers the acts from every module (it is where they meet) and hands them over."""

from collections.abc import Callable, Iterable
from datetime import date, datetime

from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.competence import (
    ActRecord,
    Authorisation,
    AuthorisationEntry,
    Finding,
    check_grant,
    outside_authorisation,
)
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.persistence import store
from slim_quality.service import quality as q


def grant(session: Session, authorisation: Authorisation) -> Authorisation:
    check_grant(authorisation)
    return store.add_authorisation(session, authorisation)


def add_entry(session: Session, authorisation_id: int, entry: AuthorisationEntry) -> Authorisation:
    store.lock_authorisation(session, authorisation_id)
    store.get_authorisation(session, authorisation_id).check(entry)
    return store.add_authorisation_entry(session, authorisation_id, entry)


def register(session: Session, as_of: datetime | None = None) -> list[Authorisation]:
    return store.list_authorisations(session, as_of=as_of)


def findings(
    session: Session,
    acts: Iterable[ActRecord],
    as_of: datetime | None = None,
    day_of: Callable[[datetime], date] = datetime.date,
) -> tuple[Finding, ...]:
    """The acts the register (as recorded by `as_of`) doesn't cover on their day; `day_of`: the lab's day
    of an instant."""
    return outside_authorisation(acts, register(session, as_of), day_of)


def finding_nc(session: Session, finding: Finding, actor: QualityActor) -> Nonconformity:
    """An NC a visitor raises from a finding, never by itself (as D-114)."""
    act = finding.act
    return q.raise_nc(
        session,
        f"{act.person.display}: {act.act.label.lower()} outside authorisation ({act.label})",
        f"On {act.on}, {act.person.display} did '{act.act.label}' on {act.label}: {finding.reason}. Was "
        "the person competent? Which released results does it touch?",
        actor,
        NcSource.COMPETENCE,
        act.ref,
    )
