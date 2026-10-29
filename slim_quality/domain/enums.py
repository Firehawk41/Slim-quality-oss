"""Enums, stored as ints (portable schema). Values are never renumbered or reused."""

from enum import IntEnum


class Measure(IntEnum):
    """What a series plots. Recoveries are fractions (1 = 100 %); the rest are in the reading's unit."""

    CHECK_RECOVERY = 1  # a check standard (CCV): measured ÷ expected
    CRM_RECOVERY = 2  # a certified reference material: measured ÷ certified
    BLANK_LEVEL = 3  # a method blank's reading
    ISTD_RECOVERY = 4  # the internal standard's recovery on QC rows, %
    CHECK_RSD = 5  # a check standard's replicate RSD, %
    CAL_LOD = 6  # the run's calibration: detection limit
    CAL_BEC = 7  # background equivalent concentration
    CAL_R = 8  # correlation coefficient

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def is_recovery(self) -> bool:
        return self in (Measure.CHECK_RECOVERY, Measure.CRM_RECOVERY)


_LABELS = {
    Measure.CHECK_RECOVERY: "Check standard recovery",
    Measure.CRM_RECOVERY: "CRM recovery",
    Measure.BLANK_LEVEL: "Method blank",
    Measure.ISTD_RECOVERY: "Internal standard recovery",
    Measure.CHECK_RSD: "Check standard RSD",
    Measure.CAL_LOD: "Calibration LOD",
    Measure.CAL_BEC: "Calibration BEC",
    Measure.CAL_R: "Calibration r",
}


class Severity(IntEnum):
    """How much a flag asks of the quality team; the profile sets it per flag code."""

    INFO = 1  # shown, nothing asked
    WARN = 2  # look at it
    ACTION = 3  # act: investigate, maintain, recalibrate


class LimitMethod(IntEnum):
    """Where a series' centre line and spread come from."""

    HISTORY = 1  # mean and SD of a baseline
    ROBUST = 2  # median and 1.4826 × MAD of a baseline: a few contaminated points don't widen it
    CERTIFICATE = 3  # entered: a certified value and its SD, or limits the lab sets


class QualityRole(IntEnum):
    USER = 1  # sees everything, records events and notes, proposes changes
    MANAGER = 2  # also approves changes and signs QC reviews


class ChangeKind(IntEnum):
    PROFILE = 1
    LIMITS = 2


class ChangeStatus(IntEnum):
    PROPOSED = 1
    APPROVED = 2
    REJECTED = 3


class EventKind(IntEnum):
    MAINTENANCE = 1  # cones cleaned, torch, pump tubing...
    NEW_LOT = 2  # a new lot of check standard or CRM
    REPAIR = 3
    OTHER = 9

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()
