"""Nonconformities (D-114): entries are facts, the state follows them; audit records (D-113)."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from slim_quality.domain.audits import AuditOutcome, AuditRecord
from slim_quality.domain.changes import NotAllowed
from slim_quality.domain.nonconformity import (
    EntryKind,
    ImpactDecision,
    ImpactLine,
    NcEntry,
    NcSource,
    NcState,
    Nonconformity,
)
from tests.factories import MANAGER, T0, USER

LINE = ImpactLine(
    ref="TR00008-01|1|Cu|2", label="TR00008-01 Cu v2: 12.3 ppb", decision=ImpactDecision.NOTIFY_CUSTOMER
)


def nc(*entries, **kw) -> Nonconformity:
    fields = dict(id=7, title="Cone wear", description="ISTD sagging on ICP-MS 1", raised_by=USER)
    return Nonconformity(**(fields | kw), entries=entries)


def assessed(by=MANAGER):
    return NcEntry(kind=EntryKind.ASSESSED, impact=(LINE,), by=by)


def action(by=USER):
    return NcEntry(
        kind=EntryKind.ACTION,
        root_cause="worn cones",
        action="replace cones",
        owner="QU",
        due=date(2026, 11, 1),
        by=by,
    )


def check(effective, by=USER):
    return NcEntry(kind=EntryKind.EFFECTIVENESS, effective=effective, note="ISTD back near 97 %", by=by)


def closed(by=MANAGER):
    return NcEntry(kind=EntryKind.CLOSED, note="effective over 2 weeks", by=by)


def test_the_state_follows_the_entries():
    assert nc().state == NcState.OPEN and nc().number == "NC-0007"
    assert nc(assessed()).state == NcState.ASSESSED
    assert nc(assessed(), action()).state == NcState.ACTION_TAKEN
    assert nc(assessed(), action(), check(False)).state == NcState.ACTION_TAKEN  # not effective: act again
    assert nc(assessed(), action(), check(True)).state == NcState.EFFECTIVE
    assert nc(assessed(), action(), check(True), assessed()).state == NcState.EFFECTIVE  # re-assessed
    done = nc(assessed(), action(), check(True), closed())
    assert (
        done.state == NcState.CLOSED
        and done.assessment.impact == (LINE,)
        and done.latest_action.owner == "QU"
    )
    assert nc().assessment is None and nc().latest_action is None
    assert NcState.ACTION_TAKEN.label == "Action taken" and NcSource.FLAG.label == "From a flag"
    assert ImpactDecision.AMEND_REPORT.label == "Amend report"


@pytest.mark.parametrize(
    "before, entry, message",
    [
        ((), assessed(USER), "a quality manager assesses"),
        ((), action(), "assess the impact before the action"),
        ((assessed(),), check(True), "follows an action"),
        ((assessed(), action()), closed(), "once the action is shown effective"),
        ((assessed(), action(), check(True)), closed(USER), "a quality manager assesses and closes"),
        (
            (assessed(), action(), check(True), closed()),
            NcEntry(kind=EntryKind.NOTE, note="x", by=USER),
            "is closed",
        ),
    ],
)
def test_entries_the_facts_dont_allow_are_refused(before, entry, message):
    with pytest.raises(NotAllowed, match=message):
        nc(*before).check(entry)


def test_allows_answers_before_any_form_is_read():
    with pytest.raises(NotAllowed, match="a quality manager"):
        nc().allows(EntryKind.ASSESSED, USER)
    nc().allows(EntryKind.ASSESSED, MANAGER)


def test_what_is_allowed_passes():
    nc().check(assessed())
    nc(assessed()).check(action())
    nc(assessed(), action()).check(check(True))
    nc(assessed(), action(), check(True)).check(closed())
    nc(assessed(), action(), check(True)).check(action())  # a further action is always allowed
    nc().check(NcEntry(kind=EntryKind.NOTE, note="called the vendor", by=USER))


@pytest.mark.parametrize(
    "fields, message",
    [
        (dict(kind=EntryKind.ACTION, root_cause="x", owner="QU"), "root cause, the action and an owner"),
        (dict(kind=EntryKind.NOTE, note="x", owner="QU"), "belong to an action"),
        (dict(kind=EntryKind.EFFECTIVENESS), "effective or not"),
        (dict(kind=EntryKind.NOTE, note="x", effective=True), "effective or not"),
        (dict(kind=EntryKind.NOTE, note="x", impact=(LINE,)), "belong to an assessment"),
        (dict(kind=EntryKind.NOTE), "write the note"),
        (dict(kind=EntryKind.ASSESSED), "says why in a note"),
        (dict(kind=EntryKind.ASSESSED, impact=(LINE, LINE)), "decided twice"),
    ],
)
def test_an_entry_carries_exactly_what_its_kind_needs(fields, message):
    with pytest.raises(ValidationError, match=message):
        NcEntry(by=MANAGER, **fields)
    assert NcEntry(kind=EntryKind.ASSESSED, note="nothing released in the window", by=MANAGER).impact == ()


@pytest.mark.parametrize(
    "fields, message",
    [
        (dict(instrument="ICP-MS 1", window_from=T0), "both ends"),
        (dict(instrument="ICP-MS 1", window_from=T0, window_to=T0 - timedelta(days=1)), "ends before"),
        (dict(window_from=T0, window_to=T0), "a window is on an instrument"),
        (dict(analyte="Cu"), "an analyte is on an instrument"),
    ],
)
def test_a_window_is_whole_and_on_an_instrument(fields, message):
    with pytest.raises(ValidationError, match=message):
        nc(**fields)


def test_an_audit_with_findings_says_what_they_are():
    with pytest.raises(ValidationError, match="write the findings"):
        AuditRecord(subject="Report 1", subject_ref="issue:1", outcome=AuditOutcome.FINDINGS, by=USER)
    AuditRecord(subject="Report 1", subject_ref="issue:1", outcome=AuditOutcome.CONFORMING, by=USER)
