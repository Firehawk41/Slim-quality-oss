from datetime import UTC, datetime

from slim_quality.demo.history import DemoSource
from slim_quality.domain.enums import EventKind, Measure
from slim_quality.domain.records import QualityEvent
from slim_quality.domain.series import Series
from slim_quality.monitors.runner import evaluate
from slim_quality.service.quality import STARTER
from slim_quality.service.svg import render
from tests.factories import USER, key

END = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
CHARTS = evaluate(DemoSource(END).series(), STARTER)


def test_a_two_sided_chart_with_window_trend_flags_and_events():
    chart = next(c for c in CHARTS if c.series.key.measure == Measure.ISTD_RECOVERY and c.flags)
    event = QualityEvent(
        kind=EventKind.MAINTENANCE,
        instrument="ICP-MS 1",
        at=chart.series.points[50].at,
        note="<cones>",
        by=USER,
    )
    svg = render(chart, [event])
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "<script" not in svg
    assert "window 70" in svg and "Maintenance: &lt;cones&gt;" in svg and "#eb3b5a" in svg
    assert 'stroke-dasharray="2 3"' in svg  # provisional limits are dotted


def test_a_one_sided_chart_and_an_empty_one():
    chart = next(c for c in CHARTS if c.series.key.measure == Measure.BLANK_LEVEL)
    assert "ppb" in render(chart)
    empty = evaluate([Series(key=key(), points=())], STARTER)[0]
    assert "No points yet" in render(empty)
