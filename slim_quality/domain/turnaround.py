"""Turnaround and customer trends, after the fact (D-133, ISO 17025 §8.9 inputs: the volume and type of
work, customer feedback, complaints).

The host hands over each request's dates; the promise (its due day) is the host's, worked out as it
always has been: working days Mon–Fri from receipt, the slowest sample's processing time, no holiday
calendar, a hold doesn't stop the clock, a rush counts from receipt like the rest. A crude clock, and
labelled so.

    on time  the complete report on or before the due day
    late     after it, by working days
    open     not complete yet; "open, late" once today is past the due day
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from typing import Self

from pydantic import Field, model_validator

from slim_quality.domain.base import Frozen


def working_days_between(start: date, end: date) -> int:
    """Mon–Fri days after `start` up to and including `end` (0 when end ≤ start)."""
    if end <= start:
        return 0
    whole_weeks, extra = divmod((end - start).days, 7)
    count = whole_weeks * 5
    day = start + timedelta(days=whole_weeks * 7)
    for _ in range(extra):
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


class TurnaroundState(IntEnum):
    ON_TIME = 1
    LATE = 2
    OPEN = 3
    OPEN_LATE = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", ", ").capitalize()


class RequestDates(Frozen):
    """One request as the host knows it: received, promised, reported."""

    tr_number: str = Field(min_length=1, max_length=20)
    customer_id: str = Field(min_length=1, max_length=50)
    customer: str = Field(min_length=1, max_length=200)
    analyses: tuple[str, ...] = ()  # analysis codes
    samples: int = Field(ge=0)
    rush: bool = False
    received: date
    due: date
    first_report: date | None = None
    complete_report: date | None = None

    @model_validator(mode="after")
    def _order(self) -> Self:
        for found in (self.first_report, self.complete_report):
            if found is not None and found < self.received:
                raise ValueError(f"{self.tr_number}: a report before receipt")
        return self

    def state(self, today: date) -> TurnaroundState:
        if self.complete_report is not None:
            return TurnaroundState.ON_TIME if self.complete_report <= self.due else TurnaroundState.LATE
        return TurnaroundState.OPEN_LATE if today > self.due else TurnaroundState.OPEN

    def days_late(self, today: date) -> int:
        return working_days_between(self.due, self.complete_report or today)

    @property
    def days_taken(self) -> int | None:
        if self.complete_report is None:
            return None
        return working_days_between(self.received, self.complete_report)

    @property
    def month(self) -> str:
        return self.received.strftime("%Y-%m")


class Tally(Frozen):
    label: str
    requests: int
    samples: int
    on_time: int
    late: int
    open: int
    open_late: int
    mean_days: Decimal | None  # receipt to complete report, working days, of the complete ones
    worst_late: int  # working days

    @property
    def complete(self) -> int:
        return self.on_time + self.late

    @property
    def on_time_percent(self) -> Decimal | None:
        if not self.complete:
            return None
        return (Decimal(100) * self.on_time / self.complete).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def tally(label: str, requests: Iterable[RequestDates], today: date) -> Tally:
    requests = tuple(requests)
    states = [r.state(today) for r in requests]
    taken = [r.days_taken for r in requests if r.days_taken is not None]
    late = [r.days_late(today) for r, s in zip(requests, states, strict=True) if s.name.endswith("LATE")]
    mean = None
    if taken:
        mean = (Decimal(sum(taken)) / len(taken)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return Tally(
        label=label,
        requests=len(requests),
        samples=sum(r.samples for r in requests),
        on_time=states.count(TurnaroundState.ON_TIME),
        late=states.count(TurnaroundState.LATE),
        open=states.count(TurnaroundState.OPEN),
        open_late=states.count(TurnaroundState.OPEN_LATE),
        mean_days=mean,
        worst_late=max(late, default=0),
    )


def grouped(
    requests: Iterable[RequestDates], keys: Callable[[RequestDates], Iterable[str]], today: date
) -> tuple[Tally, ...]:
    """A tally per key; a request with several keys (analyses) counts under each."""
    groups: dict[str, list[RequestDates]] = defaultdict(list)
    for r in requests:
        for k in keys(r):
            groups[k].append(r)
    return tuple(tally(k, found, today) for k, found in sorted(groups.items()))


class TurnaroundReport(Frozen):
    since: date | None
    today: date
    overall: Tally
    by_month: tuple[Tally, ...]
    by_customer: tuple[Tally, ...]
    by_analysis: tuple[Tally, ...]
    open_late: tuple[RequestDates, ...]  # most late first


def turnaround(requests: Iterable[RequestDates], today: date, since: date | None = None) -> TurnaroundReport:
    found = tuple(r for r in requests if since is None or r.received >= since)
    return TurnaroundReport(
        since=since,
        today=today,
        overall=tally("All requests", found, today),
        by_month=grouped(found, lambda r: (r.month,), today),
        by_customer=grouped(found, lambda r: (r.customer,), today),
        by_analysis=grouped(found, lambda r: r.analyses or ("(none)",), today),
        open_late=tuple(
            sorted(
                (r for r in found if r.state(today) == TurnaroundState.OPEN_LATE),
                key=lambda r: (-r.days_late(today), r.tr_number),
            )
        ),
    )


class CustomerTrend(Frozen):
    customer_id: str
    customer: str
    months: tuple[tuple[str, int, int], ...]  # (month, requests, samples), oldest first
    requests: int
    samples: int
    on_time_percent: Decimal | None
    complaints: int
    open_complaints: int

    @property
    def change(self) -> int | None:
        """Requests in the latest month against the one before (None with fewer than two months)."""
        if len(self.months) < 2:
            return None
        return self.months[-1][1] - self.months[-2][1]


def customer_trends(
    requests: Iterable[RequestDates],
    today: date,
    complaints: dict[str, tuple[int, int]] | None = None,
    since: date | None = None,
) -> tuple[CustomerTrend, ...]:
    """Per customer: requests and samples per month of receipt, on-time %, complaints (all, open), from
    `complaints` keyed by customer id. Busiest first."""
    complaints = complaints or {}
    per: dict[str, list[RequestDates]] = defaultdict(list)
    for r in requests:
        if since is None or r.received >= since:
            per[r.customer_id].append(r)
    found = []
    for customer_id, rs in per.items():
        months: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rs:
            months[r.month][0] += 1
            months[r.month][1] += r.samples
        t = tally(rs[0].customer, rs, today)
        total, still_open = complaints.get(customer_id, (0, 0))
        found.append(
            CustomerTrend(
                customer_id=customer_id,
                customer=rs[0].customer,
                months=tuple((m, n, s) for m, (n, s) in sorted(months.items())),
                requests=t.requests,
                samples=t.samples,
                on_time_percent=t.on_time_percent,
                complaints=total,
                open_complaints=still_open,
            )
        )
    for customer_id, (total, still_open) in complaints.items():
        if customer_id not in per and total:
            found.append(
                CustomerTrend(
                    customer_id=customer_id, customer=customer_id, months=(), requests=0, samples=0,
                    on_time_percent=None, complaints=total, open_complaints=still_open,
                )
            )  # fmt: skip
    return tuple(sorted(found, key=lambda c: (-c.requests, c.customer)))
