"""Batch 5b's pure parts: uncertainty from QC (D-131), competence (D-132), turnaround and trends (D-133)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from slim_quality.domain.base import NotAllowed
from slim_quality.domain.competence import (
    Act,
    ActRecord,
    Authorisation,
    AuthorisationEntry,
    AuthorisationEntryKind,
    Person,
    check_grant,
    outside_authorisation,
)
from slim_quality.domain.enums import Measure
from slim_quality.domain.profile import Profile, differences
from slim_quality.domain.turnaround import (
    RequestDates,
    TurnaroundState,
    customer_trends,
    turnaround,
    working_days_between,
)
from slim_quality.domain.uncertainty import (
    BiasPoint,
    UncertaintyEstimate,
    UncertaintyKey,
    UncertaintyPolicy,
    bias_from_pt,
    bias_from_recovery,
    work_out,
)
from tests.factories import MANAGER, T0, USER, key, series

KEY = UncertaintyKey(instrument="ICP-1", analyte="Cu")
END = T0 + timedelta(days=60)


def bias(*values):
    return [
        BiasPoint(source="pt", ref=f"pt:{i}:Cu", at=T0 + timedelta(days=i), label=f"R{i}", bias=D(v))
        for i, v in enumerate(values)
    ]


# --- uncertainty (D-131) ------------------------------------------------------------------------------


def test_the_estimate_is_nordtest_worked_unrounded():
    check = series(*(["0.98", "1.02"] * 10))
    found = work_out(
        KEY, check, bias("0.02", "-0.02", "0.02"), D("0.01"), "certificate", T0, END, UncertaintyPolicy()
    )
    u_rw = (D("0.008") / 19).sqrt()
    assert found.n_precision == 20 and found.u_rw == u_rw
    assert found.rms_bias == D("0.02")
    assert found.u_bias == D("0.0005").sqrt()
    assert abs(found.u_c - (D("0.008") / 19 + D("0.0005")).sqrt()) < D("1e-25")
    assert found.expanded == 2 * found.u_c
    assert found.shown == D("6.1") and UncertaintyEstimate.percent(found.u_rw) == D("2.1")
    assert found.precision_series == key().slug and KEY.label == "ICP-1 · Cu"
    assert UncertaintyKey(instrument="I", analyte="Zn", conditions="KED").label == "I · Zn (KED)"
    with pytest.raises(ValidationError, match="ends after it starts"):
        UncertaintyEstimate.model_validate(found.model_dump() | {"window_to": found.window_from})


def test_the_estimate_refuses_too_few_points_and_keeps_to_the_window():
    check = series(*(["1.0", "1.1"] * 10))
    with pytest.raises(NotAllowed, match="19 check points"):
        work_out(
            KEY, check, bias("0", "0", "0"), D(0), "b", T0 + timedelta(hours=1), END, UncertaintyPolicy()
        )
    with pytest.raises(NotAllowed, match="2 CRM or PT"):
        work_out(KEY, check, bias("0", "0"), D(0), "b", T0, END, UncertaintyPolicy())
    excluded = series(*(["1.0", "1.1"] * 10), excluded=(0,))
    with pytest.raises(NotAllowed, match="19 check points"):
        work_out(KEY, excluded, bias("0", "0", "0"), D(0), "b", T0, END, UncertaintyPolicy())
    blank = series(*(["1"] * 20), k=key(measure=Measure.BLANK_LEVEL))
    with pytest.raises(NotAllowed, match="recovery series"):
        work_out(KEY, blank, bias("0"), D(0), "b", T0, END, UncertaintyPolicy())


def test_bias_and_the_policy_in_the_profile():
    assert bias_from_recovery(D("1.03")) == D("0.03")
    assert bias_from_pt(D("10.4"), D("10.0")) == D("0.04")
    with pytest.raises(ValueError, match="assigned value of 0"):
        bias_from_pt(D(1), D(0))
    after = Profile(uncertainty=UncertaintyPolicy(min_precision_points=30, k=D(3)))
    lines = differences(Profile(), after)
    assert "Uncertainty: check points at least: 20 → 30" in lines
    assert "Uncertainty: coverage factor k: 2 → 3" in lines


# --- competence (D-132) -------------------------------------------------------------------------------

ANA = Person(id="u-ana", display="AN")
DAY = date(2026, 6, 10)


def auth(act=Act.REVIEW, method="10_elements", start=date(2026, 6, 1), until=None, granted=None, **kw):
    return Authorisation(
        person=kw.pop("person", ANA), act=act, method=method, valid_from=start, valid_until=until,
        evidence="witnessed run", granted_by=MANAGER,
        granted_at=granted or datetime(2026, 5, 30, tzinfo=UTC), **kw,
    )  # fmt: skip


def act(kind=Act.REVIEW, method="10_elements", on=DAY, ref="r1", person=ANA):
    return ActRecord(person=person, act=kind, method=method, on=on, ref=ref, label="TR00001-01 Cu")


def test_an_authorisation_covers_its_person_act_method_and_days():
    a = auth(until=date(2026, 6, 30))
    assert a.valid_on(DAY) and not a.valid_on(date(2026, 5, 31)) and not a.valid_on(date(2026, 7, 1))
    assert a.matches("u-ana", Act.REVIEW, "10_elements") and not a.matches("u-ana", Act.REVIEW, "anions")
    assert not a.matches("u-ana", Act.RELEASE, "10_elements") and not a.matches(
        "x", Act.REVIEW, "10_elements"
    )
    every = auth(method=None)
    assert every.matches("u-ana", Act.REVIEW, None) and every.method_label == "every method"
    assert str(a) == "AN: Review · 10_elements" and a.last_day == date(2026, 6, 30)
    withdrawn = auth(
        entries=(AuthorisationEntry(kind=AuthorisationEntryKind.WITHDRAWN, on=DAY, note="left", by=MANAGER),)
    )
    assert withdrawn.last_day == DAY - timedelta(days=1) and not withdrawn.valid_on(DAY)
    assert auth().last_day is None
    with pytest.raises(ValidationError, match="ends before it starts"):
        auth(until=date(2026, 5, 1))
    with pytest.raises(ValidationError, match="give the evidence"):
        Authorisation(person=ANA, act=Act.PREP, valid_from=DAY, evidence="  ", granted_by=MANAGER)


def test_only_a_manager_grants_and_never_to_themselves():
    check_grant(auth())
    with pytest.raises(NotAllowed, match="quality manager grants"):
        check_grant(auth().model_copy(update={"granted_by": USER}))
    with pytest.raises(NotAllowed, match="authorises themselves"):
        check_grant(auth(person=Person(id=MANAGER.id, display="QM")))
    note = AuthorisationEntry(kind=AuthorisationEntryKind.NOTE, on=DAY, note="refresher done", by=USER)
    with pytest.raises(NotAllowed, match="keeps the authorisations"):
        auth().check(note)
    gone = AuthorisationEntry(kind=AuthorisationEntryKind.WITHDRAWN, on=DAY, note="left", by=MANAGER)
    withdrawn = auth(entries=(gone,))
    withdrawn.check(note.model_copy(update={"by": MANAGER}))
    with pytest.raises(NotAllowed, match="was withdrawn"):
        withdrawn.check(gone)
    assert AuthorisationEntryKind.WITHDRAWN.label == "Withdrawn" and Act.SIGN.label == "Sign reports"


def test_findings_say_why_and_catch_a_backdated_record():
    register = [
        auth(),
        auth(act=Act.RELEASE, start=date(2026, 6, 20)),
        auth(act=Act.PREP, until=date(2026, 6, 5)),
        auth(act=Act.ENTER, granted=datetime(2026, 6, 15, tzinfo=UTC)),
        auth(act=Act.SIGN, method=None),
    ]
    acts = [
        act(),  # covered
        act(Act.SIGN, None, ref="s1"),  # covered by every method
        act(Act.RELEASE, ref="a"),
        act(Act.PREP, ref="b"),
        act(Act.ENTER, ref="c"),
        act(Act.REVIEW, "anions", ref="d"),
        act(Act.REVIEW, person=Person(id="u-bo", display="BO"), ref="e"),
    ]
    found = {f.act.ref: f for f in outside_authorisation(acts, register)}
    assert set(found) == {"a", "b", "c", "d", "e"}
    assert found["a"].reason == "authorised to release for 10_elements only from 2026-06-20"
    assert found["b"].reason == "the authorisation to prep for 10_elements ended on 2026-06-05"
    assert found["c"].backdated and "recorded on 2026-06-15" in found["c"].reason
    assert found["d"].reason == "AN holds no authorisation to review for anions"
    assert found["e"].reason.startswith("BO holds no authorisation")
    signed = outside_authorisation([act(Act.SIGN, None, ref="z")], [auth(act=Act.PREP)])
    assert signed[0].reason == "AN holds no authorisation to sign reports"
    late_in_the_day = outside_authorisation([act(Act.ENTER, on=date(2026, 6, 14))], register,
                                            day_of=lambda at: (at - timedelta(hours=6)).date())  # fmt: skip
    assert late_in_the_day == ()  # recorded 6/15 00:00 UTC, the lab's 6/14
    unsigned = auth(act=Act.SIGN, method=None).model_copy(update={"granted_at": None})
    assert outside_authorisation([act(Act.SIGN, None)], [unsigned]) == ()


# --- turnaround (D-133) -------------------------------------------------------------------------------

MON = date(2026, 6, 1)


def req(n, received=MON, due=None, complete=None, customer="A", analyses=("10_elements",), samples=2):
    return RequestDates(
        tr_number=f"TR{n:05d}", customer_id=customer, customer=f"Customer {customer}", analyses=analyses,
        samples=samples, received=received, due=due or received + timedelta(days=2), complete_report=complete,
        first_report=complete,
    )  # fmt: skip


def test_working_days_skip_weekends():
    assert working_days_between(MON, MON) == 0 and working_days_between(MON, MON - timedelta(days=1)) == 0
    assert working_days_between(MON, MON + timedelta(days=4)) == 4  # Friday
    assert working_days_between(MON, MON + timedelta(days=6)) == 4  # Sunday
    assert working_days_between(MON, MON + timedelta(days=7)) == 5
    assert working_days_between(MON + timedelta(days=4), MON + timedelta(days=7)) == 1  # Fri → Mon
    assert working_days_between(MON, MON + timedelta(days=15)) == 11


def test_a_request_is_on_time_late_or_open():
    today = MON + timedelta(days=10)
    assert req(1, complete=MON + timedelta(days=2)).state(today) == TurnaroundState.ON_TIME
    late = req(2, complete=MON + timedelta(days=7))
    assert late.state(today) == TurnaroundState.LATE and late.days_late(today) == 3 and late.days_taken == 5
    assert req(3, due=today).state(today) == TurnaroundState.OPEN and req(3).days_taken is None
    assert req(4).state(today) == TurnaroundState.OPEN_LATE and req(4).days_late(today) == 6
    assert TurnaroundState.OPEN_LATE.label == "Open, late" and late.month == "2026-06"
    with pytest.raises(ValidationError, match="before receipt"):
        req(5, complete=MON - timedelta(days=1))


def test_the_report_groups_by_month_customer_and_analysis():
    today = MON + timedelta(days=40)
    rs = [
        req(1, complete=MON + timedelta(days=2)),
        req(2, complete=MON + timedelta(days=9), customer="B", analyses=("10_elements", "anions")),
        req(3, received=MON + timedelta(days=31), analyses=()),
        req(4, received=MON + timedelta(days=35), due=today, customer="B"),
    ]
    found = turnaround(rs, today)
    assert found.overall.requests == 4 and found.overall.samples == 8
    assert (found.overall.on_time, found.overall.late, found.overall.open, found.overall.open_late) == (
        1,
        1,
        1,
        1,
    )
    assert found.overall.on_time_percent == 50 and found.overall.mean_days == D("4.5")
    assert found.overall.worst_late == 5 and found.overall.complete == 2
    assert [t.label for t in found.by_month] == ["2026-06", "2026-07"]
    assert [t.label for t in found.by_customer] == ["Customer A", "Customer B"]
    assert {t.label: t.requests for t in found.by_analysis} == {"(none)": 1, "10_elements": 3, "anions": 1}
    assert [r.tr_number for r in found.open_late] == ["TR00003"]
    later = turnaround(rs, today, since=MON + timedelta(days=30))
    assert (
        later.overall.requests == 2
        and later.overall.on_time_percent is None
        and later.overall.mean_days is None
    )


def test_customer_trends_count_months_and_complaints():
    today = MON + timedelta(days=40)
    rs = [req(1, complete=MON + timedelta(days=2)), req(2, received=MON + timedelta(days=31), samples=5),
          req(3, received=MON + timedelta(days=32)), req(4, customer="B")]  # fmt: skip
    found = customer_trends(rs, today, {"A": (2, 1), "Z": (1, 1), "Y": (0, 0)})
    assert [c.customer_id for c in found] == ["A", "B", "Z"]
    a, b, z = found
    assert a.months == (("2026-06", 1, 2), ("2026-07", 2, 7)) and a.change == 1
    assert a.complaints == 2 and a.open_complaints == 1 and a.on_time_percent == 100
    assert b.change is None and b.complaints == 0
    assert z.requests == 0 and z.complaints == 1
    assert [c.customer_id for c in customer_trends(rs, today, since=MON + timedelta(days=30))] == ["A"]
