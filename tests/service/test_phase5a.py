"""The service on a migrated database: the equipment register, lots, PT rounds and complaints."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from slim_quality.demo.history import DemoSource
from slim_quality.domain.base import NotAllowed
from slim_quality.domain.complaints import ComplaintEntry, ComplaintEntryKind, ComplaintState, Party
from slim_quality.domain.enums import Effect, Measure
from slim_quality.domain.equipment import EquipmentEntry, EquipmentEntryKind, EquipmentKind, EquipmentState
from slim_quality.domain.materials import LotEntry, LotEntryKind, ReferenceLot
from slim_quality.domain.nonconformity import NcSource
from slim_quality.domain.pt import Assigned, LabValue, PtClass, PtEntry, PtEntryKind
from slim_quality.persistence import store
from slim_quality.service import complaints, pt, register
from slim_quality.service import quality as q
from tests.factories import MANAGER, OTHER_MANAGER, USER

DAY = date(2026, 6, 1)
ZONE = ZoneInfo("America/Chicago")
SOURCE = DemoSource(datetime(2026, 10, 28, 12, tzinfo=UTC))


def cal(on, passed=True):
    return EquipmentEntry(
        kind=EquipmentEntryKind.CALIBRATION,
        on=on,
        passed=passed,
        provider="in house",
        next_due=on + timedelta(days=365) if passed else None,
        note="" if passed else "0.3 mg high at 100 g",
        by=USER,
    )


def test_equipment_is_registered_once_and_its_entries_checked(session):
    balance = register.register_equipment(session, " BAL-1 ", EquipmentKind.BALANCE, USER, "Lab balance")
    assert balance.name == "BAL-1" and balance.registered_at is not None
    with pytest.raises(register.AlreadyRegistered):
        register.register_equipment(session, "BAL-1", EquipmentKind.BALANCE, USER)
    register.add_equipment_entry(session, balance.id, cal(DAY))
    failed = register.add_equipment_entry(session, balance.id, cal(DAY + timedelta(days=100), passed=False))
    assert failed.state(DAY + timedelta(days=100)) == EquipmentState.OUT_OF_SERVICE
    back = EquipmentEntry(kind=EquipmentEntryKind.RETURNED, on=DAY + timedelta(days=101), note="ok", by=USER)
    with pytest.raises(NotAllowed):
        register.add_equipment_entry(session, balance.id, back)
    with pytest.raises(store.NotFound):
        register.add_equipment_entry(session, 999, back)
    with pytest.raises(store.NotFound):
        store.get_equipment(session, 999)
    assert list(register.register(session)) == ["BAL-1"]


def test_equipment_use_follows_the_profile(session):
    balance = register.register_equipment(session, "BAL-1", EquipmentKind.BALANCE, USER)
    register.add_equipment_entry(session, balance.id, cal(DAY - timedelta(days=400)))
    problems = register.equipment_use(session, ["BAL-1", "GHOST"], DAY)
    assert [(p.name, p.effect) for p in problems] == [("BAL-1", Effect.BLOCK), ("GHOST", Effect.WARN)]


def test_a_failed_calibration_raises_an_nc_whose_window_starts_at_the_last_pass(session):
    pipette = register.register_equipment(session, "P100-1", EquipmentKind.PIPETTE, USER)
    register.add_equipment_entry(session, pipette.id, cal(DAY))
    register.add_equipment_entry(session, pipette.id, cal(DAY + timedelta(days=90), passed=False))
    with pytest.raises(NotAllowed, match="from a failed"):
        register.failure_nc(session, pipette.id, 0, MANAGER, ZONE)
    with pytest.raises(NotAllowed, match="from a failed"):
        register.failure_nc(session, pipette.id, 7, MANAGER, ZONE)
    nc = register.failure_nc(session, pipette.id, 1, MANAGER, ZONE)
    assert (
        nc.source == NcSource.EQUIPMENT
        and nc.instrument == "P100-1"
        and nc.source_ref == f"equipment:{pipette.id}:1"
    )
    assert nc.window_from.astimezone(ZONE).date() == DAY
    assert nc.window_to.astimezone(ZONE).date() == DAY + timedelta(days=90)
    never = register.register_equipment(session, "P100-2", EquipmentKind.PIPETTE, USER)
    register.add_equipment_entry(session, never.id, cal(DAY, passed=False))
    assert register.failure_nc(session, never.id, 0, MANAGER, ZONE).window_from is None


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


def test_lots_are_registered_once_and_found_by_name_and_lot(session):
    found = register.register_lot(session, lot())
    with pytest.raises(register.AlreadyRegistered):
        register.register_lot(session, lot())
    assert register.lot_for_use(session, "Multi-element stock A", "DEMO-0001") == found
    assert register.lot_for_use(session, "Multi-element stock A", "NOPE") is None
    opened = register.add_lot_entry(session, found.id, LotEntry(kind=LotEntryKind.OPENED, on=DAY, by=USER))
    assert opened.effective_expiry == DAY + timedelta(days=180) and opened.entries[0].at is not None
    with pytest.raises(NotAllowed):
        register.add_lot_entry(session, found.id, LotEntry(kind=LotEntryKind.OPENED, on=DAY, by=USER))
    with pytest.raises(store.NotFound):
        store.get_lot(session, 999)
    assert [x.lot for x in store.list_lots(session)] == ["DEMO-0001"]


def sent(cu, zn, at=None):
    return PtEntry(
        kind=PtEntryKind.SUBMITTED,
        values=(
            LabValue(analyte="Cu", value=D(cu), unit="ppb", result_ref="r:Cu"),
            LabValue(analyte="Zn", value=D(zn), unit="ppb", result_ref="r:Zn"),
        ),
        by=USER,
    )


SCORED = PtEntry(
    kind=PtEntryKind.SCORED,
    assigned=(
        Assigned(analyte="Cu", assigned=D(10), sigma=D(1)),
        Assigned(analyte="Zn", assigned=D(20), sigma=D(1)),
    ),
    report_date=DAY,
    by=USER,
)


def test_pt_rounds_score_and_draw_z_charts_judged_on_fixed_limits(session):
    values = [("10.4", "19.5"), ("9.1", "20.2"), ("10.2", "23.4")]
    rounds = []
    for i, (cu, zn) in enumerate(values):
        r = pt.register_round(
            session, "Demo PT Metals", f"R-{i}", f"TR0000{i}", f"TR0000{i}-01", ("Cu", "Zn"), DAY, USER
        )
        pt.add_entry(session, r.id, sent(cu, zn))
        scored = SCORED.model_copy(update={"report_date": DAY + timedelta(days=90 * i)})
        rounds.append(pt.add_entry(session, r.id, scored))
    with pytest.raises(register.AlreadyRegistered):
        pt.register_round(session, "Demo PT Metals", "R-0", "TR9", "TR9-01", ("Cu",), DAY, USER)
    with pytest.raises(NotAllowed):
        pt.add_entry(session, rounds[0].id, SCORED)
    last = rounds[-1]
    assert [s.klass for s in pt.scores(session, last)] == [PtClass.SATISFACTORY, PtClass.UNSATISFACTORY]
    assert store.list_pt_rounds(session, tr_number="TR00002") == [last]
    with pytest.raises(store.NotFound):
        store.get_pt_round(session, 999)
    zn = next(
        c
        for c in q.charts(session, SOURCE)
        if c.series.key.measure == Measure.PT_Z and c.series.key.analyte == "Zn"
    )
    assert zn.current.center == 0 and zn.current.sd == 1 and not zn.current.provisional
    assert any(f.code == "WG_1_3S" for f in zn.flags)  # |z| ≥ 3: unsatisfactory is Westgard 1-3s (D-129)
    with pytest.raises(NotAllowed, match="unsatisfactory"):
        pt.unsatisfactory_nc(session, last.id, "Cu", MANAGER)
    nc = pt.unsatisfactory_nc(session, last.id, "Zn", MANAGER)
    assert nc.source == NcSource.PT and "z = 3.4" in nc.title
    assert any(row.code == "WG_1_3S" and row.in_force for row in q.backtest(session, SOURCE, q.STARTER))


def test_complaints_follow_their_entries_and_refuse_the_involved(session):
    raised = complaints.raise_complaint(
        session, "TR00008", "1", " The Cu looks high ", Party(id="u1", display="Ann"), issue_id=4
    )
    assert raised.text == "The Cu looks high" and raised.state == ComplaintState.RECEIVED
    other = complaints.raise_complaint(
        session, "TR00009", "2", "late", Party(id="s1", display="Sue"), by_staff=True
    )

    def add(kind, by=USER, involved=(), **kw):
        to_customer = kind in (
            ComplaintEntryKind.ACKNOWLEDGED,
            ComplaintEntryKind.OUTCOME,
            ComplaintEntryKind.CLOSED,
        )
        entry = ComplaintEntry(kind=kind, note="n", to_customer=to_customer, by=by, **kw)
        return complaints.add_entry(session, raised.id, entry, involved)

    add(ComplaintEntryKind.ACKNOWLEDGED)
    add(ComplaintEntryKind.INVESTIGATED, valid=True)
    with pytest.raises(NotAllowed, match="had a hand"):
        add(ComplaintEntryKind.OUTCOME, by=MANAGER, involved={"qm"})
    add(ComplaintEntryKind.OUTCOME, by=OTHER_MANAGER, involved={"qm"})
    done = add(ComplaintEntryKind.CLOSED)
    assert done.state == ComplaintState.CLOSED and len(done.for_customer) == 3
    assert store.list_complaints(session, customer_id="2") == [other]
    assert store.list_complaints(session, tr_number="TR00008") == [done]
    with pytest.raises(store.NotFound):
        complaints.add_entry(session, 999, ComplaintEntry(kind=ComplaintEntryKind.NOTE, note="x", by=USER))
    with pytest.raises(store.NotFound):
        store.get_complaint(session, 999)
    nc = complaints.complaint_nc(session, other.id, MANAGER)
    assert nc.source == NcSource.COMPLAINT and nc.source_ref == f"complaint:{other.id}"
