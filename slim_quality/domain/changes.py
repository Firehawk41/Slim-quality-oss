"""Changes to the rules go through a request: a quality user or manager proposes, a manager decides.

A manager may approve their own proposal (a lab may have one quality manager); the record says so,
so an assessor sees every self-approval.
"""

from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import ChangeKind, ChangeStatus
from slim_quality.domain.limits import LimitSet
from slim_quality.domain.profile import Profile


class NotAllowed(PermissionError):
    pass


class Decision(Frozen):
    approved: bool
    note: str = Field(default="", max_length=1000)
    by: QualityActor
    at: AwareDatetime | None = None


class ChangeRequest(Frozen):
    id: int | None = None
    kind: ChangeKind
    reason: str = Field(min_length=1, max_length=1000)
    proposed_by: QualityActor
    proposed_at: AwareDatetime | None = None
    profile: Profile | None = None
    limit_set: LimitSet | None = None
    decision: Decision | None = None

    @model_validator(mode="after")
    def _payload(self) -> Self:
        if (self.kind == ChangeKind.PROFILE) != (self.profile is not None):
            raise ValueError("a profile change carries a profile, and only that")
        if (self.kind == ChangeKind.LIMITS) != (self.limit_set is not None):
            raise ValueError("a limits change carries a limit set, and only that")
        return self

    @property
    def status(self) -> ChangeStatus:
        if self.decision is None:
            return ChangeStatus.PROPOSED
        return ChangeStatus.APPROVED if self.decision.approved else ChangeStatus.REJECTED

    @property
    def self_approved(self) -> bool:
        return (
            self.decision is not None
            and self.decision.approved
            and self.decision.by.id == self.proposed_by.id
        )

    def decide(self, actor: QualityActor, approved: bool, note: str = "") -> Decision:
        if not actor.is_manager:
            raise NotAllowed("only a quality manager decides a change")
        if self.decision is not None:
            raise NotAllowed("this change was already decided")
        if not approved and not note.strip():
            raise NotAllowed("say why it is rejected")
        return Decision(approved=approved, note=note, by=actor)
