"""The monitor contract: a tool reads one stretch of a series under one set of limits and says where
its pattern shows. Pure: no database, no clock. A tool is `name@version`; a changed tool is a new
version and the old one stays, so a past chart can be redrawn exactly (as slim-data's steps)."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, NamedTuple, Protocol

from slim_quality.domain.base import Frozen
from slim_quality.domain.enums import Severity
from slim_quality.domain.limits import Limits, Window
from slim_quality.domain.series import Point


@dataclass(frozen=True)
class Track:
    """Judged points (oldest first) that share one set of limits; `limits` None = none yet."""

    points: Sequence[Point]
    limits: Limits | None
    window: Window

    def z(self) -> list[Decimal]:
        assert self.limits is not None
        return [self.limits.z(p.value) for p in self.points]


class Hit(NamedTuple):
    code: str
    index: int  # into the track's points
    message: str


class Monitor(Protocol):
    name: ClassVar[str]
    version: ClassVar[int]
    title: ClassVar[str]
    codes: ClassVar[dict[str, Severity]]  # every code it can raise, with its default severity
    Params: ClassVar[type[Frozen]]

    def check(self, track: Track, params: Frozen) -> list[Hit]: ...


def key(monitor) -> str:
    return f"{monitor.name}@{monitor.version}"


def onset(condition: Sequence[bool]) -> list[int]:
    """The points where a condition starts to hold: a pattern is flagged once, not on every point
    that keeps it going."""
    return [i for i, held in enumerate(condition) if held and (i == 0 or not condition[i - 1])]


def fmt(value: Decimal, places: int = 2) -> str:
    return f"{value:.{places}f}"
