"""Who acts in slim-quality. The host decides who holds which role (a capability per person)."""

from pydantic import Field

from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import QualityRole


class QualityActor(Frozen):
    id: str = Field(min_length=1, max_length=100)
    display: str = Field(min_length=1, max_length=200)
    role: QualityRole

    @property
    def is_manager(self) -> bool:
        return self.role == QualityRole.MANAGER
