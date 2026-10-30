"""The repositories: the only code that touches slim-quality's tables. They flush; the caller commits.
Every read takes `as_of` (the database clock), so the rules can be read as they stood."""

from datetime import datetime

from sqlalchemy import Column, select
from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.audits import AuditOutcome, AuditRecord
from slim_quality.domain.changes import ChangeRequest, Decision
from slim_quality.domain.enums import ChangeKind, EventKind
from slim_quality.domain.limits import LimitSet
from slim_quality.domain.nonconformity import NcEntry, NcSource, Nonconformity
from slim_quality.domain.profile import Profile
from slim_quality.domain.records import PointNote, QcReview, QualityEvent
from slim_quality.persistence.tables import (
    quality_audits,
    quality_changes,
    quality_decisions,
    quality_events,
    quality_limit_sets,
    quality_nc_entries,
    quality_ncs,
    quality_notes,
    quality_profiles,
    quality_reviews,
)
from slim_quality.persistence.types import db_now


class NotFound(LookupError):
    pass


def as_of_filter(column: Column, as_of: datetime | None):
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return column <= as_of if as_of is not None else column.isnot(None)


def database_time(session: Session) -> datetime:
    return session.scalar(select(db_now()))


def _actor(data) -> QualityActor:
    return QualityActor.model_validate(data)


# --- changes ------------------------------------------------------------------------------------


