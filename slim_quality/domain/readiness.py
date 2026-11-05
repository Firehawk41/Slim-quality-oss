"""Readiness per clause (D-136): one row per ISO 17025 clause, the evidence the records already hold, and a
red / amber / green from the profile's rules. Read-only: worked out each time from `Facts`, which the
service gathers from slim-quality's records and what the host hands over (turnaround, the acts the
competence check reads, the installed modules for §7.11).

A clause's colour is the worst of its evidence. Every clause has one line about its last audit (an
internal audit of it, or a vertical audit for the clauses one covers, D-113); some have more.
"""

from datetime import date, timedelta
from decimal import Decimal
from enum import IntEnum

from pydantic import Field

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.clauses import CLAUSES, clause_order


class Rag(IntEnum):
    GREEN = 1
    AMBER = 2
    RED = 3

    @property
    def label(self) -> str:
        return self.name.lower()


class ReadinessPolicy(Frozen):
    """The profile's readiness rules (D-136), change-controlled like every threshold."""

    audit_every_days: int = Field(default=365, ge=1, le=1100)  # a clause audited longer ago is amber
    nc_open_days: int = Field(default=60, ge=1, le=730)  # an NC open longer is amber
    complaint_open_days: int = Field(default=30, ge=1, le=365)
    uncertainty_max_age_days: int = Field(default=365, ge=1, le=1100)
    pt_every_days: int = Field(default=365, ge=1, le=1100)  # no PT round scored within: amber
    review_every_days: int = Field(default=365, ge=1, le=1100)  # management review
    on_time_target: Dec = Field(default=Decimal(90), ge=0, le=100)  # % of requests reported on time


class Evidence(Frozen):
    rag: Rag
    text: str
    page: str | None = None  # which page shows it (the host maps the name to a URL)


class ClauseReadiness(Frozen):
    clause: str
    title: str
    evidence: tuple[Evidence, ...]

    @property
    def rag(self) -> Rag:
        return max((e.rag for e in self.evidence), default=Rag.GREEN)


class Facts(Frozen):
    """What the records say on `today`. A None means nobody handed it over (not checked)."""

    today: date
    last_audited: dict[str, date] = {}  # clause → the last day it was audited
    findings_without_nc: dict[str, int] = {}  # clause → internal-audit findings no NC was raised from
    audits_planned_this_year: int = 0
    audits_overdue: int = 0
    competence_findings: int | None = None
    equipment_overdue: int = 0  # overdue, never calibrated
    equipment_due: int = 0
    equipment_out: int = 0
    lots_expired_in_use: int = 0  # past their effective expiry and not withdrawn
    lots_expiring: int = 0  # within 30 days
    uncertainty_estimates: int = 0
    uncertainty_oldest: date | None = None
    pt_last_scored: date | None = None
    pt_unsatisfactory: int = 0  # scores in the last year
    pt_overdue: int = 0  # rounds past their due date and not yet submitted
    complaints_open: int = 0
    complaints_open_long: int = 0
    ncs_open: int = 0
    ncs_open_long: int = 0
    nc_actions_overdue: int = 0
    risks_open: int = 0
    risks_flagged: int = 0
    last_review: date | None = None
    validation: Rag | None = None  # GREEN: signed for what is installed; AMBER: for something else
    validation_signed: date | None = None
    on_time_percent: Decimal | None = None
    on_time_requests: int = 0


