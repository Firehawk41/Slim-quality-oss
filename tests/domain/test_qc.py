"""QC records into series (the host's handover) and the invented demo history."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from slim_quality.demo.history import DemoSource, as_records, history, maintenance_at
from slim_quality.domain.enums import Measure
from slim_quality.domain.qc import CalibrationRecord, QcRecord, to_series
from slim_quality.monitors.runner import evaluate
from slim_quality.service.quality import STARTER

T = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
END = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def record(kind, measured, seq, **kw):
    return QcRecord(
        kind=kind,
        ref=f"r{seq}",
        instrument="ICP-1",
        at=T,
        run_code="R1",
        sequence=seq,
        analyte="Cu",
        conditions="KED",
        measured=Decimal(measured),
        unit="ppb",
        **kw,
    )


def test_records_become_one_series_per_measure_and_key():
    records = [
        record(
            "check",
            "4.9",
            1,
            expected=Decimal(5),
            material="CCV",
            rsd=Decimal("1.1"),
            istd_recovery=Decimal(95),
        ),
        record("check", "5.1", 1, expected=Decimal(5), material="CCV", istd_recovery=Decimal(95)),  # same row
        record("crm", "9", 2, expected=Decimal(10), material="CRM-A"),
        record("crm", "9", 3),  # no certified value: not charted
        record("blank", "0.02", 4),
    ]
    cal = CalibrationRecord(
        instrument="ICP-1", at=T, run_code="R1", analyte="Cu", unit="ppb", lod=Decimal("0.01")
    )
    found = {s.key.measure: s for s in to_series(records, [cal])}
    assert set(found) == {
        Measure.CHECK_RECOVERY,
        Measure.CHECK_RSD,
        Measure.CRM_RECOVERY,
        Measure.BLANK_LEVEL,
        Measure.ISTD_RECOVERY,
        Measure.CAL_LOD,
    }
    check = found[Measure.CHECK_RECOVERY]
    assert [p.value for p in check.points] == [Decimal("0.98"), Decimal("1.02")]
    assert check.key.level == 5 and check.key.material == "CCV" and check.unit == ""
    assert len(found[Measure.ISTD_RECOVERY].points) == 1 and found[Measure.ISTD_RECOVERY].unit == "%"
    assert found[Measure.BLANK_LEVEL].unit == "ppb" and found[Measure.CAL_LOD].points[0].ref == "cal:R1:Cu"


def test_the_demo_history_is_fixed_and_finds_what_was_planted():
    runs = history(END, days=120)
    assert runs == history(END, days=120) and runs[0].code.startswith("QC-ICPMS1-")
    assert all(r.at.weekday() < 5 for r in runs)
    assert maintenance_at(END) < END
    records, calibrations = as_records(runs[:2])
    assert len(records) == 2 * (6 + 3) and len(calibrations) == 2 * 3
    charts = {c.series.key.label: c for c in evaluate(DemoSource(END).series(), STARTER)}
    drift = charts["ICP-MS 1 · Internal standard recovery"]
    assert {"EWMA_LOW", "LIMIT_AHEAD"} <= {f.code for f in drift.flags}
    zinc = [c for label, c in charts.items() if "Zn (KED) · Method blank" in label]
    assert all("SHARED_SHIFT" in {f.code for f in c.flags} for c in zinc) and len(zinc) == 2
    recent = DemoSource(END).series(since=END - timedelta(days=7))
    assert all(p.at >= END - timedelta(days=7) for s in recent for p in s.points)


def test_q6_one_level_however_the_file_wrote_it():  # review 2026-10-30b
    records = [
        record("check", "4.9", 1, expected=Decimal("5"), material="CCV"),
        record("check", "5.0", 2, expected=Decimal("5.000"), material="CCV"),
        record("check", "99", 3, expected=Decimal("1E+2"), material="CCV"),
    ]
    levels = sorted(s.key.level for s in to_series(records) if s.key.measure == Measure.CHECK_RECOVERY)
    assert levels == [Decimal("5"), Decimal("100")] and "@ 100" in str(
        [s.key.label for s in to_series(records)]
    )