def add_change(session: Session, change: ChangeRequest) -> ChangeRequest:
    payload = change.profile if change.kind == ChangeKind.PROFILE else change.limit_set
    change_id = session.execute(
        quality_changes.insert().values(
            kind=int(change.kind),
            payload=payload.model_dump(mode="json"),
            reason=change.reason,
            proposed_by_id=change.proposed_by.id,
            proposed_by=change.proposed_by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return get_change(session, change_id)


def add_decision(session: Session, change: ChangeRequest, decision: Decision) -> ChangeRequest:
    session.execute(
        quality_decisions.insert().values(
            change_id=change.id,
            approved=decision.approved,
            note=decision.note,
            by_id=decision.by.id,
            by=decision.by.model_dump(mode="json"),
        )
    )
    if decision.approved and change.kind == ChangeKind.PROFILE:
        version = (latest_profile_version(session) or 0) + 1
        profile = change.profile.model_copy(update={"version": version})
        session.execute(
            quality_profiles.insert().values(
                version=version, change_id=change.id, profile=profile.model_dump(mode="json")
            )
        )
    elif decision.approved:
        limit_set = change.limit_set
        session.execute(
            quality_limit_sets.insert().values(
                change_id=change.id,
                series=limit_set.series.slug,
                limit_set=limit_set.model_dump(mode="json"),
                effective_from=limit_set.effective_from,
            )
        )
    session.flush()
    return get_change(session, change.id)


def _changes(session: Session, *where) -> list[ChangeRequest]:
    rows = session.execute(
        select(
            quality_changes,
            quality_decisions.c.approved,
            quality_decisions.c.note,
            quality_decisions.c.by,
            quality_decisions.c.created_at.label("decided_at"),
        )
        .outerjoin(quality_decisions, quality_decisions.c.change_id == quality_changes.c.id)
        .where(*where)
        .order_by(quality_changes.c.id.desc())
    ).all()
    found = []
    for row in rows:
        kind = ChangeKind(row.kind)
        decision = None
        if row.approved is not None:
            decision = Decision(approved=row.approved, note=row.note, by=_actor(row.by), at=row.decided_at)
        found.append(
            ChangeRequest(
                id=row.id,
                kind=kind,
                reason=row.reason,
                proposed_by=_actor(row.proposed_by),
                proposed_at=row.created_at,
                profile=Profile.model_validate(row.payload) if kind == ChangeKind.PROFILE else None,
                limit_set=LimitSet.model_validate(row.payload) if kind == ChangeKind.LIMITS else None,
                decision=decision,
            )
        )
    return found


def get_change(session: Session, change_id: int) -> ChangeRequest:
    found = _changes(session, quality_changes.c.id == change_id)
    if not found:
        raise NotFound(f"no change {change_id}")
    return found[0]


def list_changes(session: Session, open_only: bool = False) -> list[ChangeRequest]:
    where = [quality_decisions.c.id.is_(None)] if open_only else []
    return _changes(session, *where)


# --- the rules in force -------------------------------------------------------------------------


def latest_profile_version(session: Session) -> int | None:
    return session.scalar(
        select(quality_profiles.c.version).order_by(quality_profiles.c.version.desc()).limit(1)
    )


def profile_as_of(session: Session, as_of: datetime | None = None) -> Profile | None:
    data = session.scalar(
        select(quality_profiles.c.profile)
        .where(as_of_filter(quality_profiles.c.created_at, as_of))
        .order_by(quality_profiles.c.version.desc())
        .limit(1)
    )
    return None if data is None else Profile.model_validate(data)


def limit_sets(session: Session, as_of: datetime | None = None, series: str | None = None) -> list[LimitSet]:
    where = [as_of_filter(quality_limit_sets.c.created_at, as_of)]
    if series is not None:
        where.append(quality_limit_sets.c.series == series)
    rows = session.execute(
        select(quality_limit_sets)
        .where(*where)
        .order_by(quality_limit_sets.c.effective_from, quality_limit_sets.c.id)
    ).all()
    return [LimitSet.model_validate(row.limit_set).model_copy(update={"id": row.id}) for row in rows]


# --- records ------------------------------------------------------------------------------------


def add_event(session: Session, event: QualityEvent) -> QualityEvent:
    event_id = session.execute(
        quality_events.insert().values(
            kind=int(event.kind),
            instrument=event.instrument,
            analyte=event.analyte,
            at=event.at,
            note=event.note,
            by_id=event.by.id,
            by=event.by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return event.model_copy(update={"id": event_id})


def events(
    session: Session, instrument: str | None = None, as_of: datetime | None = None
) -> list[QualityEvent]:
    where = [as_of_filter(quality_events.c.created_at, as_of)]
    if instrument is not None:
        where.append(quality_events.c.instrument == instrument)
    rows = session.execute(
        select(quality_events).where(*where).order_by(quality_events.c.at, quality_events.c.id)
    )
    return [
        QualityEvent(
            id=r.id,
            kind=EventKind(r.kind),
            instrument=r.instrument,
            analyte=r.analyte,
            at=r.at,
            note=r.note,
            by=_actor(r.by),
        )
        for r in rows
    ]


def add_note(session: Session, note: PointNote) -> PointNote:
    note_id = session.execute(
        quality_notes.insert().values(
            series=note.series,
            ref=note.ref,
            note=note.note,
            by_id=note.by.id,
            by=note.by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return notes(session, note.series, ids=(note_id,))[0]


def notes(session: Session, series: str, as_of: datetime | None = None, ids=()) -> list[PointNote]:
    where = [quality_notes.c.series == series, as_of_filter(quality_notes.c.created_at, as_of)]
    if ids:
        where.append(quality_notes.c.id.in_(ids))
    rows = session.execute(select(quality_notes).where(*where).order_by(quality_notes.c.id))
    return [
        PointNote(id=r.id, series=r.series, ref=r.ref, note=r.note, by=_actor(r.by), at=r.created_at)
        for r in rows
    ]


def add_review(session: Session, review: QcReview) -> QcReview:
    review_id = session.execute(
        quality_reviews.insert().values(
            period_from=review.period_from,
            period_to=review.period_to,
            summary=review.summary,
            comment=review.comment,
            by_id=review.by.id,
            by=review.by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return next(r for r in reviews(session) if r.id == review_id)


def reviews(session: Session, as_of: datetime | None = None) -> list[QcReview]:
    rows = session.execute(
        select(quality_reviews)
        .where(as_of_filter(quality_reviews.c.created_at, as_of))
        .order_by(quality_reviews.c.period_to.desc(), quality_reviews.c.id.desc())
    )
    return [
        QcReview(
            id=r.id,
            period_from=r.period_from,
            period_to=r.period_to,
            summary=r.summary,
            comment=r.comment,
            by=_actor(r.by),
            at=r.created_at,
        )
        for r in rows
    ]


# --- audits and nonconformities (D-113, D-114) --------------------------------------------------------


def add_audit(session: Session, record: AuditRecord) -> AuditRecord:
    audit_id = session.execute(
        quality_audits.insert().values(
            subject=record.subject,
            subject_ref=record.subject_ref,
            outcome=int(record.outcome),
            findings=record.findings,
            by_id=record.by.id,
            by=record.by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return audits(session, ids=(audit_id,))[0]


def audits(
    session: Session, subject_ref: str | None = None, as_of: datetime | None = None, ids=()
) -> list[AuditRecord]:
    """Newest first."""
    where = [as_of_filter(quality_audits.c.created_at, as_of)]
    if subject_ref is not None:
        where.append(quality_audits.c.subject_ref == subject_ref)
    if ids:
        where.append(quality_audits.c.id.in_(ids))
    rows = session.execute(select(quality_audits).where(*where).order_by(quality_audits.c.id.desc()))
    return [
        AuditRecord(
            id=r.id,
            subject=r.subject,
            subject_ref=r.subject_ref,
            outcome=AuditOutcome(r.outcome),
            findings=r.findings,
            by=_actor(r.by),
            at=r.created_at,
        )
        for r in rows
    ]


def add_nc(session: Session, nc: Nonconformity) -> Nonconformity:
    nc_id = session.execute(
        quality_ncs.insert().values(
            title=nc.title,
            description=nc.description,
            source=int(nc.source),
            source_ref=nc.source_ref,
            instrument=nc.instrument,
            analyte=nc.analyte,
            window_from=nc.window_from,
            window_to=nc.window_to,
            by_id=nc.raised_by.id,
            by=nc.raised_by.model_dump(mode="json"),
        )
    ).inserted_primary_key[0]
    return get_nc(session, nc_id)


def add_nc_entry(session: Session, nc_id: int, entry: NcEntry) -> Nonconformity:
    session.execute(
        quality_nc_entries.insert().values(
            nc_id=nc_id,
            kind=int(entry.kind),
            entry=entry.model_dump(mode="json", exclude={"at"}),
            by_id=entry.by.id,
            by=entry.by.model_dump(mode="json"),
        )
    )
    return get_nc(session, nc_id)


def get_nc(session: Session, nc_id: int, as_of: datetime | None = None) -> Nonconformity:
    found = list_ncs(session, as_of=as_of, ids=(nc_id,))
    if not found:
        raise NotFound(f"no nonconformity {nc_id}")
    return found[0]


def list_ncs(session: Session, as_of: datetime | None = None, ids=()) -> list[Nonconformity]:
    """Newest first, each with its entries, oldest first."""
    where = [as_of_filter(quality_ncs.c.created_at, as_of)]
    if ids:
        where.append(quality_ncs.c.id.in_(ids))
    rows = session.execute(select(quality_ncs).where(*where).order_by(quality_ncs.c.id.desc())).all()
    if not rows:
        return []
    entries: dict[int, list[NcEntry]] = {}
    for e in session.execute(
        select(quality_nc_entries)
        .where(
            quality_nc_entries.c.nc_id.in_([r.id for r in rows]),
            as_of_filter(quality_nc_entries.c.created_at, as_of),
        )
        .order_by(quality_nc_entries.c.id)
    ):
        entries.setdefault(e.nc_id, []).append(NcEntry.model_validate(e.entry | {"at": e.created_at}))
    return [
        Nonconformity(
            id=r.id,
            title=r.title,
            description=r.description,
            source=NcSource(r.source),
            source_ref=r.source_ref,
            instrument=r.instrument,
            analyte=r.analyte,
            window_from=r.window_from,
            window_to=r.window_to,
            raised_by=_actor(r.by),
            raised_at=r.created_at,
            entries=tuple(entries.get(r.id, ())),
        )
        for r in rows
    ]
