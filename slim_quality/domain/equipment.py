"""The equipment register (D-125, ISO 17025 §6.4): instruments, balances, pipettes, each by the name the
other modules already record (slim-data's instrument, slim-prep's lists), with an append-only list of
entries. The state follows the entries and the day asked about, never set by hand:

    out of service   taken out, or a calibration or check failed, and not returned since
    overdue          calibration required and its due date passed (or never calibrated)
    due              due within the profile's days
    in service       otherwise

What an overdue or out-of-service item does where it is used is the profile's (D-126): a lab setting per
kind, under change control; out of service always blocks.
"""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed
from slim_quality.domain.enums import Effect


class EquipmentKind(IntEnum):
    INSTRUMENT = 1
    BALANCE = 2
    PIPETTE = 3
    OTHER = 9

    @property
    def label(self) -> str:
        return self.name.capitalize()


class EquipmentState(IntEnum):
    IN_SERVICE = 1
    DUE = 2
    OVERDUE = 3
    OUT_OF_SERVICE = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


class EquipmentEntryKind(IntEnum):
    CALIBRATION = 1
    CHECK = 2  # an intermediate check (§6.4.10)
    OUT_OF_SERVICE = 3
    RETURNED = 4
    NOTE = 5

    @property
    def label(self) -> str:
        return {
            1: "Calibration",
            2: "Intermediate check",
            3: "Out of service",
            4: "Returned to service",
            5: "Note",
        }[self]


def _default_overdue() -> dict["EquipmentKind", Effect]:
    return {
        EquipmentKind.INSTRUMENT: Effect.WARN,  # a lapsed service does not void a run that passed QC
        EquipmentKind.BALANCE: Effect.BLOCK,
        EquipmentKind.PIPETTE: Effect.BLOCK,
        EquipmentKind.OTHER: Effect.WARN,
    }


class EquipmentPolicy(Frozen):
    """The profile's equipment settings (D-126)."""

    overdue: dict[EquipmentKind, Effect] = Field(default_factory=_default_overdue)
    unregistered: Effect = Effect.WARN  # a name used that isn't in the register
    due_within_days: int = Field(default=30, ge=0, le=365)

    def effect(self, kind: EquipmentKind) -> Effect:
        return self.overdue.get(kind, Effect.WARN)


class EquipmentEntry(Frozen):
    kind: EquipmentEntryKind
    on: date  # the day it was done
    passed: bool | None = None  # a calibration or check
    provider: str = Field(default="", max_length=200)  # "in house" or the calibration lab
    certificate: str = Field(default="", max_length=100)
    next_due: date | None = None  # a passing calibration
    note: str = Field(default="", max_length=2000)
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _fields_for_its_kind(self) -> Self:
        tested = self.kind in (EquipmentEntryKind.CALIBRATION, EquipmentEntryKind.CHECK)
        if tested != (self.passed is not None):
            raise ValueError("a calibration or a check passes or fails, and only they do")
        if self.kind == EquipmentEntryKind.CALIBRATION:
            if not self.provider.strip():
                raise ValueError("say who calibrated it")
            if self.passed and (self.next_due is None or self.next_due <= self.on):
                raise ValueError("a passing calibration gives the next due date, after the day it was done")
            if not self.passed and not self.note.strip():
                raise ValueError("say what failed")
        elif self.next_due is not None or self.provider or self.certificate:
            raise ValueError("provider, certificate and next due belong to a calibration")
        if self.kind == EquipmentEntryKind.CHECK and not self.passed and not self.note.strip():
            raise ValueError("say what failed")
        if self.kind in (
            EquipmentEntryKind.OUT_OF_SERVICE,
            EquipmentEntryKind.RETURNED,
            EquipmentEntryKind.NOTE,
        ):
            if not self.note.strip():
                raise ValueError("write the reason")
        return self

    @property
    def failed(self) -> bool:
        return self.passed is False


