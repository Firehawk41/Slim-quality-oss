"""Invented QC history (D-05: never a real lab's numbers), seeded, so every run of the demo is the same.

Two ICP-MS instruments, one QC run per working day each, for `days` days up to `end`: a calibration,
check standards (CCV) at the start and end of the run, and a method blank. Fe and Zn in KED mode, Na
in cool plasma: each mode a series of its own (D-106).

Planted, for the charts to find:
  * ICP-MS 1, cone wear over the last 5 weeks: the internal standard sags, the check recoveries creep
    up while every one still passes the ±25 % window, and Na's BEC rises. The forecast says when the
    window will be crossed; EWMA and Westgard's 4_1s / nx catch it first.
  * Zn blanks up on both instruments over the last 10 days: a new acid lot, a shared cause.
  * Na blanks: contamination spikes now and then (skewed data, the reason blanks get robust limits).
  * A maintenance event on ICP-1 two months back (cones cleaned): the host records it with the history.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

INSTRUMENTS = ("ICP-MS 1", "ICP-MS 2")
ELEMENTS = {"Fe": "KED", "Zn": "KED", "Na": "Cool"}  # element -> plasma mode
CHECK_LEVEL = Decimal("5")  # ppb
CHECK_MATERIAL = "CCV 5 ppb"
WEAR_DAYS = 35  # ICP-MS 1's cone wear: the last 35 days
SHARED_DAYS = 10  # the Zn acid lot: the last 10 days
MAINTENANCE_DAYS_AGO = 60


@dataclass(frozen=True)
class DemoCheck:
    values: dict  # element -> measured, ppb
    rsd: dict  # element -> %
    istd: Decimal  # %


@dataclass(frozen=True)
class DemoRun:
    instrument: str
    at: datetime
    checks: tuple[DemoCheck, ...]  # start and end of the run
    blank: dict  # element -> measured, ppb
    blank_istd: Decimal
    calibration: dict = field(default_factory=dict)  # element -> (r, R², LOD, BEC)

    @property
    def code(self) -> str:
        return f"QC-{self.instrument.replace(' ', '').replace('-', '')}-{self.at:%Y%m%d}"


def _d(value: float, places: int) -> Decimal:
    return Decimal(f"{value:.{places}f}")


def history(end: datetime, days: int = 120, seed: int = 17) -> tuple[DemoRun, ...]:
    """Oldest first. `end` must be timezone-aware: the last run is on its date."""
    rng = random.Random(seed)
    runs = []
    first = end - timedelta(days=days)
    for offset in range(days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        ago = (end - day).days
        for instrument in INSTRUMENTS:
            wear = max(0.0, (WEAR_DAYS - ago) / WEAR_DAYS) if instrument == INSTRUMENTS[0] else 0.0
            at = day.replace(
                hour=8, minute=30 if instrument == INSTRUMENTS[0] else 45, second=0, microsecond=0
            )
            checks = []
            for _ in range(2):
                istd = 97.0 - 22.0 * wear + rng.gauss(0, 1.5)
                values = {
                    e: _d(float(CHECK_LEVEL) * (1.0 + 0.10 * wear + rng.gauss(0, 0.022)), 3) for e in ELEMENTS
                }
                rsd = {e: _d(abs(rng.gauss(1.5 + 2.0 * wear, 0.5)) + 0.2, 2) for e in ELEMENTS}
                checks.append(DemoCheck(values=values, rsd=rsd, istd=_d(istd, 1)))
            blank = {
                "Fe": _d(abs(rng.gauss(0.040, 0.008)), 4),
                "Zn": _d(abs(rng.gauss(0.030 + (0.025 if ago < SHARED_DAYS else 0.0), 0.006)), 4),
                "Na": _d(rng.lognormvariate(-3.2, 0.35) + (0.25 if rng.random() < 0.06 else 0.0), 4),
            }
            calibration = {
                e: (
                    _d(0.99990 - abs(rng.gauss(0, 0.00004)), 5),
                    _d(0.99980 - abs(rng.gauss(0, 0.00008)), 5),
                    _d(abs(rng.gauss(0.010, 0.002)) + 0.002, 4),
                    _d(abs(rng.gauss(0.020 + (0.03 * wear if e == "Na" else 0.0), 0.004)) + 0.002, 4),
                )
                for e in ELEMENTS
            }
            runs.append(
                DemoRun(
                    instrument=instrument,
                    at=at,
                    checks=tuple(checks),
                    blank=blank,
                    blank_istd=_d(98.0 - 15.0 * wear + rng.gauss(0, 1.5), 1),
                    calibration=calibration,
                )
            )
    return tuple(runs)


def maintenance_at(end: datetime) -> datetime:
    return (end - timedelta(days=MAINTENANCE_DAYS_AGO)).replace(hour=16, minute=0, second=0, microsecond=0)


def as_records(runs):
    """The history as slim-quality's own QC records, for a standalone demo or tests (no host needed)."""
    from slim_quality.domain.qc import CalibrationRecord, QcRecord

    records, calibrations = [], []
    for run in runs:
        sequence = 1
        for n, check in enumerate(run.checks):
            for element, mode in ELEMENTS.items():
                records.append(
                    QcRecord(
                        kind="check",
                        ref=f"{run.code}:{n}:{element}",
                        instrument=run.instrument,
                        at=run.at,
                        run_code=run.code,
                        sequence=sequence,
                        analyte=element,
                        conditions=mode,
                        measured=check.values[element],
                        unit="ppb",
                        expected=CHECK_LEVEL,
                        material=CHECK_MATERIAL,
                        rsd=check.rsd[element],
                        istd_recovery=check.istd,
                    )
                )
            sequence += 2
        for element, mode in ELEMENTS.items():
            records.append(
                QcRecord(
                    kind="blank",
                    ref=f"{run.code}:blank:{element}",
                    instrument=run.instrument,
                    at=run.at,
                    run_code=run.code,
                    sequence=2,
                    analyte=element,
                    conditions=mode,
                    measured=run.blank[element],
                    unit="ppb",
                    istd_recovery=run.blank_istd,
                )
            )
            r, _, lod, bec = run.calibration[element]
            calibrations.append(
                CalibrationRecord(
                    instrument=run.instrument,
                    at=run.at,
                    run_code=run.code,
                    analyte=element,
                    conditions=mode,
                    unit="ppb",
                    correlation=r,
                    lod=lod,
                    bec=bec,
                )
            )
    return records, calibrations


class DemoSource:
    """A `QcSource` over the invented history, for the standalone demo and tests."""

    def __init__(self, end: datetime, days: int = 120) -> None:
        from slim_quality.domain.qc import to_series

        records, calibrations = as_records(history(end, days))
        self._series = to_series(records, calibrations)

    def series(self, since=None, as_of=None):
        from slim_quality.domain.series import Series

        if since is None:
            return self._series
        return tuple(
            Series(key=s.key, points=tuple(p for p in s.points if p.at >= since), unit=s.unit)
            for s in self._series
        )
