"""What slim-quality needs from the host: its QC data as series. Modules never import each other; the
host adapts slim-data's QC history to this port."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from slim_quality.domain.series import Series


class QcSource(Protocol):
    def series(self, since: datetime | None = None, as_of: datetime | None = None) -> Sequence[Series]: ...
