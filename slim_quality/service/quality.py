"""slim-quality's service: what the host calls. Flushes; the host owns the transaction (commits).

* Charts: `charts()` runs the profile in force over the host's QC series (`QcSource`), with the
  approved limits; `overview()` is the at-a-glance list; `backtest()` compares a candidate profile
  with the one in force over the same history before anyone switches a tool on.
* Change control: `propose_profile()` / `propose_limits()` by a quality user or manager;
  `decide()` by a manager. Approving adds a profile version or a limit set; nothing is edited.
* Records: events on an instrument, notes on a point, the signed periodic QC review.
* Audits and nonconformities (D-113, D-114): `record_audit()`, `raise_nc()`, `add_nc_entry()`;
  `context_at()` is the QC state an audit shows for a run, `flags_since()` an effectiveness check's
  evidence.
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.audits import AuditOutcome, AuditRecord
from slim_quality.domain.changes import ChangeRequest, NotAllowed
from slim_quality.domain.enums import ChangeKind, EventKind, LimitMethod, Severity
from slim_quality.domain.flags import Flag
from slim_quality.domain.limits import Limits, LimitSet, baseline
from slim_quality.domain.nonconformity import NcEntry, NcSource, Nonconformity
from slim_quality.domain.ports import QcSource
from slim_quality.domain.profile import Profile
from slim_quality.domain.records import PointNote, QcReview, QualityEvent
from slim_quality.domain.series import Series, SeriesKey
from slim_quality.monitors.runner import Chart, check_profile, evaluate
from slim_quality.persistence import store

STARTER = Profile.model_validate(
    json.loads((Path(__file__).parents[1] / "profiles" / "starter.json").read_text())
)


# --- charts -------------------------------------------------------------------------------------


def current_profile(session: Session, as_of: datetime | None = None) -> Profile:
    """The newest approved profile, or the starter profile (version 0) until one is approved."""
    return store.profile_as_of(session, as_of) or STARTER


def charts(
    session: Session,
    source: QcSource,
    since: datetime | None = None,
    as_of: datetime | None = None,
    profile: Profile | None = None,
) -> tuple[Chart, ...]:
    series = source.series(since=since, as_of=as_of)
    return evaluate(series, profile or current_profile(session, as_of), store.limit_sets(session, as_of))


def chart(session: Session, source: QcSource, slug: str, as_of: datetime | None = None) -> Chart:
    for found in charts(session, source, as_of=as_of):
        if found.series.key.slug == slug:
            return found
    raise store.NotFound(f"no series {slug}")


@dataclass(frozen=True)
class Status:
    chart: Chart
    worst: Severity | None  # of the flags in the period
    flags: int
    days_to_limit: Decimal | None


def overview(found: Sequence[Chart], now: datetime, days: int = 30) -> list[Status]:
    """Each series with its worst flag of the last `days` days, worst first."""
    since = now - timedelta(days=days)
    statuses = []
    for c in found:
        recent = [f for f in c.flags if f.at >= since]
        worst = max((f.severity for f in recent), default=None)
        days_left = c.trend.days_to_limit if c.trend is not None else None
        statuses.append(Status(chart=c, worst=worst, flags=len(recent), days_to_limit=days_left))
    return sorted(statuses, key=lambda s: (-(s.worst or 0), -s.flags, s.chart.series.key.label))


def flag_summary(found: Sequence[Chart], start: datetime, end: datetime) -> dict[str, int]:
    counts = Counter(f.severity.name for c in found for f in c.flags if start <= f.at <= end)
    return {s.name: counts.get(s.name, 0) for s in Severity}


@dataclass(frozen=True)
class BacktestRow:
    tool: str
    code: str
    in_force: int
    candidate: int


def backtest(
    session: Session, source: QcSource, candidate: Profile, since: datetime | None = None
) -> list[BacktestRow]:
    """How many flags each tool and code would have raised over the same history: the profile in force
    against the candidate. Nothing is stored."""
    check_profile(candidate)
    series = source.series(since=since)
    sets = store.limit_sets(session)
    now = Counter((f.tool, f.code) for c in evaluate(series, current_profile(session), sets) for f in c.flags)
    then = Counter((f.tool, f.code) for c in evaluate(series, candidate, sets) for f in c.flags)
    return [
        BacktestRow(tool, code, now[(tool, code)], then[(tool, code)])
        for tool, code in sorted(now.keys() | then.keys())
    ]


# --- change control -----------------------------------------------------------------------------


class ChangeConflict(RuntimeError):
    """The change can't be decided as it stands: the profile moved on since it was proposed, or someone
    else decided it first. Nothing was written; propose again from the profile in force."""


def propose_profile(session: Session, profile: Profile, reason: str, actor: QualityActor) -> ChangeRequest:
    """A proposal records the version it was made from (its `version`), so approving it can never undo
    a change approved in between (review Q1)."""
    check_profile(profile)
    based_on = profile.model_copy(update={"version": current_profile(session).version})
    change = ChangeRequest(kind=ChangeKind.PROFILE, reason=reason, proposed_by=actor, profile=based_on)
    return store.add_change(session, change)


def baseline_limits(series: Series, method: LimitMethod, start: datetime, end: datetime) -> Limits:
    """Limits from the series' judged points measured from `start` to `end`."""
    points = [p for p in series.judged if start <= p.at <= end]
    basis = f"baseline: {len(points)} points, {start:%Y-%m-%d} to {end:%Y-%m-%d}"
    return baseline([p.value for p in points], method, basis=basis)


