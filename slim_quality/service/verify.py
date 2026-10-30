"""`verify` for slim-quality (review 2026-10-30b Q7): proves its records hold together, as slim-data's
`verify` replays results and slim-prep's rechecks the prep book. Read-only.

* Every profile version is numbered 1..n and came from an approved profile change.
* Every limit set came from an approved limits change.
* Every nonconformity's entries, replayed in order, were each allowed when they were added (a manager
  assessed and closed it, an action came after the assessment...), so its state is one the rules reach.
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
    for nc in ncs:
        replayed = nc.model_copy(update={"entries": ()})
        for n, entry in enumerate(nc.entries, start=1):
            try:
                replayed.check(entry)
            except NotAllowed as refused:
                problems.append(f"{nc.number} entry {n} ({entry.kind.name.lower()}): {refused}")
            replayed = replayed.model_copy(update={"entries": (*replayed.entries, entry)})
    return QualityVerify(
        profiles=len(versions), limit_sets=len(sets), nonconformities=len(ncs), problems=tuple(problems)
    )
