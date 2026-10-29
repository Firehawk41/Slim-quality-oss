"""What a monitor raises on a point of a series."""

from pydantic import AwareDatetime, Field

from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import Severity


class Flag(Frozen):
    tool: str  # "westgard@1"
    code: str  # "WG_4_1S"
    severity: Severity
    index: int = Field(ge=0)  # the point it fell on, in the series
    at: AwareDatetime
    message: str