def propose_limits(
    session: Session,
    key: SeriesKey,
    limits: Limits,
    effective_from: datetime,
    reason: str,
    actor: QualityActor,
) -> ChangeRequest:
    limit_set = LimitSet(
        series=key, limits=limits.model_copy(update={"provisional": False}), effective_from=effective_from
    )
    change = ChangeRequest(kind=ChangeKind.LIMITS, reason=reason, proposed_by=actor, limit_set=limit_set)
    return store.add_change(session, change)


def decide(session: Session, change_id: int, approved: bool, note: str, actor: QualityActor) -> ChangeRequest:
    change = store.get_change(session, change_id)
    decision = change.decide(actor, approved, note)
    in_force = current_profile(session).version
    if approved and change.profile is not None and change.profile.version != in_force:
        raise ChangeConflict(
            f"proposed on profile v{change.profile.version}, but v{in_force} is in force now: "
            "reject it and propose again from the current profile"
        )
    try:
        with session.begin_nested():
            return store.add_decision(session, change, decision)
    except IntegrityError:
        raise ChangeConflict("someone else decided this change (or approved a profile) first") from None


# --- records ------------------------------------------------------------------------------------


def record_event(
    session: Session,
    kind: EventKind,
    instrument: str,
    at: datetime,
    note: str,
    actor: QualityActor,
    analyte: str | None = None,
) -> QualityEvent:
    event = QualityEvent(
        kind=kind, instrument=instrument, analyte=analyte or None, at=at, note=note, by=actor
    )
    return store.add_event(session, event)


def add_note(session: Session, slug: str, ref: str, note: str, actor: QualityActor) -> PointNote:
    return store.add_note(session, PointNote(series=slug, ref=ref, note=note, by=actor))


def sign_review(
    session: Session,
    source: QcSource,
    period_from: date,
    period_to: date,
    comment: str,
    actor: QualityActor,
    tz=None,
) -> QcReview:
    """The manager signs the period's QC review; the flag counts they saw are kept with it."""
    if not actor.is_manager:
        raise NotAllowed("only a quality manager signs a QC review")
    zone = tz or datetime.now().astimezone().tzinfo
    start = datetime.combine(period_from, time.min, zone)
    end = datetime.combine(period_to, time.max, zone)
    summary = flag_summary(charts(session, source), start, end)
    review = QcReview(
        period_from=period_from, period_to=period_to, summary=summary, comment=comment, by=actor
    )
    return store.add_review(session, review)


# --- audits and nonconformities (D-113, D-114) --------------------------------------------------------


@dataclass(frozen=True)
class ContextLine:
    chart: Chart
    limits: Limits | None  # in force at the moment asked about
    flags: tuple[Flag, ...]  # within the days around it


def context_at(
    found: Sequence[Chart], instrument: str, analyte: str, at: datetime, days: int = 7
) -> list[ContextLine]:
    """The QC around one moment on one instrument and analyte (and the instrument's internal standard):
    the limits in force then and the flags from `days` before to a day after. Pass charts drawn `as_of`
    the moment to see them as they stood."""
    start, end = at - timedelta(days=days), at + timedelta(days=1)
    lines = []
    for c in found:
        key = c.series.key
        if key.instrument != instrument or key.analyte not in (analyte, ""):
            continue
        before = [i for i, p in enumerate(c.series.points) if p.at <= at]
        limits = c.limits[before[-1]] if before else None
        flags = tuple(f for f in c.flags if start <= f.at <= end)
        lines.append(ContextLine(chart=c, limits=limits, flags=flags))
    return lines


def flags_since(found: Sequence[Chart], instrument: str, analyte: str | None, since: datetime) -> list[Flag]:
    """Every flag on the instrument (one analyte and the internal standard, or all) since a moment,
    oldest first: what an effectiveness check looks at."""
    return sorted(
        (
            f
            for c in found
            if c.series.key.instrument == instrument
            and (analyte is None or c.series.key.analyte in (analyte, ""))
            for f in c.flags
            if f.at >= since
        ),
        key=lambda f: f.at,
    )


def record_audit(
    session: Session,
    subject: str,
    subject_ref: str,
    outcome: AuditOutcome,
    findings: str,
    actor: QualityActor,
) -> AuditRecord:
    record = AuditRecord(
        subject=subject, subject_ref=subject_ref, outcome=outcome, findings=findings, by=actor
    )
    return store.add_audit(session, record)


def raise_nc(
    session: Session,
    title: str,
    description: str,
    actor: QualityActor,
    source: NcSource = NcSource.MANUAL,
    source_ref: str = "",
    instrument: str | None = None,
    analyte: str | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> Nonconformity:
    nc = Nonconformity(
        title=title,
        description=description,
        source=source,
        source_ref=source_ref,
        instrument=instrument or None,
        analyte=analyte or None,
        window_from=None if window is None else window[0],
        window_to=None if window is None else window[1],
        raised_by=actor,
    )
    return store.add_nc(session, nc)


def add_nc_entry(session: Session, nc_id: int, entry: NcEntry) -> Nonconformity:
    """Adds an entry the facts allow (`Nonconformity.check`); the state follows."""
    nc = store.get_nc(session, nc_id)
    nc.check(entry)
    return store.add_nc_entry(session, nc_id, entry)
