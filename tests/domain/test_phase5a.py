"""Equipment, reference lots, PT and complaints (D-125…D-130): the rules each record keeps."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from slim_quality.domain.base import NotAllowed
from slim_quality.domain.complaints import (
    Complaint,
    ComplaintEntry,
    ComplaintEntryKind,
    ComplaintState,
    Party,
)
from slim_quality.domain.enums import Effect, Measure
from slim_quality.domain.equipment import (
    Equipment,
    EquipmentEntry,
    EquipmentEntryKind,
    EquipmentKind,
    EquipmentPolicy,
    EquipmentState,
    check_use,
)
from slim_quality.domain.materials import LotEntry, LotEntryKind, ReferenceLot
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.profile import Profile, differences
from slim_quality.domain.pt import (
    Assigned,
    LabValue,
    PtClass,
    PtEntry,
    PtEntryKind,
    PtPolicy,
    PtRound,
    PtState,
    classify,
    z_series,
)
from tests.factories import MANAGER, OTHER_MANAGER, USER

DAY = date(2026, 6, 1)
AT = datetime(2026, 6, 1, 9, tzinfo=UTC)


def cal(on, passed=True, due_days=365, note=""):
    return EquipmentEntry(
        kind=EquipmentEntryKind.CALIBRATION,
        on=on,
        passed=passed,
        provider="Demo Calibration Co.",
        certificate="C-1",
        next_due=on + timedelta(days=due_days) if passed else None,
        note=note or ("" if passed else "reads 0.3 mg high"),
        by=USER,
    )


def item(*entries, kind=EquipmentKind.BALANCE, required=True):
    return Equipment(
        name="BAL-1", kind=kind, calibration_required=required, registered_by=USER, entries=entries
    )


# --- equipment (D-125, D-126) -------------------------------------------------------------------------


def test_equipment_entries_carry_the_fields_of_their_kind():
    with pytest.raises(ValidationError, match="passes or fails"):
        EquipmentEntry(kind=EquipmentEntryKind.CALIBRATION, on=DAY, provider="x", by=USER)
    with pytest.raises(ValidationError, match="passes or fails"):
        EquipmentEntry(kind=EquipmentEntryKind.NOTE, on=DAY, passed=True, note="x", by=USER)
    with pytest.raises(ValidationError, match="who calibrated"):
        EquipmentEntry(kind=EquipmentEntryKind.CALIBRATION, on=DAY, passed=True, next_due=DAY, by=USER)
    with pytest.raises(ValidationError, match="next due date"):
        EquipmentEntry(kind=EquipmentEntryKind.CALIBRATION, on=DAY, passed=True, provider="x", by=USER)
    with pytest.raises(ValidationError, match="what failed"):
        EquipmentEntry(kind=EquipmentEntryKind.CALIBRATION, on=DAY, passed=False, provider="x", by=USER)
    with pytest.raises(ValidationError, match="belong to a calibration"):
        EquipmentEntry(kind=EquipmentEntryKind.CHECK, on=DAY, passed=True, provider="x", by=USER)
    with pytest.raises(ValidationError, match="what failed"):
        EquipmentEntry(kind=EquipmentEntryKind.CHECK, on=DAY, passed=False, by=USER)
    with pytest.raises(ValidationError, match="the reason"):
        EquipmentEntry(kind=EquipmentEntryKind.OUT_OF_SERVICE, on=DAY, by=USER)
    assert (
        EquipmentKind.PIPETTE.label == "Pipette" and EquipmentState.OUT_OF_SERVICE.label == "Out of service"
    )
    assert EquipmentEntryKind.CHECK.label == "Intermediate check"


def test_the_state_follows_the_entries_and_the_day():
    never = item()
    assert never.state(DAY) == EquipmentState.OVERDUE  # never calibrated
    good = item(cal(DAY, due_days=60))
    assert good.state(DAY) == EquipmentState.IN_SERVICE
    assert good.state(DAY + timedelta(days=31)) == EquipmentState.DUE  # due within 30 days
    assert good.state(DAY + timedelta(days=60)) == EquipmentState.DUE  # usable through the due day
    assert good.state(DAY + timedelta(days=61)) == EquipmentState.OVERDUE
    assert good.state(DAY - timedelta(days=1)) == EquipmentState.OVERDUE  # before it was calibrated
    failed = item(cal(DAY), cal(DAY + timedelta(days=10), passed=False))
    assert failed.state(DAY + timedelta(days=10)) == EquipmentState.OUT_OF_SERVICE
    assert failed.state(DAY + timedelta(days=9)) == EquipmentState.IN_SERVICE  # as it stood then
    not_required = item(kind=EquipmentKind.OTHER, required=False)
    assert not_required.state(DAY) == EquipmentState.IN_SERVICE


def test_returning_to_service_needs_a_passing_calibration():
    failed = item(cal(DAY), cal(DAY + timedelta(days=10), passed=False))
    back = EquipmentEntry(
        kind=EquipmentEntryKind.RETURNED, on=DAY + timedelta(days=12), note="fixed", by=USER
    )
    with pytest.raises(NotAllowed, match="calibrate"):
        failed.check(back)
    with pytest.raises(NotAllowed, match="already out of service"):
        failed.check(EquipmentEntry(kind=EquipmentEntryKind.OUT_OF_SERVICE, on=DAY, note="x", by=USER))
    recalibrated = failed.model_copy(update={"entries": (*failed.entries, cal(DAY + timedelta(days=11)))})
    assert recalibrated.out_of_service()  # a pass doesn't return it by itself
    recalibrated.check(back)
    returned = recalibrated.model_copy(update={"entries": (*recalibrated.entries, back)})
    assert returned.state(DAY + timedelta(days=12)) == EquipmentState.IN_SERVICE
    with pytest.raises(NotAllowed, match="is in service"):
        returned.check(back)
    with pytest.raises(NotAllowed, match="needs no calibration"):
        item(kind=EquipmentKind.OTHER, required=False).check(cal(DAY))
    out = item(
        EquipmentEntry(kind=EquipmentEntryKind.OUT_OF_SERVICE, on=DAY, note="dropped", by=USER),
        kind=EquipmentKind.OTHER,
        required=False,
    )
    out.check(back)  # no calibration needed to return it


def test_a_failure_window_starts_at_the_last_pass():
    failed = item(cal(DAY), cal(DAY + timedelta(days=100)), cal(DAY + timedelta(days=200), passed=False))
    assert failed.failure_window(2) == (DAY + timedelta(days=100), DAY + timedelta(days=200))
    assert failed.failure_window(1) is None  # it passed
    assert item(cal(DAY, passed=False)).failure_window(0) is None  # never passed: no window
    check = EquipmentEntry(
        kind=EquipmentEntryKind.CHECK, on=DAY + timedelta(days=5), passed=False, note="x", by=USER
    )
    assert item(cal(DAY), check).failure_window(1) == (DAY, DAY + timedelta(days=5))


def test_use_warns_or_blocks_by_the_kinds_setting():
    policy = EquipmentPolicy()
    register = {
        "BAL-1": item(cal(DAY - timedelta(days=400))),  # overdue balance: block
        "ICP-1": item(cal(DAY - timedelta(days=400)), kind=EquipmentKind.INSTRUMENT).model_copy(
            update={"name": "ICP-1"}
        ),
        "P100-1": item(cal(DAY), kind=EquipmentKind.PIPETTE),
        "OUT": item(
            EquipmentEntry(kind=EquipmentEntryKind.OUT_OF_SERVICE, on=DAY, note="x", by=USER),
            kind=EquipmentKind.OTHER,
            required=False,
        ),
        "NEW": item(),
    }
    found = check_use(register, ["BAL-1", "ICP-1", "P100-1", "OUT", "NEW", "GHOST", "BAL-1"], DAY, policy)
    assert [(p.name, p.effect) for p in found] == [
        ("BAL-1", Effect.BLOCK),
        ("ICP-1", Effect.WARN),
        ("OUT", Effect.BLOCK),
        ("NEW", Effect.BLOCK),
        ("GHOST", Effect.WARN),
    ]
    assert "overdue since 2026-04-28" in str(found[0]) and found[3].message == "never calibrated"
    relaxed = EquipmentPolicy(overdue={EquipmentKind.BALANCE: Effect.RECORDED}, unregistered=Effect.RECORDED)
    assert [p.name for p in check_use(register, ["BAL-1", "GHOST", "OUT"], DAY, relaxed)] == ["OUT"]
    assert relaxed.effect(EquipmentKind.PIPETTE) == Effect.WARN  # a kind left out warns


# --- reference lots (D-127) ---------------------------------------------------------------------------


def lot(**kw):
    fields = dict(
        name="Multi-element stock A",
        lot="DEMO-0001",
        supplier="Demo Standards Ltd",
        certificate="CERT-1",
        value=D(1000),
        unit="ppm",
        elements=("Cu", "Fe"),
        expires=date(2027, 6, 1),
        shelf_life_opened_days=180,
        registered_by=USER,
    )
    return ReferenceLot(**(fields | kw))


def test_a_lot_is_usable_through_its_effective_expiry():
    sealed = lot()
    assert sealed.effective_expiry == date(2027, 6, 1) and sealed.unusable_on(date(2027, 6, 1)) is None
    assert sealed.unusable_on(date(2027, 6, 2)) == "expired on 2027-06-01"
    assert str(sealed) == "Multi-element stock A lot DEMO-0001"
    opened = sealed.model_copy(update={"entries": (LotEntry(kind=LotEntryKind.OPENED, on=DAY, by=USER),)})
    assert opened.opened_on == DAY and opened.effective_expiry == DAY + timedelta(days=180)
    no_shelf_life = lot(shelf_life_opened_days=None).model_copy(update={"entries": opened.entries})
    assert no_shelf_life.effective_expiry == date(2027, 6, 1)
    with pytest.raises(NotAllowed, match="was opened"):
        opened.check(LotEntry(kind=LotEntryKind.OPENED, on=DAY, by=USER))
    gone = LotEntry(kind=LotEntryKind.WITHDRAWN, on=DAY + timedelta(days=3), note="contaminated", by=USER)
    withdrawn = opened.model_copy(update={"entries": (*opened.entries, gone)})
    assert withdrawn.unusable_on(DAY + timedelta(days=2)) is None
    assert withdrawn.unusable_on(DAY + timedelta(days=3)) == f"withdrawn on {DAY + timedelta(days=3)}"
    with pytest.raises(NotAllowed, match="withdrawn"):
        withdrawn.check(gone)
    withdrawn.check(LotEntry(kind=LotEntryKind.NOTE, on=DAY, note="disposed of", by=USER))
    with pytest.raises(ValidationError, match="the reason"):
        LotEntry(kind=LotEntryKind.WITHDRAWN, on=DAY, by=USER)
    with pytest.raises(ValidationError, match="named twice"):
        lot(elements=("Cu", "Cu"))
    assert LotEntryKind.OPENED.label == "Opened"


# --- proficiency testing (D-128, D-129) ---------------------------------------------------------------


def pt_round(*entries):
    return PtRound(
        id=7,
        scheme="Demo PT Metals",
        round_code="R-24",
        tr_number="TR00042",
        sample="TR00042-01",
        analytes=("Cu", "Zn"),
        due=DAY,
        registered_by=USER,
        entries=entries,
    )


SENT = PtEntry(
    kind=PtEntryKind.SUBMITTED,
    values=(
        LabValue(analyte="Cu", value=D("10.6"), unit="ppb", result_ref="r1"),
        LabValue(analyte="Zn", value=D("21.0"), unit="ppb", result_ref="r2"),
    ),
    by=USER,
    at=AT,
)
SCORED = PtEntry(
    kind=PtEntryKind.SCORED,
    assigned=(
        Assigned(analyte="Cu", assigned=D("10"), sigma=D("0.25"), provider_z=D("2.4")),
        Assigned(analyte="Zn", assigned=D("20"), sigma=D("0.3")),
    ),
    report_date=DAY,
    by=USER,
    at=AT,
)


def test_pt_entries_carry_the_fields_of_their_kind():
    with pytest.raises(ValidationError, match="lab's values"):
        PtEntry(kind=PtEntryKind.SUBMITTED, by=USER)
    with pytest.raises(ValidationError, match="provider's values"):
        PtEntry(kind=PtEntryKind.SCORED, report_date=DAY, by=USER)
    with pytest.raises(ValidationError, match="report date"):
        PtEntry(kind=PtEntryKind.SCORED, assigned=SCORED.assigned, by=USER)
    with pytest.raises(ValidationError, match="write the note"):
        PtEntry(kind=PtEntryKind.NOTE, by=USER)
    with pytest.raises(ValidationError, match="given twice"):
        PtEntry(kind=PtEntryKind.SUBMITTED, values=(SENT.values[0], SENT.values[0]), by=USER)
    with pytest.raises(ValidationError, match="named twice"):
        pt_round().model_copy(update={"analytes": ("Cu", "Cu")}).model_validate(
            pt_round().model_dump() | {"analytes": ("Cu", "Cu")}
        )
    with pytest.raises(ValidationError, match="below unsatisfactory"):
        PtPolicy(questionable=D(3), unsatisfactory=D(3))


def test_a_round_is_submitted_once_then_scored():
    fresh = pt_round()
    assert (
        fresh.state == PtState.REGISTERED
        and fresh.overdue(DAY + timedelta(days=1))
        and not fresh.overdue(DAY)
    )
    assert fresh.scores(PtPolicy()) == () and fresh.worst(PtPolicy()) is None
    with pytest.raises(NotAllowed, match="before entering"):
        fresh.check(SCORED)
    with pytest.raises(NotAllowed, match="every analyte"):
        fresh.check(SENT.model_copy(update={"values": SENT.values[:1]}))
    fresh.check(SENT)
    sent = pt_round(SENT)
    assert sent.state == PtState.SUBMITTED and not sent.overdue(DAY + timedelta(days=9))
    with pytest.raises(NotAllowed, match="were submitted"):
        sent.check(SENT)
    with pytest.raises(NotAllowed, match="every analyte"):
        sent.check(SCORED.model_copy(update={"assigned": SCORED.assigned[:1]}))
    sent.check(SCORED)
    scored = pt_round(SENT, SCORED)
    assert scored.state == PtState.SCORED
    with pytest.raises(NotAllowed, match="is scored"):
        scored.check(SCORED)
    cu, zn = scored.scores(PtPolicy())
    assert (cu.z, cu.shown, cu.klass, cu.provider_differs) == (
        D("2.4"),
        D("2.4"),
        PtClass.QUESTIONABLE,
        False,
    )
    assert (zn.shown, zn.klass, zn.provider_differs) == (D("3.3"), PtClass.UNSATISFACTORY, False)
    assert scored.worst(PtPolicy()) == PtClass.UNSATISFACTORY
    other = SCORED.assigned[0].model_copy(update={"provider_z": D("2.3")})
    differs = pt_round(SENT, SCORED.model_copy(update={"assigned": (other, SCORED.assigned[1])}))
    assert differs.scores(PtPolicy())[0].provider_differs
    assert PtState.SCORED.label == "Scored" and PtClass.QUESTIONABLE.label == "Questionable"


def test_classes_are_judged_unrounded_on_the_profiles_cut_offs():
    policy = PtPolicy()
    assert classify(D("2"), policy) == PtClass.SATISFACTORY
    assert classify(D("-2.0001"), policy) == PtClass.QUESTIONABLE  # shown as -2.0, still questionable
    assert classify(D("-3"), policy) == PtClass.UNSATISFACTORY
    assert classify(D("2.5"), PtPolicy(questionable=D("2.5"), unsatisfactory=D(4))) == PtClass.SATISFACTORY


def test_z_scores_make_a_series_per_scheme_and_analyte():
    later = SCORED.model_copy(update={"report_date": DAY + timedelta(days=90)})
    second = pt_round(SENT, later).model_copy(update={"id": 8, "round_code": "R-25"})
    found = z_series([second, pt_round(SENT, SCORED), pt_round(SENT)])
    assert [s.key.label for s in found] == [
        "Demo PT Metals · Cu · PT z-score",
        "Demo PT Metals · Zn · PT z-score",
    ]
    cu = found[0]
    assert cu.key.measure == Measure.PT_Z and [p.run_code for p in cu.points] == ["R-24", "R-25"]
    assert cu.points[0].value == D("2.4") and cu.points[0].ref == "pt:7:Cu"


def test_the_profile_carries_equipment_and_pt_settings_under_change_control():
    before = Profile()
    after = Profile(
        equipment=EquipmentPolicy(
            overdue={EquipmentKind.INSTRUMENT: Effect.BLOCK}, unregistered=Effect.BLOCK, due_within_days=14
        ),
        pt=PtPolicy(questionable=D("2.5"), unsatisfactory=D(3)),
    )
    lines = differences(before, after)
    assert "Instrument overdue: Warn → Block" in lines
    assert "Balance overdue: Block → Warn" in lines  # left out: warns
    assert "Unregistered equipment: Warn → Block" in lines
    assert "Equipment due within (days): 30 → 14" in lines
    assert "PT questionable above |z|: 2 → 2.5" in lines
    assert not any(line.startswith("PT unsatisfactory") for line in lines)
    assert Effect.BLOCK.label == "Block"


# --- complaints (D-130) -------------------------------------------------------------------------------


def entry(kind, by=USER, **kw):
    to_customer = kind in (
        ComplaintEntryKind.ACKNOWLEDGED,
        ComplaintEntryKind.OUTCOME,
        ComplaintEntryKind.CLOSED,
    )
    fields = dict(kind=kind, note="text", to_customer=to_customer, by=by)
    if kind == ComplaintEntryKind.INVESTIGATED:
        fields["valid"] = True
    return ComplaintEntry(**(fields | kw))


def complaint(*entries):
    return Complaint(
        id=3,
        tr_number="TR00008",
        customer_id="1",
        text="The Cu result looks high",
        raised_by=Party(id="u1", display="Ann Customer"),
        entries=entries,
    )


def test_complaint_entries_say_who_sees_them():
    with pytest.raises(ValidationError, match="goes to the customer"):
        entry(ComplaintEntryKind.ACKNOWLEDGED, to_customer=False)
    with pytest.raises(ValidationError, match="is internal"):
        entry(ComplaintEntryKind.NOTE, to_customer=True)
    with pytest.raises(ValidationError, match="whether it is valid"):
        entry(ComplaintEntryKind.PROGRESS, valid=True)
    with pytest.raises(ValidationError, match="whether it is valid"):
        entry(ComplaintEntryKind.INVESTIGATED, valid=None)
    assert (
        ComplaintState.OUTCOME_APPROVED.label == "Outcome approved"
        and ComplaintEntryKind.NOTE.label == "Note"
    )


def test_a_complaint_moves_through_its_states_in_order():
    fresh = complaint()
    assert fresh.number == "CP-0003" and fresh.state == ComplaintState.RECEIVED
    assert Complaint.model_validate(fresh.model_dump() | {"id": None}).number == "CP-new"
    with pytest.raises(NotAllowed, match="acknowledge"):
        fresh.check(entry(ComplaintEntryKind.INVESTIGATED))
    with pytest.raises(NotAllowed, match="investigate before"):
        fresh.check(entry(ComplaintEntryKind.OUTCOME, by=MANAGER))
    with pytest.raises(NotAllowed, match="outcome is approved"):
        fresh.check(entry(ComplaintEntryKind.CLOSED))
    fresh.check(entry(ComplaintEntryKind.PROGRESS, to_customer=True))
    acked = complaint(entry(ComplaintEntryKind.ACKNOWLEDGED))
    with pytest.raises(NotAllowed, match="was acknowledged"):
        acked.check(entry(ComplaintEntryKind.ACKNOWLEDGED))
    investigated = complaint(
        *acked.entries, entry(ComplaintEntryKind.INVESTIGATED), entry(ComplaintEntryKind.NOTE)
    )
    assert investigated.state == ComplaintState.INVESTIGATED and investigated.investigation.valid
    investigated.check(entry(ComplaintEntryKind.INVESTIGATED, valid=False))  # re-investigated: allowed
    with pytest.raises(NotAllowed, match="quality manager approves"):
        investigated.check(entry(ComplaintEntryKind.OUTCOME))
    done = complaint(
        *investigated.entries, entry(ComplaintEntryKind.OUTCOME, by=MANAGER), entry(ComplaintEntryKind.CLOSED)
    )
    assert done.state == ComplaintState.CLOSED
    assert [e.kind for e in done.for_customer] == [
        ComplaintEntryKind.ACKNOWLEDGED,
        ComplaintEntryKind.OUTCOME,
        ComplaintEntryKind.CLOSED,
    ]
    with pytest.raises(NotAllowed, match="is closed"):
        done.check(entry(ComplaintEntryKind.NOTE))


def test_the_outcome_is_approved_by_someone_with_no_hand_in_the_work():
    """§7.9.6: the host passes who had a hand; slim-quality refuses them."""
    investigated = complaint(entry(ComplaintEntryKind.ACKNOWLEDGED), entry(ComplaintEntryKind.INVESTIGATED))
    with pytest.raises(NotAllowed, match="had a hand in TR00008"):
        investigated.check(entry(ComplaintEntryKind.OUTCOME, by=MANAGER), involved={"qm"})
    investigated.check(entry(ComplaintEntryKind.OUTCOME, by=OTHER_MANAGER), involved={"qm"})


def test_new_nonconformity_sources_have_labels():
    assert [s.label for s in (NcSource.EQUIPMENT, NcSource.PT, NcSource.COMPLAINT)] == [
        "From equipment",
        "From a PT round",
        "From a complaint",
    ]
