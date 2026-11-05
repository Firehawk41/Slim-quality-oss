"""Batch 5c's pure rules: risks (D-134), internal audits (D-135), readiness and the management review
(D-136), the validation signature (D-137)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from slim_quality.domain.base import NotAllowed
from slim_quality.domain.clauses import CLAUSES, check_clause, clause_label
from slim_quality.domain.competence import Person
from slim_quality.domain.internal_audits import (
    AuditEntry,
    AuditEntryKind,
    AuditState,
    ChecklistLine,
    InternalAudit,
    LineOutcome,
    check_plan,
)
from slim_quality.domain.management_review import (
    FROM_RECORDS,
    INPUTS,
    WRITTEN,
    ManagementReview,
    OutputKind,
    PackSection,
    ReviewOutput,
    ReviewPack,
    check_review,
)
from slim_quality.domain.profile import Profile, differences
from slim_quality.domain.readiness import Facts, Rag, ReadinessPolicy, readiness
from slim_quality.domain.risks import (
    Risk,
    RiskBand,
    RiskEntry,
    RiskEntryKind,
    RiskKind,
    RiskPolicy,
    RiskSource,
)
from slim_quality.domain.validation import ValidationSignature, check_signature, status, versions_key
from tests.factories import MANAGER, USER

TODAY = date(2026, 11, 5)
AT = datetime(2026, 11, 5, 12, tzinfo=UTC)
POLICY = RiskPolicy()


def risk(**kw) -> Risk:
    base = dict(
        title="Only one ICP-MS", source=RiskSource.EQUIPMENT, owner="Jo", likelihood=2, severity=5,
        review_by=TODAY + timedelta(days=90), raised_by=USER, id=3,
    )  # fmt: skip
    return Risk(**base | kw)


def entry(kind, by=USER, **kw) -> RiskEntry:
    return RiskEntry(kind=kind, by=by, **kw)


# --- risks (D-134) ------------------------------------------------------------------------------------


def test_a_risk_is_scored_and_banded_by_the_profile():
    r = risk()
    assert r.number == "R-003" and str(r) == "R-003 Only one ICP-MS"
    assert r.score == 10 and r.scored == (2, 5) and r.band(POLICY) == RiskBand.HIGH
    assert RiskPolicy(medium_from=5, high_from=12).band(10) == RiskBand.MEDIUM
    assert POLICY.band(4) == RiskBand.LOW and RiskBand.LOW.label == "low"
    with pytest.raises(ValidationError, match="medium starts below high"):
        RiskPolicy(medium_from=10, high_from=10)
    with pytest.raises(ValidationError, match="a risk is scored"):
        risk(likelihood=None)
    with pytest.raises(ValidationError, match="not scored"):
        risk(kind=RiskKind.OPPORTUNITY)
    o = risk(kind=RiskKind.OPPORTUNITY, likelihood=None, severity=None, id=None)
    assert o.number == "O-new" and o.score is None and o.scored is None and o.band(POLICY) is None
    assert RiskKind.OPPORTUNITY.label == "Opportunity" and RiskSource.IT.label == "IT"
    assert RiskSource.IMPARTIALITY.label == "Impartiality"


def test_entries_are_checked_for_their_kind():
    for kind, kw, message in (
        (RiskEntryKind.ASSESSED, {}, "likelihood and the severity"),
        (RiskEntryKind.NOTE, {"likelihood": 1, "severity": 1, "note": "x"}, "likelihood and the severity"),
        (RiskEntryKind.NOTE, {"likelihood": 1, "note": "x"}, "only an assessment scores"),
        (RiskEntryKind.ACTION_PLANNED, {"action": "x"}, "who owns it"),
        (RiskEntryKind.NOTE, {"owner": "Jo", "note": "x"}, "belong to a planned action"),
        (RiskEntryKind.ACTION_DONE, {}, "names which action"),
        (RiskEntryKind.REVIEWED, {"note": "x"}, "next review date"),
        (RiskEntryKind.NOTE, {"note": "x", "review_by": TODAY}, "only a review or an assessment"),
        (RiskEntryKind.CLOSED, {"note": " "}, "write the note"),
    ):
        with pytest.raises(ValidationError, match=message):
            entry(kind, **kw)
    assert RiskEntryKind.ACTION_PLANNED.label == "Action planned"


def test_a_high_risk_needs_an_action_since_it_was_scored_and_a_review_in_date():
    r = risk()
    assert r.flags(TODAY, POLICY) == ("high, with no action planned since it was scored",)
    planned = entry(RiskEntryKind.ACTION_PLANNED, action="Service contract", owner="Jo", due=TODAY)
    r = r.model_copy(update={"entries": (planned,)})
    assert r.flags(TODAY, POLICY) == () and len(r.open_actions) == 1
    rescored = entry(RiskEntryKind.ASSESSED, likelihood=3, severity=5, review_by=TODAY - timedelta(days=1))
    r = r.model_copy(update={"entries": (planned, rescored)})
    assert r.score == 15 and r.review_due == TODAY - timedelta(days=1)
    assert r.flags(TODAY, POLICY) == (
        "high, with no action planned since it was scored",
        f"review overdue since {TODAY - timedelta(days=1)}",
    )
    low = risk(likelihood=1, severity=2, review_by=TODAY)
    assert low.flags(TODAY, POLICY) == () and low.review_due == TODAY


def test_actions_are_done_once_and_a_manager_closes_with_none_open():
    planned = entry(RiskEntryKind.ACTION_PLANNED, action="Contract", owner="Jo")
    r = risk(entries=(planned,))
    with pytest.raises(NotAllowed, match="no action 2"):
        r.check(entry(RiskEntryKind.ACTION_DONE, action_no=2))
    with pytest.raises(NotAllowed, match="1 action"):
        r.check(entry(RiskEntryKind.CLOSED, by=MANAGER, note="done"))
    done = entry(RiskEntryKind.ACTION_DONE, action_no=1, note="signed")
    r.check(done)
    r = r.model_copy(update={"entries": (planned, done)})
    assert r.actions[0].done == done and r.open_actions == ()
    with pytest.raises(NotAllowed, match="action 1 was done"):
        r.check(done)
    with pytest.raises(NotAllowed, match="quality manager closes"):
        r.check(entry(RiskEntryKind.CLOSED, note="done"))
    closed = entry(RiskEntryKind.CLOSED, by=MANAGER, note="done")
    r.check(closed)
    r = r.model_copy(update={"entries": (planned, done, closed)})
    assert r.closed and r.flags(TODAY + timedelta(days=999), POLICY) == ()
    with pytest.raises(NotAllowed, match="is closed"):
        r.check(entry(RiskEntryKind.NOTE, note="x"))
    o = risk(kind=RiskKind.OPPORTUNITY, likelihood=None, severity=None)
    with pytest.raises(NotAllowed, match="opportunity is not scored"):
        o.check(entry(RiskEntryKind.ASSESSED, likelihood=1, severity=1))


# --- internal audits (D-135) --------------------------------------------------------------------------

AUDITOR = Person(id="ana", display="Ana")


def audit(**kw) -> InternalAudit:
    return InternalAudit(
        **dict(id=1, clause="7.8", planned_for=TODAY, auditor=AUDITOR, planned_by=MANAGER) | kw
    )


def conducted(*outcomes, on=TODAY) -> AuditEntry:
    lines = tuple(
        ChecklistLine(clause="7.8.2", checked=f"report {n}", outcome=o, evidence="" if o == 1 else "seen")
        for n, o in enumerate(outcomes)
    )
    return AuditEntry(kind=AuditEntryKind.CONDUCTED, on=on, lines=lines, note="Summary", by=USER)


def test_a_planned_audit_names_its_clause_and_is_overdue_after_its_day():
    a = audit()
    assert a.number == "IA-2026-001" and a.year == 2026 and a.label == "7.8 Reporting of results"
    assert str(audit(area="metals")) == "IA-2026-001 7.8 Reporting of results (metals)"
    assert audit(id=None).number == "IA-new"
    assert a.state == AuditState.PLANNED and not a.overdue(TODAY) and a.overdue(TODAY + timedelta(days=1))
    with pytest.raises(ValidationError, match="no clause"):
        audit(clause="9.9")
    moved = AuditEntry(kind=AuditEntryKind.RESCHEDULED, on=TODAY + timedelta(days=7), by=USER)
    a = a.model_copy(update={"entries": (moved,)})
    assert a.due == TODAY + timedelta(days=7) and not a.overdue(TODAY + timedelta(days=1))
    assert a.lines_of(LineOutcome.FINDING) == () and a.done_on is None
    assert AuditState.CANCELLED.label == "Cancelled" and AuditEntryKind.NOTE.label == "Note"


def test_an_entry_is_checked_for_its_kind():
    with pytest.raises(ValidationError, match="has a day"):
        AuditEntry(kind=AuditEntryKind.RESCHEDULED, by=USER)
    with pytest.raises(ValidationError, match="at least one line"):
        AuditEntry(kind=AuditEntryKind.CONDUCTED, on=TODAY, note="x", by=USER)
    with pytest.raises(ValidationError, match="write the summary"):
        AuditEntry(kind=AuditEntryKind.CONDUCTED, on=TODAY, lines=conducted(1).lines, by=USER)
    with pytest.raises(ValidationError, match="write the note"):
        AuditEntry(kind=AuditEntryKind.CANCELLED, note=" ", by=USER)
    with pytest.raises(ValidationError, match="finding gives its evidence"):
        ChecklistLine(clause="7.8", checked="x", outcome=LineOutcome.FINDING)
    assert LineOutcome.OBSERVATION.label == "Observation"


def test_the_auditor_is_independent_and_lines_stay_in_the_clause():
    check_plan(audit())
    with pytest.raises(NotAllowed, match="quality manager plans"):
        check_plan(audit(planned_by=USER))
    with pytest.raises(NotAllowed, match="Ana had a hand in 7.8 Reporting"):
        check_plan(audit(), involved={"ana"})
    a = audit()
    with pytest.raises(NotAllowed, match="doesn't audit their own work"):
        a.check(conducted(1), involved={"ana"})
    outside = AuditEntry(
        kind=AuditEntryKind.CONDUCTED, on=TODAY, note="x", by=USER,
        lines=(ChecklistLine(clause="7.1", checked="x", outcome=LineOutcome.CONFORMING),),
    )  # fmt: skip
    with pytest.raises(NotAllowed, match="outside the audit's clause"):
        a.check(outside)
    with pytest.raises(NotAllowed, match="quality manager cancels"):
        a.check(AuditEntry(kind=AuditEntryKind.CANCELLED, note="no time", by=USER))
    done = conducted(1, 2, 3)
    a.check(done)
    a = a.model_copy(update={"entries": (done,)})
    assert a.state == AuditState.CONDUCTED and a.done_on == TODAY and not a.overdue(TODAY + timedelta(days=9))
    assert [n for n, _ in a.lines_of(LineOutcome.FINDING)] == [3] and a.finding_ref(3) == "internal-audit:1:3"
    with pytest.raises(NotAllowed, match="was conducted"):
        a.check(AuditEntry(kind=AuditEntryKind.RESCHEDULED, on=TODAY, by=USER))
    a.check(AuditEntry(kind=AuditEntryKind.NOTE, note="follow-up", by=USER))
    cancelled = audit(entries=(AuditEntry(kind=AuditEntryKind.CANCELLED, note="x", by=MANAGER),))
    assert cancelled.state == AuditState.CANCELLED and not cancelled.overdue(TODAY + timedelta(days=9))


def test_clauses():
    assert check_clause("8.9") == "8.9" and clause_label("6.4") == "6.4 Equipment" and len(CLAUSES) == 27


# --- readiness (D-136) --------------------------------------------------------------------------------


def rows(**facts) -> dict:
    return {r.clause: r for r in readiness(Facts(today=TODAY, **facts), ReadinessPolicy())}


def texts(row) -> list[tuple[str, str]]:
    return [(e.rag.label, e.text) for e in row.evidence]


def test_an_empty_lab_is_red_where_records_are_missing():
    found = rows()
    assert list(found) == sorted(CLAUSES, key=lambda c: tuple(map(int, c.split("."))))
    assert all(r.evidence[0].text == "never audited" for r in found.values())
    assert all(r.rag == Rag.RED for r in found.values())
    assert texts(found["6.2"])[1] == ("amber", "acts not checked against the authorisation register")
    assert texts(found["7.6"])[1] == ("red", "no signed uncertainty estimate")
    assert texts(found["7.7"])[1] == ("red", "no PT round scored")
    assert len(found["7.8"].evidence) == 1  # no turnaround handed over: no line
    assert texts(found["7.11"])[1] == ("red", "the software validation pack was never signed")
    assert texts(found["8.5"])[1] == ("red", "no risks or opportunities registered")
    assert texts(found["8.8"])[1] == ("red", "no audit planned for 2026")
    assert texts(found["8.9"])[1] == ("red", "no management review signed")
    assert texts(found["6.4"])[1] == ("green", "every registered item in date")


def test_a_lab_in_good_order_is_green_and_old_evidence_amber():
    old, recent = TODAY - timedelta(days=400), TODAY - timedelta(days=10)
    good = rows(
        last_audited=dict.fromkeys(CLAUSES, recent), competence_findings=0, equipment_out=1,
        uncertainty_estimates=2, uncertainty_oldest=recent, pt_last_scored=recent, on_time_percent=D(95),
        on_time_requests=20, validation=Rag.GREEN, validation_signed=recent, risks_open=3,
        audits_planned_this_year=4, last_review=recent,
    )  # fmt: skip
    assert all(r.rag == Rag.GREEN for r in good.values()), [r for r in good.values() if r.rag != Rag.GREEN]
    assert ("green", "1 item out of service, taken out") in texts(good["6.4"])
    assert texts(good["7.8"])[1] == ("green", "95 % of 20 reported on time (target 90 %)")
    stale = rows(
        last_audited={"7.8": old}, findings_without_nc={"7.8": 2}, competence_findings=1, equipment_overdue=1,
        equipment_due=2, lots_expired_in_use=1, lots_expiring=1, uncertainty_estimates=1,
        uncertainty_oldest=old, pt_last_scored=old, pt_unsatisfactory=1, pt_overdue=1, on_time_percent=D(80),
        on_time_requests=5, complaints_open=2, complaints_open_long=1, ncs_open=3, ncs_open_long=1,
        nc_actions_overdue=1, validation=Rag.AMBER, validation_signed=old, risks_open=2, risks_flagged=1,
        audits_planned_this_year=1, audits_overdue=1, last_review=old,
    )  # fmt: skip
    assert texts(stale["7.8"])[:2] == [
        ("amber", f"last audited {old}, more than 365 days ago"),
        ("amber", "2 audit findings with no NC raised"),
    ]
    assert stale["7.8"].rag == Rag.AMBER and stale["6.4"].rag == Rag.RED and stale["8.7"].rag == Rag.RED
    for clause, line in (
        ("6.2", "1 act outside an authorisation"),
        ("6.4", "2 items due soon"),
        ("6.5", "1 lot past expiry and not withdrawn"),
        ("6.5", "1 lot expiring within 30 days"),
        ("7.6", f"the oldest estimate was signed {old}"),
        ("7.7", f"last PT round scored {old}"),
        ("7.7", "1 unsatisfactory score in the last year"),
        ("7.7", "1 PT round past due, not submitted"),
        ("7.8", "80 % of 5 reported on time (target 90 %)"),
        ("7.9", "1 complaint open over 30 days"),
        ("7.10", "1 nonconformity open over 60 days"),
        ("7.11", f"last signed {old}, for other module versions"),
        ("8.5", "1 risk flagged (high with no action, or review overdue)"),
        ("8.8", "1 planned audit overdue"),
        ("8.9", f"last management review {old}"),
    ):
        assert ("amber", line) in texts(stale[clause]), (clause, texts(stale[clause]))
    assert ("red", "1 corrective action past due") in texts(stale["7.10"])
    assert ("green", "3 nonconformities open") in texts(stale["7.10"])


# --- the management review (D-136) ----------------------------------------------------------------------


def review(**kw) -> ManagementReview:
    pack = ReviewPack(
        period_from=TODAY - timedelta(days=365), period_to=TODAY,
        readiness=readiness(Facts(today=TODAY), ReadinessPolicy()),
        sections=(PackSection(letter="e", title=INPUTS["e"], lines=("IA-2026-001 …",)),),
    )  # fmt: skip
    base = dict(
        pack=pack, written={"a": "New customer in Q3\nA move planned"},
        outputs=(ReviewOutput(kind=OutputKind.RESOURCES, text="A second ICP-MS", owner="Jo", due=TODAY),),
        signed_by=MANAGER,
    )  # fmt: skip
    return ManagementReview(**base | kw)


def test_the_review_lists_every_input_and_a_manager_signs_it_with_outputs():
    assert set(FROM_RECORDS) | set(WRITTEN) == set(INPUTS) and not set(FROM_RECORDS) & set(WRITTEN)
    r = review()
    sections = r.sections()
    assert [s[0] for s in sections] == list(INPUTS)
    assert sections[0][2] == ("New customer in Q3", "A move planned")
    assert sections[1][2] == () and sections[4][2] == ("IA-2026-001 …",)
    assert r.number == "MR-new" and review(id=2).number == "MR-002"
    check_review(r)
    with pytest.raises(NotAllowed, match="quality manager signs"):
        check_review(review(signed_by=USER))
    with pytest.raises(NotAllowed, match="outputs"):
        check_review(review(outputs=()))
    with pytest.raises(ValidationError, match="not an input the manager writes: e"):
        review(written={"e": "x"})
    with pytest.raises(ValidationError, match="ends before it starts"):
        ReviewPack(period_from=TODAY, period_to=TODAY - timedelta(days=1), readiness=(), sections=())
    assert OutputKind.CHANGE.label == "Need for change"


# --- the validation signature (D-137) -------------------------------------------------------------------


def test_a_signature_covers_exactly_its_versions():
    versions = {"slim-data": "abc", "slim-quality": "def"}
    assert versions_key(versions) == versions_key(dict(reversed(versions.items())))
    sig = ValidationSignature(
        versions=versions, note="CI green; verify clean", signed_by=MANAGER, signed_at=AT
    )
    check_signature(sig)
    with pytest.raises(NotAllowed, match="quality manager signs"):
        check_signature(sig.model_copy(update={"signed_by": USER}))
    with pytest.raises(ValidationError, match="say what was checked"):
        ValidationSignature(versions=versions, note=" ", signed_by=MANAGER)
    assert status([], versions) == (None, None)
    assert status([sig], versions) == (Rag.GREEN, sig)
    assert status([sig], versions | {"slim-data": "new"}) == (Rag.AMBER, sig)


def test_the_profile_carries_the_new_policies_and_lists_their_changes():
    before = Profile()
    after = Profile(risk=RiskPolicy(high_from=12), readiness=ReadinessPolicy(audit_every_days=180))
    lines = differences(before, after)
    assert "Risk: high from score: 10 → 12" in lines
    assert "Readiness: audit each clause within (days): 365 → 180" in lines
