"""The service on a migrated database: risks, internal audits, readiness, the management review and the
validation signature (D-134…D-137)."""

from datetime import UTC, datetime, timedelta

import pytest

from slim_quality.domain.base import NotAllowed
from slim_quality.domain.competence import Act, ActRecord, Person
from slim_quality.domain.internal_audits import (
    AuditEntry,
    AuditEntryKind,
    ChecklistLine,
    InternalAudit,
)
from slim_quality.domain.management_review import OutputKind, ReviewOutput
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.readiness import Rag
from slim_quality.domain.risks import Risk, RiskEntry, RiskEntryKind, RiskSource
from slim_quality.domain.turnaround import RequestDates
from slim_quality.persistence import store
from slim_quality.service import internal_audits as ia
from slim_quality.service import quality as q
from slim_quality.service import readiness as rd
from slim_quality.service import risks
from slim_quality.service.verify import verify
from tests.factories import MANAGER, USER

TODAY = datetime.now(UTC).date()  # rows are stamped by the database clock
ANA = Person(id="ana", display="Ana")
VERSIONS = {"slim-data": "abc", "slim-quality": "def"}


class Dates:
    def __init__(self, *requests):
        self._requests = requests

    def requests(self, since=None):
        return [r for r in self._requests if since is None or r.received >= since]


def request(n, received, complete):
    return RequestDates(
        tr_number=f"TR{n:05d}", customer_id="1", customer="A", samples=2, received=received,
        due=received + timedelta(days=7), complete_report=complete,
    )  # fmt: skip


def a_risk(session, **kw):
    base = dict(
        title="Only one ICP-MS", source=RiskSource.EQUIPMENT, owner="Jo", likelihood=2, severity=5,
        review_by=TODAY + timedelta(days=90), raised_by=USER,
    )  # fmt: skip
    return risks.raise_risk(session, Risk(**base | kw))


def test_the_risk_register_flags_first_and_a_risk_can_raise_an_nc(session):
    high = a_risk(session)
    low = a_risk(session, title="Shared balance", likelihood=1, severity=2)
    assert [(r.id, f) for r, f in risks.register(session, TODAY)] == [
        (high.id, ("high, with no action planned since it was scored",)),
        (low.id, ()),
    ]
    planned = RiskEntry(kind=RiskEntryKind.ACTION_PLANNED, action="Service contract", owner="Jo", by=USER)
    high = risks.add_entry(session, high.id, planned)
    assert high.entries[0].at is not None and risks.register(session, TODAY)[0][1] == ()
    with pytest.raises(NotAllowed, match="1 action"):
        risks.add_entry(session, high.id, RiskEntry(kind=RiskEntryKind.CLOSED, note="x", by=MANAGER))
    with pytest.raises(store.NotFound):
        risks.add_entry(session, 999, planned)
    closed = RiskEntry(kind=RiskEntryKind.CLOSED, note="gone", by=MANAGER)
    risks.add_entry(session, low.id, closed)
    assert [r.id for r, _ in risks.register(session, TODAY)] == [high.id]
    assert len(risks.register(session, TODAY, include_closed=True)) == 2
    nc = risks.risk_nc(session, high.id, USER)
    assert nc.source == NcSource.RISK and nc.source_ref == f"risk:{high.id}" and "R-001 happened" in nc.title


def plan(session, clause="7.8", auditor=ANA, planned_for=TODAY, involved=()):
    found = InternalAudit(clause=clause, planned_for=planned_for, auditor=auditor, planned_by=MANAGER)
    return ia.plan(session, found, involved)


def conduct(*outcomes, on=TODAY, clause="7.8"):
    lines = tuple(
        ChecklistLine(clause=clause, checked=f"item {n}", outcome=o, evidence="" if o == 1 else "seen")
        for n, o in enumerate(outcomes, start=1)
    )
    return AuditEntry(kind=AuditEntryKind.CONDUCTED, on=on, lines=lines, note="Summary", by=USER)