class Equipment(Frozen):
    id: int | None = None
    name: str = Field(min_length=1, max_length=100)  # as slim-data and slim-prep record it
    kind: EquipmentKind
    description: str = Field(default="", max_length=500)
    calibration_required: bool = True
    registered_by: QualityActor
    registered_at: AwareDatetime | None = None
    entries: tuple[EquipmentEntry, ...] = ()

    def _until(self, day: date | None) -> list[EquipmentEntry]:
        return [e for e in self.entries if day is None or e.on <= day]

    def out_of_service(self, day: date | None = None) -> bool:
        out = False
        for e in self._until(day):
            if e.kind == EquipmentEntryKind.OUT_OF_SERVICE or e.failed:
                out = True
            elif e.kind == EquipmentEntryKind.RETURNED:
                out = False
        return out

    def last_calibration(self, day: date | None = None, passed: bool | None = True) -> EquipmentEntry | None:
        return next(
            (
                e
                for e in reversed(self._until(day))
                if e.kind == EquipmentEntryKind.CALIBRATION and (passed is None or e.passed == passed)
            ),
            None,
        )

    def due_on(self, day: date | None = None) -> date | None:
        last = self.last_calibration(day)
        return None if last is None else last.next_due

    def state(self, day: date, due_within_days: int = 30) -> EquipmentState:
        if self.out_of_service(day):
            return EquipmentState.OUT_OF_SERVICE
        if not self.calibration_required:
            return EquipmentState.IN_SERVICE
        due = self.due_on(day)
        if due is None or day > due:
            return EquipmentState.OVERDUE
        if due - day <= timedelta(days=due_within_days):
            return EquipmentState.DUE
        return EquipmentState.IN_SERVICE

    def failure_window(self, index: int) -> tuple[date, date] | None:
        """For the failed calibration or check at `index`: from the last passing calibration before it to
        the day it failed, what a nonconformity's impact looks at (D-126). None when it never passed."""
        entry = self.entries[index]
        if not entry.failed:
            return None
        last = next(
            (
                e
                for e in reversed(self.entries[:index])
                if e.kind == EquipmentEntryKind.CALIBRATION and e.passed and e.on <= entry.on
            ),
            None,
        )
        return None if last is None else (last.on, entry.on)

    def check(self, entry: EquipmentEntry) -> None:
        """Refuses an entry the facts don't allow."""
        if entry.kind == EquipmentEntryKind.RETURNED:
            if not self.out_of_service():
                raise NotAllowed(f"{self.name} is in service")
            if self.calibration_required:
                last = self.last_calibration(passed=None)
                if last is None or not last.passed:
                    raise NotAllowed(f"calibrate {self.name} (and pass) before returning it to service")
        if entry.kind == EquipmentEntryKind.OUT_OF_SERVICE and self.out_of_service():
            raise NotAllowed(f"{self.name} is already out of service")
        if entry.kind == EquipmentEntryKind.CALIBRATION and not self.calibration_required:
            raise NotAllowed(f"{self.name} needs no calibration")


class UseProblem(Frozen):
    """Why a piece of equipment named on a prep batch or a run is warned about or refused (D-126)."""

    name: str
    effect: Effect
    message: str

    def __str__(self) -> str:
        return f"{self.name}: {self.message}"


def check_use(
    register: Mapping[str, Equipment], names: Iterable[str], day: date, policy: EquipmentPolicy
) -> list[UseProblem]:
    """What the equipment used on `day` says: a problem per item that warns or blocks; nothing for an item
    in service or only due (that shows on the register)."""
    problems = []
    for name in dict.fromkeys(names):
        item = register.get(name)
        if item is None:
            if policy.unregistered != Effect.RECORDED:
                problems.append(
                    UseProblem(name=name, effect=policy.unregistered, message="not in the register")
                )
            continue
        state = item.state(day, policy.due_within_days)
        if state == EquipmentState.OUT_OF_SERVICE:
            problems.append(UseProblem(name=name, effect=Effect.BLOCK, message="out of service"))
        elif state == EquipmentState.OVERDUE and policy.effect(item.kind) != Effect.RECORDED:
            due = item.due_on(day)
            message = (
                "never calibrated" if due is None else f"calibration overdue since {due + timedelta(days=1)}"
            )
            problems.append(UseProblem(name=name, effect=policy.effect(item.kind), message=message))
    return problems
