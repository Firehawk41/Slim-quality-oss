"""Runs the profile over the series: limits per point, then each enabled tool on its measures.

Each point is judged under the limits in force when it was measured: the newest approved set from
before it, or, before any set, provisional limits from the series' first points (the measure's
policy: method and how many). A new set starts a new track, so a rule's run never spans two
baselines. Excluded points are drawn but never judged. Pure: same input, same charts.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from slim_quality.domain.flags import Flag
from slim_quality.domain.limits import Limits, LimitSet, Window, baseline
from slim_quality.domain.profile import Profile
from slim_quality.domain.series import Point, Series
from slim_quality.monitors.base import Track
from slim_quality.monitors.projection import ProjectionParams, Trend, fit
from slim_quality.monitors.registry import GROUP_TOOLS, SERIES_TOOLS, TOOLS


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Chart:
    series: Series
    limits: tuple[Limits | None, ...]  # per point
    window: Window
    flags: tuple[Flag, ...]
    trend: Trend | None  # the projection's line through the newest points, when the tool is on

    @property
    def current(self) -> Limits | None:
        return self.limits[-1] if self.limits else None


def check_profile(profile: Profile) -> None:
    """Refuses unknown tools, bad settings and severities for codes a tool doesn't raise."""
    for setting in profile.tools:
        tool = TOOLS.get(setting.tool)
        if tool is None:
            raise ProfileError(f"no tool {setting.tool}")
        try:
            tool.Params.model_validate(setting.params)
        except ValidationError as error:
            raise ProfileError(f"{setting.tool}: {error.errors()[0]['msg']}") from None
        unknown = set(setting.severities) - set(tool.codes)
        if unknown:
            raise ProfileError(f"{setting.tool} raises no {', '.join(sorted(unknown))}")


def evaluate(
    series: Sequence[Series], profile: Profile, limit_sets: Sequence[LimitSet] = ()
) -> tuple[Chart, ...]:
    check_profile(profile)
    by_series: dict[str, list[LimitSet]] = {}
    for item in sorted(limit_sets, key=lambda s: s.effective_from):
        by_series.setdefault(item.series.slug, []).append(item)
    built = []
    for s in series:
        limits = _limits_per_point(s, profile, by_series.get(s.key.slug, []))
        tracks = _tracks(s, limits, profile.policy(s.key.measure).window)
        flags = []
        for setting in profile.tools:
            tool = SERIES_TOOLS.get(setting.tool)
            if tool is None or not setting.enabled or s.key.measure not in setting.measures:
                continue
            params = tool.Params.model_validate(setting.params)
            for track, indices in tracks:
                for hit in tool.check(track, params):
                    index = indices[hit.index]
                    severity = setting.severities.get(hit.code, tool.codes[hit.code])
                    at = s.points[index].at
                    flags.append(
                        Flag(
                            tool=setting.tool,
                            code=hit.code,
                            severity=severity,
                            index=index,
                            at=at,
                            message=hit.message,
                        )
                    )
        built.append([s, limits, tracks, flags])
    for setting in profile.tools:
        tool = GROUP_TOOLS.get(setting.tool)
        if tool is None or not setting.enabled:
            continue
        params = tool.Params.model_validate(setting.params)
        latest = {}
        for entry in built:
            s, _, tracks, _ = entry
            if s.key.measure in setting.measures and tracks:
                latest[s.key.slug] = (entry, tracks[-1])
        found = tool.check_all([(e[0].key, t[0]) for e, t in latest.values()], params)
        for key, index, message in found:
            entry, (_, indices) = latest[key.slug]
            point_index = indices[index]
            severity = setting.severities.get("SHARED_SHIFT", tool.codes["SHARED_SHIFT"])
            at = entry[0].points[point_index].at
            entry[3].append(
                Flag(
                    tool=setting.tool,
                    code="SHARED_SHIFT",
                    severity=severity,
                    index=point_index,
                    at=at,
                    message=message,
                )
            )
    charts = []
    for s, limits, tracks, flags in built:
        window = profile.policy(s.key.measure).window
        charts.append(
            Chart(
                series=s,
                limits=tuple(limits),
                window=window,
                flags=tuple(sorted(flags, key=lambda f: (f.index, f.tool, f.code))),
                trend=_trend(s, tracks, profile, window),
            )
        )
    return tuple(charts)


def _limits_per_point(s: Series, profile: Profile, sets: list[LimitSet]) -> list[Limits | None]:
    policy = profile.policy(s.key.measure)
    first_set = sets[0].effective_from if sets else None
    early = [p for p in s.judged if first_set is None or p.at < first_set]
    provisional = None
    if len(early) >= policy.baseline_points:
        window = early[: policy.baseline_points]
        span = f"{window[0].at:%Y-%m-%d} to {window[-1].at:%Y-%m-%d}"
        basis = f"provisional: the first {len(window)} points, {span}"
        try:
            provisional = baseline([p.value for p in window], policy.method, basis=basis)
            provisional = provisional.model_copy(update={"provisional": True})
        except ValueError:
            provisional = None
    found = []
    for p in s.points:
        current = None
        for item in sets:
            if item.effective_from <= p.at:
                current = item.limits
        found.append(current if current is not None else provisional)
    return found


def _tracks(s: Series, limits: list[Limits | None], window: Window) -> list[tuple[Track, list[int]]]:
    """Judged points split where the limits change; with each, the points' indices in the series."""
    tracks: list[tuple[list[Point], list[int], Limits | None]] = []
    for index, (point, current) in enumerate(zip(s.points, limits, strict=True)):
        if point.excluded:
            continue
        if not tracks or tracks[-1][2] is not current:
            tracks.append(([], [], current))
        tracks[-1][0].append(point)
        tracks[-1][1].append(index)
    return [(Track(points=tuple(p), limits=lim, window=window), idx) for p, idx, lim in tracks]


def _trend(s: Series, tracks, profile: Profile, window: Window) -> Trend | None:
    setting = next((t for t in profile.tools if t.tool.startswith("projection@")), None)
    if setting is None or not setting.enabled or s.key.measure not in setting.measures or not tracks:
        return None
    params = ProjectionParams.model_validate(setting.params)
    points = list(tracks[-1][0].points)[-params.last :]
    return fit(points, window) if len(points) >= params.last else None
