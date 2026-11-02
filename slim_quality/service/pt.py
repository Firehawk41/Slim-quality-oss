"""Proficiency testing (D-128, D-129): rounds, the lab's submission, the provider's report, the scores.
Flushes; the host commits. The host takes the lab's values from slim-data's released results (it is
where the modules meet); slim-quality keeps them as they were sent."""

from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.nonconformity import NcSource, Nonconformity
from slim_quality.domain.pt import PtClass, PtEntry, PtRound, Score
from slim_quality.persistence import store
from slim_quality.service import quality as q
from slim_quality.service.register import AlreadyRegistered


def register_round(
    session: Session,
    scheme: str,
    round_code: str,
    tr_number: str,
    sample: str,
    analytes: tuple[str, ...],
    due: date,
    actor: QualityActor,
) -> PtRound:
    pt = PtRound(
        scheme=scheme.strip(),
        round_code=round_code.strip(),
        tr_number=tr_number.strip(),
        sample=sample.strip(),
        analytes=analytes,
        due=due,
        registered_by=actor,
    )
    try:
        with session.begin_nested():
            return store.add_pt_round(session, pt)
    except IntegrityError:
        raise AlreadyRegistered(f"{pt.scheme} {pt.round_code} is already registered") from None


def add_entry(session: Session, round_id: int, entry: PtEntry) -> PtRound:
    store.lock_pt_round(session, round_id)
    store.get_pt_round(session, round_id).check(entry)
    return store.add_pt_entry(session, round_id, entry)


def scores(session: Session, pt: PtRound) -> tuple[Score, ...]:
    return pt.scores(q.current_profile(session).pt)


def unsatisfactory_nc(session: Session, round_id: int, analyte: str, actor: QualityActor) -> Nonconformity:
    """The nonconformity a visitor raises from an unsatisfactory score, never by itself (as D-114)."""
    pt = store.get_pt_round(session, round_id)
    score = next((s for s in scores(session, pt) if s.analyte == analyte), None)
    if score is None or score.klass != PtClass.UNSATISFACTORY:
        raise NotAllowed("raise it from an unsatisfactory score")
    return q.raise_nc(
        session,
        f"PT {pt.scheme} {pt.round_code}: {analyte} unsatisfactory (z = {score.shown})",
        f"The lab reported {score.lab.value} {score.lab.unit} on {pt.sample}; the assigned value was "
        f"{score.assigned.assigned} with σ_pt {score.assigned.sigma}. Find the cause; which released "
        f"results does it touch?",
        actor,
        NcSource.PT,
        f"pt:{pt.id}:{analyte}",
    )
