"""The service on a migrated database: change control, the rules as they stood, records, backtest."""

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from slim_quality.demo.history import DemoSource
from slim_quality.domain.changes import NotAllowed
from slim_quality.domain.enums import ChangeStatus, EventKind, LimitMethod, Measure, Severity
from slim_quality.domain.limits import Limits
from slim_quality.monitors.runner import ProfileError
from slim_quality.persistence import store
from slim_quality.service import quality as q
from tests.factories import MANAGER, USER

END = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SOURCE = DemoSource(END)


def instant(session):
    time.sleep(0.002)
    moment = store.database_time(session)
    time.sleep(0.002)
    return moment


def without_ewma(profile):
    tools = tuple(t.model_copy(update={"enabled": False}) if t.tool == "ewma@1" else t for t in profile.tools)
    return profile.model_copy(update={"tools": tools})


def test_the_starter_profile_until_a_change_is_approved(session):
    assert q.current_profile(session).version == 0
    change = q.propose_profile(session, without_ewma(q.STARTER), "EWMA too noisy for us", USER)
    assert change.status == ChangeStatus.PROPOSED and store.list_changes(session, open_only=True) == [change]
    before = instant(session)
    decided = q.decide(session, change.id, True, "agreed", MANAGER)
    assert decided.status == ChangeStatus.APPROVED and not decided.self_approved
    assert q.current_profile(session).version == 1
    assert not q.current_profile(session).setting("ewma@1").enabled
    assert q.current_profile(session, as_of=before).version == 0  # the rules as they stood
    assert store.list_changes(session, open_only=True) == []
    second = q.decide(session, q.propose_profile(session, q.STARTER, "back", MANAGER).id, True, "", MANAGER)
    assert second.self_approved and q.current_profile(session).version == 2


def test_proposals_are_checked_and_decisions_are_a_managers(session):
    bad = q.STARTER.model_copy(update={"tools": (q.STARTER.tools[0].model_copy(update={"tool": "nope@1"}),)})
    with pytest.raises(ProfileError):
        q.propose_profile(session, bad, "x", USER)
    change = q.propose_profile(session, q.STARTER, "x", USER)
    with pytest.raises(NotAllowed):
        q.decide(session, change.id, True, "", USER)
    rejected = q.decide(session, change.id, False, "not now", MANAGER)
    assert rejected.status == ChangeStatus.REJECTED and q.current_profile(session).version == 0
    with pytest.raises(store.NotFound):
        q.decide(session, 999, True, "", MANAGER)


def test_limits_from_a_baseline_take_over_once_approved(session):
    chart = next(c for c in q.charts(session, SOURCE) if c.series.key.measure == Measure.ISTD_RECOVERY)
    assert chart.current.provisional
    start, end = chart.series.points[0].at, chart.series.points[59].at
    limits = q.baseline_limits(chart.series, LimitMethod.ROBUST, start, end)
    assert limits.n == 60 and "baseline: 60 points" in limits.basis
    change = q.propose_limits(session, chart.series.key, limits, start, "a longer baseline", USER)
    assert not change.limit_set.limits.provisional
    q.decide(session, change.id, True, "", MANAGER)
    redrawn = q.chart(session, SOURCE, chart.series.key.slug)
    assert redrawn.current.n == 60 and not redrawn.current.provisional
    assert store.limit_sets(session, series=chart.series.key.slug)[0].id is not None
    with pytest.raises(store.NotFound):
        q.chart(session, SOURCE, "1|nope||||")


def test_entered_limits_for_a_certificate(session):
    key = q.charts(session, SOURCE)[0].series.key
    limits = Limits(method=LimitMethod.CERTIFICATE, center=Decimal("1"), sd=Decimal("0.03"), basis="cert 123")
    change = q.propose_limits(session, key, limits, END - timedelta(days=30), "certificate", MANAGER)
    assert store.get_change(session, change.id).limit_set.limits.basis == "cert 123"


def test_overview_puts_the_worst_first_and_backtest_compares(session):
    found = q.charts(session, SOURCE)
    statuses = q.overview(found, END, days=30)
    assert statuses[0].worst == Severity.ACTION and statuses[0].flags >= statuses[-1].flags
    assert any(s.days_to_limit is not None for s in statuses)
    rows = {(r.tool, r.code): r for r in q.backtest(session, SOURCE, without_ewma(q.STARTER))}
    ewma = rows[("ewma@1", "EWMA_LOW")]
    assert ewma.in_force > 0 and ewma.candidate == 0
    recent = q.charts(session, SOURCE, since=END - timedelta(days=10))
    assert max(len(c.series.points) for c in recent) < max(len(c.series.points) for c in found)


def test_events_notes_and_a_signed_review(session):
    event = q.record_event(session, EventKind.MAINTENANCE, "ICP-MS 1", END, "cones cleaned", USER, analyte="")
    assert event.analyte is None and store.events(session, "ICP-MS 1") == [event]
    assert store.events(session, "ICP-MS 2") == []
    slug = q.charts(session, SOURCE)[0].series.key.slug
    note = q.add_note(session, slug, "p1", "bubble in the line", USER)
    assert store.notes(session, slug) == [note] and note.at is not None
    with pytest.raises(NotAllowed):
        q.sign_review(session, SOURCE, date(2026, 10, 1), date(2026, 10, 28), "ok", USER)
    review = q.sign_review(
        session, SOURCE, date(2026, 10, 1), date(2026, 10, 28), "drift on ICP-MS 1", MANAGER, UTC
    )
    assert review.summary["ACTION"] > 0 and set(review.summary) == {"INFO", "WARN", "ACTION"}
    assert store.reviews(session) == [review]
    with pytest.raises(ValueError, match="timezone-aware"):
        store.reviews(session, as_of=datetime(2026, 1, 1))