def _audit_line(facts: Facts, clause: str, policy: ReadinessPolicy) -> Evidence:
    last = facts.last_audited.get(clause)
    if last is None:
        return Evidence(rag=Rag.RED, text="never audited", page="internal_audits")
    if facts.today - last > timedelta(days=policy.audit_every_days):
        return Evidence(
            rag=Rag.AMBER,
            text=f"last audited {last}, more than {policy.audit_every_days} days ago",
            page="internal_audits",
        )
    return Evidence(rag=Rag.GREEN, text=f"audited {last}", page="internal_audits")


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _specific(facts: Facts, clause: str, policy: ReadinessPolicy) -> list[Evidence]:  # noqa: C901 (a table)
    f, found = facts, []

    def add(rag: Rag, text: str, page: str | None = None) -> None:
        found.append(Evidence(rag=rag, text=text, page=page))

    if clause == "6.2":
        if f.competence_findings is None:
            add(Rag.AMBER, "acts not checked against the authorisation register", "competence")
        elif f.competence_findings:
            add(Rag.AMBER, _count(f.competence_findings, "act") + " outside an authorisation", "competence")
        else:
            add(Rag.GREEN, "every recorded act was authorised", "competence")
    elif clause == "6.4":
        if f.equipment_overdue:
            add(Rag.RED, _count(f.equipment_overdue, "item") + " overdue or never calibrated", "equipment")
        if f.equipment_due:
            add(Rag.AMBER, _count(f.equipment_due, "item") + " due soon", "equipment")
        if f.equipment_out:
            add(Rag.GREEN, _count(f.equipment_out, "item") + " out of service, taken out", "equipment")
        if not (f.equipment_overdue or f.equipment_due):
            add(Rag.GREEN, "every registered item in date", "equipment")
    elif clause == "6.5":
        if f.lots_expired_in_use:
            add(
                Rag.AMBER,
                _count(f.lots_expired_in_use, "lot") + " past expiry and not withdrawn",
                "materials",
            )
        if f.lots_expiring:
            add(Rag.AMBER, _count(f.lots_expiring, "lot") + " expiring within 30 days", "materials")
        if not (f.lots_expired_in_use or f.lots_expiring):
            add(Rag.GREEN, "reference lots in date", "materials")
    elif clause == "7.6":
        if not f.uncertainty_estimates:
            add(Rag.RED, "no signed uncertainty estimate", "uncertainty")
        elif f.today - f.uncertainty_oldest > timedelta(days=policy.uncertainty_max_age_days):
            add(Rag.AMBER, f"the oldest estimate was signed {f.uncertainty_oldest}", "uncertainty")
        else:
            add(Rag.GREEN, _count(f.uncertainty_estimates, "signed estimate"), "uncertainty")
    elif clause == "7.7":
        if f.pt_last_scored is None:
            add(Rag.RED, "no PT round scored", "pt")
        elif f.today - f.pt_last_scored > timedelta(days=policy.pt_every_days):
            add(Rag.AMBER, f"last PT round scored {f.pt_last_scored}", "pt")
        else:
            add(Rag.GREEN, f"last PT round scored {f.pt_last_scored}", "pt")
        if f.pt_unsatisfactory:
            add(Rag.AMBER, _count(f.pt_unsatisfactory, "unsatisfactory score") + " in the last year", "pt")
        if f.pt_overdue:
            add(Rag.AMBER, _count(f.pt_overdue, "PT round") + " past due, not submitted", "pt")
    elif clause == "7.8" and f.on_time_percent is not None:
        rag = Rag.GREEN if f.on_time_percent >= policy.on_time_target else Rag.AMBER
        add(
            rag,
            f"{f.on_time_percent} % of {f.on_time_requests} reported on time "
            f"(target {policy.on_time_target} %)",
            "trends",
        )
    elif clause == "7.9":
        if f.complaints_open_long:
            add(
                Rag.AMBER,
                _count(f.complaints_open_long, "complaint") + f" open over {policy.complaint_open_days} days",
                "complaints",
            )
        add(Rag.GREEN, _count(f.complaints_open, "complaint") + " open", "complaints")
    elif clause in ("7.10", "8.7"):
        if f.nc_actions_overdue:
            add(Rag.RED, _count(f.nc_actions_overdue, "corrective action") + " past due", "ncs")
        if f.ncs_open_long:
            add(
                Rag.AMBER,
                _count(f.ncs_open_long, "nonconformity", "nonconformities")
                + f" open over {policy.nc_open_days} days",
                "ncs",
            )
        add(Rag.GREEN, _count(f.ncs_open, "nonconformity", "nonconformities") + " open", "ncs")
    elif clause == "7.11":
        if f.validation is None:
            add(Rag.RED, "the software validation pack was never signed", "validation")
        elif f.validation == Rag.AMBER:
            add(Rag.AMBER, f"last signed {f.validation_signed}, for other module versions", "validation")
        else:
            add(Rag.GREEN, f"signed {f.validation_signed} for the installed modules", "validation")
    elif clause == "8.5":
        if not f.risks_open:
            add(Rag.RED, "no risks or opportunities registered", "risks")
        elif f.risks_flagged:
            add(
                Rag.AMBER,
                _count(f.risks_flagged, "risk") + " flagged (high with no action, or review overdue)",
                "risks",
            )
        else:
            add(
                Rag.GREEN,
                _count(f.risks_open, "open risk or opportunity", "open risks and opportunities"),
                "risks",
            )
    elif clause == "8.8":
        if not f.audits_planned_this_year:
            add(Rag.RED, f"no audit planned for {f.today.year}", "internal_audits")
        if f.audits_overdue:
            add(Rag.AMBER, _count(f.audits_overdue, "planned audit") + " overdue", "internal_audits")
        if f.audits_planned_this_year and not f.audits_overdue:
            add(
                Rag.GREEN,
                _count(f.audits_planned_this_year, "audit") + f" in the {f.today.year} programme",
                "internal_audits",
            )
    elif clause == "8.9":
        if f.last_review is None:
            add(Rag.RED, "no management review signed", "management_review")
        elif f.today - f.last_review > timedelta(days=policy.review_every_days):
            add(Rag.AMBER, f"last management review {f.last_review}", "management_review")
        else:
            add(Rag.GREEN, f"last management review {f.last_review}", "management_review")
    return found


def readiness(facts: Facts, policy: ReadinessPolicy) -> tuple[ClauseReadiness, ...]:
    rows = []
    for clause in sorted(CLAUSES, key=clause_order):
        evidence = [_audit_line(facts, clause, policy)]
        if facts.findings_without_nc.get(clause):
            n = facts.findings_without_nc[clause]
            evidence.append(
                Evidence(
                    rag=Rag.AMBER,
                    text=_count(n, "audit finding") + " with no NC raised",
                    page="internal_audits",
                )
            )
        evidence += _specific(facts, clause, policy)
        rows.append(ClauseReadiness(clause=clause, title=CLAUSES[clause], evidence=tuple(evidence)))
    return tuple(rows)
