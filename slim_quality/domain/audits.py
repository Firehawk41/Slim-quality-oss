"""A vertical audit, recorded (D-113): who audited what, and what they found. The audit itself is read
from the modules by the host; this is the record that it was done (ISO 17025 §8.8)."""

from enum import IntEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen


class AuditOutcome(IntEnum):
    CONFORMING = 1
    FINDINGS = 2


class AuditRecord(Frozen):
    id: int | None = None
    subject: str = Field(min_length=1, max_length=200)  # e.g. "Report 12 (TR00008)"
    subject_ref: str = Field(min_length=1, max_length=100)  # the host's id for it, e.g. "issue:12"
    outcome: AuditOutcome
    findings: str = Field(default="", max_length=4000)
    by: QualityActor
    at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _findings_written(self) -> Self:
        if self.outcome == AuditOutcome.FINDINGS and not self.findings.strip():
            raise ValueError("write the findings")
        return self