def test_an_internal_audit_is_planned_conducted_and_its_finding_raises_one_nc(session):
    with pytest.raises(NotAllowed, match="Ana had a hand"):
        plan(session, involved={"ana"})
    audit = plan(session)
    assert audit.id and audit.planned_at and ia.programme(session, TODAY.year) == [audit]
    assert ia.programme(session, TODAY.year + 1) == []
    with pytest.raises(NotAllowed, match="own work"):
        ia.add_entry(session, audit.id, conduct(1), involved={"ana"})
    audit = ia.add_entry(session, audit.id, conduct(1, 3))
    assert ia.last_audited(session)["7.8"] == TODAY
    assert {k: [(a.id, n) for a, n in v] for k, v in ia.findings_without_nc(session).items()} == {
        "7.8": [(audit.id, 2)]
    }
    with pytest.raises(store.NotFound, match="no finding on line 1"):
        ia.finding_nc(session, audit.id, 1, USER)
    nc = ia.finding_nc(session, audit.id, 2, USER)
    assert nc.source == NcSource.AUDIT and nc.source_ref == f"internal-audit:{audit.id}:2"
    assert ia.findings_without_nc(session) == {}
    with pytest.raises(NotAllowed, match="already raised"):
        ia.finding_nc(session, audit.id, 2, USER)
    q.record_audit(session, "Report 1", "issue:1", 1, "", USER)
    assert ia.last_audited(session)["7.1"] == TODAY  # a vertical audit counts for its clauses


def test_whoever_kept_a_clauses_records_here_does_not_audit_it(session):
    me = Person(id=USER.id, display=USER.display)
    a_risk(session)  # raised by the quality user
    with pytest.raises(NotAllowed, match="8.5 Actions"):
        plan(session, clause="8.5", auditor=me)
    q.raise_nc(session, "x", "y", USER)
    for clause in ("7.10", "8.7"):
        with pytest.raises(NotAllowed):
            plan(session, clause=clause, auditor=me)
    plan(session, clause="8.8", auditor=me)  # planned by the manager
    with pytest.raises(NotAllowed):
        plan(session, clause="8.8", auditor=Person(id=MANAGER.id, display="Quinn"))
    for clause in ("6.2", "6.4", "6.5", "7.6", "7.7", "7.9", "7.11", "8.9", "4.1"):
        assert ia.record_keepers(session, clause, TODAY) == frozenset(), clause
    assert ia.record_keepers(session, "8.5", TODAY + timedelta(days=400)) == frozenset()


def test_readiness_gathers_the_records_and_what_the_host_hands_over(session):
    facts = rd.gather(session, TODAY)
    assert facts.competence_findings is None and facts.on_time_percent is None and facts.validation is None
    rows = {r.clause: r for r in rd.view(session, facts)}
    assert rows["8.5"].rag == Rag.RED and rows["7.11"].rag == Rag.RED
    a_risk(session)
    audit = plan(session, planned_for=TODAY - timedelta(days=3))
    acts = [
        ActRecord(person=ANA, act=Act.SIGN, on=TODAY, ref="issue:1", label="report 1"),
    ]
    dates = Dates(
        request(1, TODAY - timedelta(days=20), TODAY - timedelta(days=15)),
        request(2, TODAY - timedelta(days=20), None),
    )
    rd.sign_validation(session, VERSIONS, {"modules": []}, "CI green", MANAGER)
    facts = rd.gather(session, TODAY, dates, acts, VERSIONS)
    assert (facts.risks_open, facts.risks_flagged, facts.audits_overdue, facts.audits_planned_this_year) == (
        1,
        1,
        1,
        1,
    )
    assert facts.competence_findings == 1 and facts.on_time_requests == 1 and facts.validation == Rag.GREEN
    assert rd.gather(session, TODAY, versions=VERSIONS | {"slim-data": "new"}).validation == Rag.AMBER
    assert rd.gather(session, TODAY).validation == Rag.AMBER  # not told what is installed
    assert rd.validation(session, VERSIONS)[0] == Rag.GREEN
    with pytest.raises(NotAllowed, match="quality manager signs the validation"):
        rd.sign_validation(session, VERSIONS, {}, "x", USER)
    ia.add_entry(session, audit.id, conduct(3))
    facts = rd.gather(session, TODAY, dates, acts, VERSIONS)
    assert facts.findings_without_nc == {"7.8": 1} and facts.last_audited == {"7.8": TODAY}


