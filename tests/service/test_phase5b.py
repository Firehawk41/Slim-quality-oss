"""The service on a migrated database: uncertainty estimates, the authorisation register, trends."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from slim_quality.demo.history import DemoSource
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.competence import (
    Act,
    ActRecord,
    Authorisation,
    AuthorisationEntry,
    AuthorisationEntryKind,
    Person,
)
from slim_quality.domain.complaints import ComplaintEntry, ComplaintEntryKind, Party
from slim_quality.domain.enums import Measure
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.pt import Assigned, LabValue, PtEntry, PtEntryKind
from slim_quality.domain.series import Point, Series, SeriesKey
from slim_quality.domain.turnaround import RequestDates
from slim_quality.persistence import store
from slim_quality.service import competence, complaints, pt, trends
from slim_quality.service import uncertainty as unc
from tests.factories import MANAGER, USER

END = datetime(2026, 10, 28, 12, tzinfo=UTC)
SOURCE = DemoSource(END)
START = END - timedelta(days=90)


def zn_check() -> str:
    found = unc.precision_series(SOURCE.series())
    return next(s.key.slug for s in found if s.key.instrument == "ICP-MS 2" and s.key.analyte == "Zn")


def scored_round(session, code, lab, assigned, on):
    r = pt.register_round(session, "Demo PT", code, "HISTORY", f"S-{code}", ("Zn",), on, USER)
    sent = (LabValue(analyte="Zn", value=D(lab), unit="ppb", result_ref=f"h:{code}"),)
    pt.add_entry(session, r.id, PtEntry(kind=PtEntryKind.SUBMITTED, values=sent, by=USER))
    scored = (Assigned(analyte="Zn", assigned=D(assigned), sigma=D(1)),)
    entry = PtEntry(kind=PtEntryKind.SCORED, assigned=scored, report_date=on, by=USER)
    return pt.add_entry(session, r.id, entry)


def test_an_estimate_is_worked_from_the_records_and_signed_by_a_manager(session):
    slug = zn_check()
    with pytest.raises(NotAllowed, match="0 CRM or PT"):
        unc.estimate(session, SOURCE, slug, START, END, D("0.01"), "")
    for i, (lab, assigned) in enumerate((("20.4", "20"), ("19.8", "20"), ("20.2", "20"))):
        scored_round(session, f"R{i}", lab, assigned, (END - timedelta(days=60 - 20 * i)).date())
    pt.register_round(session, "Demo PT", "R9", "TR1", "TR1-01", ("Zn",), END.date(), USER)  # unscored
    draft = unc.estimate(session, SOURCE, slug, START, END, D("0.01"), "")
    assert draft.signed_by is None and draft.n_precision > 20 and len(draft.bias_points) == 3
    assert [b.bias for b in draft.bias_points] == [D("0.02"), D("-0.01"), D("0.01")]
    assert draft.basis == "—" and draft.key.label == "ICP-MS 2 · Zn (KED)"
    with pytest.raises(NotAllowed, match="quality manager signs"):
        unc.sign(session, SOURCE, slug, START, END, D("0.01"), "cert", USER)
    with pytest.raises(NotAllowed, match="u\\(Cref\\)"):
        unc.sign(session, SOURCE, slug, START, END, D("0.01"), " ", MANAGER)
    with pytest.raises(NotAllowed, match="check standard series"):
        unc.estimate(session, SOURCE, "nope", START, END, D("0.01"), "")
    signed = unc.sign(session, SOURCE, slug, START, END, D("0.01"), "certificate, 1 %", MANAGER)
    assert (
        signed.id and signed.signed_by == MANAGER and signed.signed_at and signed.expanded == draft.expanded
    )
    newer = unc.sign(session, SOURCE, slug, START + timedelta(days=30), END, D("0.01"), "again", MANAGER)
    assert unc.current(session) == {signed.key.slug: newer}
    assert len(store.uncertainty_estimates(session, key=signed.key.slug)) == 2
    with pytest.raises(ValueError, match="recorded signed"):
        store.add_uncertainty(session, draft)


def test_crm_recoveries_count_as_bias_for_their_own_key():
    def crm(instrument):
        k = SeriesKey(
            measure=Measure.CRM_RECOVERY, instrument=instrument, analyte="Zn", conditions="KED",
            material="CRM-1", level=D(5),
        )  # fmt: skip
        point = Point(at=END, value=D("1.03"), run_code="R1", ref=f"{instrument}:1")
        return Series(key=k, points=(point,))

    key = unc.key_of(crm("ICP-MS 1").key)
    found = unc.bias_points([crm("ICP-MS 1"), crm("ICP-MS 2")], [], key)
    assert [(b.source, b.label, b.bias) for b in found] == [("crm", "CRM-1 @ 5", D("0.03"))]


ANA = Person(id="u-ana", display="AN")


def test_the_register_grants_withdraws_and_finds_acts_outside_it(session):
    a = competence.grant(
        session,
        Authorisation(
            person=ANA, act=Act.REVIEW, method="10_elements", valid_from=date(2026, 1, 1),
            evidence="witnessed run", granted_by=MANAGER,
        ),
    )  # fmt: skip
    with pytest.raises(NotAllowed):
        competence.grant(session, a.model_copy(update={"granted_by": USER}))
    today = a.granted_at.date()
    acts = [
        ActRecord(person=ANA, act=Act.REVIEW, method="10_elements", on=today, ref="ok", label="TR1-01 Cu"),
        ActRecord(person=ANA, act=Act.RELEASE, method="10_elements", on=today, ref="no", label="TR1-01 Cu"),
    ]
    assert [f.act.ref for f in competence.findings(session, acts)] == ["no"]
    gone = AuthorisationEntry(kind=AuthorisationEntryKind.WITHDRAWN, on=today, note="moved on", by=MANAGER)
    withdrawn = competence.add_entry(session, a.id, gone)
    assert withdrawn.withdrawn_on == today and len(competence.register(session)) == 1
    with pytest.raises(NotAllowed, match="was withdrawn"):
        competence.add_entry(session, a.id, gone)
    assert [f.act.ref for f in competence.findings(session, acts)] == ["no", "ok"]
    assert store.list_authorisations(session, person_id="nobody") == []
    with pytest.raises(store.NotFound):
        store.get_authorisation(session, 999)
    nc = competence.finding_nc(session, competence.findings(session, acts)[0], USER)
    assert nc.source == NcSource.COMPETENCE and nc.source_ref == "no" and "release outside" in nc.title


class Requests:
    def __init__(self, found):
        self.found = found

    def requests(self, since=None):
        return [r for r in self.found if since is None or r.received >= since]


def test_trends_bring_in_the_complaints(session):
    day = date(2026, 6, 1)
    source = Requests(
        [
            RequestDates(
                tr_number="TR00001", customer_id="7", customer="Customer A", samples=2, received=day,
                due=day + timedelta(days=2), complete_report=day + timedelta(days=1),
            ),
        ]
    )  # fmt: skip
    party = Party(id="c1", display="Casey")
    first = complaints.raise_complaint(session, "TR00001", "7", "Late.", party)
    complaints.raise_complaint(session, "TR00001", "7", "Still late.", party)
    for kind, note in ((ComplaintEntryKind.ACKNOWLEDGED, "Thanks"), (ComplaintEntryKind.INVESTIGATED, "x"),
                       (ComplaintEntryKind.OUTCOME, "y"), (ComplaintEntryKind.CLOSED, "z")):  # fmt: skip
        entry = ComplaintEntry(
            kind=kind, note=note, to_customer=kind != ComplaintEntryKind.INVESTIGATED,
            valid=True if kind == ComplaintEntryKind.INVESTIGATED else None, by=MANAGER,
        )  # fmt: skip
        complaints.add_entry(session, first.id, entry)
    assert trends.complaint_counts(session) == {"7": (2, 1)}
    assert trends.complaint_counts(session, since=date(2999, 1, 1)) == {}
    report = trends.turnaround_report(source, day + timedelta(days=10))
    assert report.overall.on_time == 1
    (a,) = trends.customers(session, source, day + timedelta(days=10))
    assert a.customer == "Customer A" and a.complaints == 2 and a.open_complaints == 1
