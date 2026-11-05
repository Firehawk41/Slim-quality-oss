"""`verify` for slim-quality (review 2026-10-30b Q7): proves its records hold together, as slim-data's
`verify` replays results and slim-prep's rechecks the prep book. Read-only.

* Every profile version is numbered 1..n and came from an approved profile change.
* Every limit set came from an approved limits change.
* Every nonconformity's entries, replayed in order, were each allowed when they were added (a manager
  assessed and closed it, an action came after the assessment...), so its state is one the rules reach.
* The same replay for risks and internal audits (D-134, D-135); every management review and validation
  signature was signed by a quality manager (D-136, D-137). An auditor's independence depends on who did
  what in the year before, so it is checked when planned and recorded, not replayed here.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_quality.domain.base import Frozen
from slim_quality.domain.changes import NotAllowed
from slim_quality.persistence import store
from slim_quality.persistence.tables import quality_decisions, quality_limit_sets, quality_profiles


class QualityVerify(Frozen):
    profiles: int
    limit_sets: int
    nonconformities: int
    risks: int = 0
    internal_audits: int = 0
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(session: Session) -> QualityVerify:
    problems = []
    approved = {
        row.change_id
        for row in session.execute(select(quality_decisions.c.change_id).where(quality_decisions.c.approved))
    }
    versions = session.execute(
        select(quality_profiles.c.version, quality_profiles.c.change_id).order_by(quality_profiles.c.version)
    ).all()
    if [v.version for v in versions] != list(range(1, len(versions) + 1)):
        problems.append(f"profile versions are not 1..{len(versions)}: {[v.version for v in versions]}")
    problems += [
        f"profile v{v.version} has no approved change ({v.change_id})"
        for v in versions
        if v.change_id not in approved
    ]
    sets = session.execute(select(quality_limit_sets.c.id, quality_limit_sets.c.change_id)).all()
    problems += [
        f"limit set {s.id} has no approved change ({s.change_id})"
        for s in sets
        if s.change_id not in approved
    ]
    ncs = store.list_ncs(session)
    risks = store.list_risks(session)
    audits = store.list_internal_audits(session)
    for record in (*ncs, *risks, *audits):
        problems += _replay(record)
    problems += [
        f"{r.number} was signed by {r.signed_by.display}, not a quality manager"
        for r in store.management_reviews(session)
        if not r.signed_by.is_manager
    ]
    problems += [
        f"validation signature {s.id} was signed by {s.signed_by.display}, not a quality manager"
        for s in store.validation_signatures(session)
        if not s.signed_by.is_manager
    ]
    return QualityVerify(
        profiles=len(versions),
        limit_sets=len(sets),
        nonconformities=len(ncs),
        risks=len(risks),
        internal_audits=len(audits),
        problems=tuple(problems),
    )


def _replay(record) -> list[str]:
    """Each entry, checked against the record as it stood before it."""
    problems = []
    replayed = record.model_copy(update={"entries": ()})
    for n, entry in enumerate(record.entries, start=1):
        try:
            replayed.check(entry)
        except NotAllowed as refused:
            problems.append(f"{record.number} entry {n} ({entry.kind.name.lower()}): {refused}")
        replayed = replayed.model_copy(update={"entries": (*replayed.entries, entry)})
    return problems