def test_the_management_review_pack_is_frozen_when_signed(session):
    a_risk(session)
    audit = plan(session)
    ia.add_entry(session, audit.id, conduct(1, 2, 3))
    q.raise_nc(session, "Cones", "worn", USER)
    q.record_audit(session, "Report 1", "issue:1", 2, "missing signature", USER)
    dates = Dates(request(1, TODAY - timedelta(days=20), TODAY - timedelta(days=15)))
    acts = [ActRecord(person=ANA, act=Act.SIGN, on=TODAY, ref="issue:1", label="report 1")]
    facts = rd.gather(session, TODAY, dates, acts, VERSIONS)
    start = TODAY - timedelta(days=365)
    pack = rd.pack(session, start, TODAY, facts, dates, acts)
    lines = {s.letter: s.lines for s in pack.sections}
    assert lines["d"] == ("No earlier management review.",)
    assert lines["e"][0].endswith("1 conforming, 1 observations, 1 findings")
    assert lines["e"][1] == "1 vertical audits of reports, 1 with findings"
    assert (
        lines["f"][0] == "1 nonconformities raised, 0 closed in the period."
        and "NC-0001 Cones" in lines["f"][1]
    )
    assert lines["h"][-1] == "100 % of 1 complete requests on time."
    assert lines["j"] == ("No complaint in the period.",)
    assert lines["m"][0].startswith("R-001 Only one ICP-MS: score 10 (high)")
    assert lines["o"][0] == "1 acts outside an authorisation."
    outputs = [ReviewOutput(kind=OutputKind.RESOURCES, text="A second ICP-MS", owner="Jo", due=TODAY)]
    with pytest.raises(NotAllowed, match="quality manager signs"):
        rd.sign_review(session, pack, {}, outputs, "", USER)
    with pytest.raises(NotAllowed, match="outputs"):
        rd.sign_review(session, pack, {}, [], "", MANAGER)
    signed = rd.sign_review(session, pack, {"a": "New customer", "b": " "}, outputs, "QM, Jo", MANAGER)
    assert signed.id and signed.signed_at and signed.written == {"a": "New customer"}
    assert store.management_reviews(session) == [signed] and signed.pack == pack
    again = rd.pack(session, start, TODAY, rd.gather(session, TODAY), None)
    after = {s.letter: s.lines for s in again.sections}
    assert (
        after["d"][0].startswith("MR-001, signed")
        and after["d"][1] == "Resources needed: A second ICP-MS (Jo, due " + str(TODAY) + ")"
    )
    assert after["h"] == ("No request dates handed over.",)
    assert rd.gather(session, TODAY).last_review == TODAY
    empty = rd.pack(session, TODAY + timedelta(days=1), TODAY + timedelta(days=2), facts, Dates())
    assert {s.letter: s.lines for s in empty.sections}["e"] == ("No audit recorded in the period.",)
    assert {s.letter: s.lines for s in empty.sections}["h"] == ("No requests received.",)


def test_verify_replays_risks_and_audits_and_checks_the_signers(session):
    risk = a_risk(session)
    risks.add_entry(session, risk.id, RiskEntry(kind=RiskEntryKind.NOTE, note="x", by=USER))
    audit = plan(session)
    ia.add_entry(session, audit.id, conduct(1))
    found = verify(session)
    assert found.ok and found.risks == 1 and found.internal_audits == 1
    store.add_risk_entry(session, risk.id, RiskEntry(kind=RiskEntryKind.CLOSED, note="x", by=USER))
    store.add_internal_audit_entry(session, audit.id, conduct(1))
    problems = verify(session).problems
    assert any("R-001 entry 2 (closed)" in p for p in problems)
    assert any(f"{audit.number} entry 2 (conducted)" in p for p in problems)
