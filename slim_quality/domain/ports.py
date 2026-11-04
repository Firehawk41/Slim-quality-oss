"""What slim-quality needs from the host: its QC data as series, and each request's dates (D-133). Modules
never import each other; the host adapts slim-data's QC history and its own TR tables to these ports."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from slim_quality.domain.series import Series
from slim_quality.domain.turnaround import RequestDates


class QcSource(Protocol):
    def series(self, since: datetime | None = None, as_of: datetime | None = None) -> Sequence[Series]: ...


class TurnaroundSource(Protocol):
    def requests(self, since: date | None = None) -> Sequence[RequestDates]: ...
