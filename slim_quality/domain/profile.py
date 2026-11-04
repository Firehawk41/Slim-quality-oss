"""The quality profile: which tools run on which measures, with what settings. Policy data the quality
manager edits (through a change request), never code. Every approved version is kept."""

from typing import Any, Self

from pydantic import Field, model_validator

from slim_quality.domain.base import Dec, Frozen
from slim_quality.domain.enums import LimitMethod, Measure, Severity
from slim_quality.domain.equipment import EquipmentPolicy
from slim_quality.domain.limits import Limits, Window
from slim_quality.domain.pt import PtPolicy
from slim_quality.domain.uncertainty import UncertaintyPolicy


class ToolSetting(Frozen):
    tool: str = Field(pattern=r"^[a-z_]+@\d+$")  # "westgard@1"
    enabled: bool = True
    measures: frozenset[Measure] = frozenset()
    params: dict[str, Any] = {}  # checked by the tool itself
    severities: dict[str, Severity] = {}  # flag code -> severity; a code left out takes the tool's default


class MeasurePolicy(Frozen):
    """How a measure's limits are set when nobody has approved any, and its acceptance window. A
    standardised measure (a z-score, D-129) has its limits fixed here instead: method certificate, with
    the centre and SD."""

    method: LimitMethod = LimitMethod.HISTORY
    baseline_points: int = Field(default=20, ge=3, le=500)
    window_low: Dec | None = None
    window_high: Dec | None = None
    center: Dec | None = None  # certificate only
    sd: Dec | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _fixed_or_baseline(self) -> Self:
        fixed = self.center is not None and self.sd is not None
        if (self.method == LimitMethod.CERTIFICATE) != fixed:
            raise ValueError("fixed limits (a centre and an SD) go with method certificate, and only with it")
        if (self.center is None) != (self.sd is None):
            raise ValueError("fixed limits give both a centre and an SD")
        Window(low=self.window_low, high=self.window_high)
        return self

    @property
    def fixed(self) -> Limits | None:
        if self.method != LimitMethod.CERTIFICATE:
            return None
        return Limits(
            method=LimitMethod.CERTIFICATE, center=self.center, sd=self.sd, basis="fixed by the profile"
        )

    @property
    def window(self) -> Window:
        return Window(low=self.window_low, high=self.window_high)


class Profile(Frozen):
    version: int = Field(default=0, ge=0)  # 0 = the starter profile, never approved
    tools: tuple[ToolSetting, ...] = ()
    measures: dict[Measure, MeasurePolicy] = {}
    equipment: EquipmentPolicy = EquipmentPolicy()  # D-126
    pt: PtPolicy = PtPolicy()  # D-128
    uncertainty: UncertaintyPolicy = UncertaintyPolicy()  # D-131

    @model_validator(mode="after")
    def _one_setting_per_tool(self) -> Self:
        names = [t.tool for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("a tool appears twice in the profile")
        return self

    def policy(self, measure: Measure) -> MeasurePolicy:
        return self.measures.get(measure, MeasurePolicy())

    def setting(self, tool: str) -> ToolSetting | None:
        return next((t for t in self.tools if t.tool == tool), None)


def differences(before: Profile, after: Profile) -> list[str]:
    """What a change does, line by line: tools switched, measures, settings, severities, policies."""
    lines = []
    for setting in after.tools:
        old = before.setting(setting.tool) or ToolSetting(tool=setting.tool, enabled=False)
        if old.enabled != setting.enabled:
            lines.append(f"{setting.tool}: {'on' if setting.enabled else 'off'}")
        for m in sorted(setting.measures - old.measures):
            lines.append(f"{setting.tool}: also on {m.label}")
        for m in sorted(old.measures - setting.measures):
            lines.append(f"{setting.tool}: no longer on {m.label}")
        for name in sorted(set(old.params) | set(setting.params)):
            a, b = old.params.get(name), setting.params.get(name)
            if _norm(a) != _norm(b):
                lines.append(f"{setting.tool}: {name} {_text(a)} → {_text(b)}")
        for code in sorted(set(old.severities) | set(setting.severities)):
            a, b = old.severities.get(code), setting.severities.get(code)
            if a != b:
                lines.append(
                    f"{setting.tool}: {code} {a.name if a else 'default'} → {b.name if b else 'default'}"
                )
    for measure in sorted(set(before.measures) | set(after.measures)):
        a, b = before.policy(measure), after.policy(measure)
        if a != b:
            lines.append(f"{measure.label}: {_policy(a)} → {_policy(b)}")
    for kind in sorted(set(before.equipment.overdue) | set(after.equipment.overdue)):
        a, b = before.equipment.effect(kind), after.equipment.effect(kind)
        if a != b:
            lines.append(f"{kind.label} overdue: {a.label} → {b.label}")
    for name, a, b in (
        ("Unregistered equipment", before.equipment.unregistered.label, after.equipment.unregistered.label),
        ("Equipment due within (days)", before.equipment.due_within_days, after.equipment.due_within_days),
        ("PT questionable above |z|", before.pt.questionable, after.pt.questionable),
        ("PT unsatisfactory from |z|", before.pt.unsatisfactory, after.pt.unsatisfactory),
        (
            "Uncertainty: check points at least",
            before.uncertainty.min_precision_points,
            after.uncertainty.min_precision_points,
        ),
        (
            "Uncertainty: CRM or PT results at least",
            before.uncertainty.min_bias_points,
            after.uncertainty.min_bias_points,
        ),
        ("Uncertainty: coverage factor k", before.uncertainty.k, after.uncertainty.k),
    ):
        if _norm(a) != _norm(b):
            lines.append(f"{name}: {a} → {b}")
    return lines


def _norm(value):
    if isinstance(value, list | tuple | set | frozenset):
        return sorted(str(v) for v in value)
    return None if value is None else str(value)


def _text(value) -> str:
    norm = _norm(value)
    return "—" if norm is None else ", ".join(norm) if isinstance(norm, list) else norm


def _policy(p: MeasurePolicy) -> str:
    if p.fixed is not None:
        return f"fixed at {p.center} ± {p.sd} SD"
    low = "…" if p.window_low is None else p.window_low
    high = "…" if p.window_high is None else p.window_high
    window = f"{low}–{high}"
    return f"{p.method.name.lower()} from {p.baseline_points} points, window {window}"
