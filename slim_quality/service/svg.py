"""A chart as inline SVG, drawn on the server: no script, so it passes a strict CSP, and it prints.

Points by time; the centre line and ±1/2/3 SD (or the one-sided upper limit) per stretch of limits;
the acceptance window dashed; the forecast line; events as vertical marks. A point takes the colour
of its worst flag, and its tooltip (`<title>`) says what each flag found. Excluded points are hollow.
"""

from collections.abc import Sequence
from datetime import datetime, tzinfo
from xml.sax.saxutils import escape

from slim_quality.domain.enums import Measure, Severity
from slim_quality.domain.records import QualityEvent
from slim_quality.monitors.runner import Chart

WIDTH, HEIGHT = 960, 280
LEFT, RIGHT, TOP, BOTTOM = 64, 16, 16, 36
COLOURS = {None: "#4b6584", Severity.INFO: "#2d98da", Severity.WARN: "#f7b731", Severity.ACTION: "#eb3b5a"}
ONE_SIDED = {Measure.BLANK_LEVEL, Measure.CHECK_RSD, Measure.CAL_LOD, Measure.CAL_BEC}


def _value_text(chart: Chart, value) -> str:
    if chart.series.key.measure.is_recovery:
        return f"{value * 100:.1f} %"
    return f"{value:.4g} {chart.series.unit}".strip()


def render(chart: Chart, events: Sequence[QualityEvent] = (), tz: tzinfo | None = None) -> str:
    """`tz`: the lab's, so a point's date is the lab's day, not UTC's (review Q2)."""
    points = chart.series.points
    if not points:
        return '<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 960 40"><text x="8" y="24">No points yet</text></svg>'
    one_sided = chart.series.key.measure in ONE_SIDED
    levels = [p.value for p in points]
    for limits in {id(lim): lim for lim in chart.limits if lim is not None}.values():
        if one_sided:
            levels += [limits.center, limits.upper or limits.center]
        else:
            levels += [limits.center - 3 * limits.sd, limits.center + 3 * limits.sd]
    levels += [v for v in (chart.window.low, chart.window.high) if v is not None]
    low, high = float(min(levels)), float(max(levels))
    pad = (high - low) * 0.08 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    t0, t1 = points[0].at.timestamp(), points[-1].at.timestamp()
    span = (t1 - t0) or 1.0

    def x(at: datetime) -> float:
        return LEFT + (at.timestamp() - t0) / span * (WIDTH - LEFT - RIGHT)

    def y(value) -> float:
        return TOP + (high - float(value)) / (high - low) * (HEIGHT - TOP - BOTTOM)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 {WIDTH} {HEIGHT}" class="qc-chart">',
        f"<title>{escape(chart.series.key.label)}</title>",
        f'<rect x="{LEFT}" y="{TOP}" width="{WIDTH - LEFT - RIGHT}" height="{HEIGHT - TOP - BOTTOM}" fill="none" stroke="#d1d8e0"/>',
    ]
    # limits, one stretch at a time
    start = 0
    for i in range(1, len(points) + 1):
        if i < len(points) and chart.limits[i] is chart.limits[start]:
            continue
        limits = chart.limits[start]
        if limits is not None:
            x0, x1 = x(points[start].at), x(points[i - 1].at)
            lines = [(limits.center, "#20bf6b", "")]
            if one_sided:
                if limits.upper is not None:
                    lines.append((limits.upper, "#eb3b5a", ""))
            else:
                for k, colour in ((1, "#a5b1c2"), (2, "#f7b731"), (3, "#eb3b5a")):
                    lines += [
                        (limits.center + k * limits.sd, colour, ""),
                        (limits.center - k * limits.sd, colour, ""),
                    ]
            dash = ' stroke-dasharray="2 3"' if limits.provisional else ""
            for value, colour, _ in lines:
                out.append(
                    f'<line x1="{x0:.1f}" x2="{max(x1, x0 + 2):.1f}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="{colour}"{dash}/>'
                )
        start = i
    for value in (chart.window.low, chart.window.high):
        if value is not None:
            out.append(
                f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="#8854d0" stroke-dasharray="6 4"/>'
            )
            out.append(
                f'<text x="{WIDTH - RIGHT - 4}" y="{y(value) - 4:.1f}" text-anchor="end" font-size="11" fill="#8854d0">window {escape(_value_text(chart, value))}</text>'
            )
    for event in events:
        if t0 <= event.at.timestamp() <= t1:
            ex = x(event.at)
            out.append(
                f'<line x1="{ex:.1f}" x2="{ex:.1f}" y1="{TOP}" y2="{HEIGHT - BOTTOM}" stroke="#3867d6" stroke-dasharray="1 3"><title>{escape(f"{event.kind.label}: {event.note}")}</title></line>'
            )
    if chart.trend is not None:
        t = chart.trend
        out.append(
            f'<line x1="{x(t.start):.1f}" x2="{x(t.end):.1f}" y1="{y(t.start_value):.1f}" y2="{y(t.end_value):.1f}" stroke="#0fb9b1" stroke-width="2" stroke-dasharray="5 3"/>'
        )
    flags_at: dict[int, list] = {}
    for flag in chart.flags:
        flags_at.setdefault(flag.index, []).append(flag)
    for i, p in enumerate(points):
        found = flags_at.get(i, [])
        worst = max((f.severity for f in found), default=None)
        colour = COLOURS[worst]
        fill = "none" if p.excluded else colour
        lines = [
            f"{p.at.astimezone(tz):%Y-%m-%d %H:%M} · {p.run_code} · {_value_text(chart, p.value)}"
            + (" · excluded" if p.excluded else "")
        ]
        lines += [f"{f.severity.name} {f.code}: {f.message}" for f in found]
        out.append(
            f'<circle cx="{x(p.at):.1f}" cy="{y(p.value):.1f}" r="{4 if found else 2.5}" fill="{fill}" stroke="{colour}"><title>{escape(chr(10).join(lines))}</title></circle>'
        )
    for value in (high - pad, low + pad):
        out.append(
            f'<text x="{LEFT - 6}" y="{y(value) + 4:.1f}" text-anchor="end" font-size="11" fill="#4b6584">{escape(_value_text(chart, round(value, 6)))}</text>'
        )
    for at, anchor in ((points[0].at, "start"), (points[-1].at, "end")):
        out.append(
            f'<text x="{x(at):.1f}" y="{HEIGHT - 12}" text-anchor="{anchor}" font-size="11" fill="#4b6584">{at.astimezone(tz):%Y-%m-%d}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)
