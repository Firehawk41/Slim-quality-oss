"""The runner: limits per point (provisional, then approved sets), tracks, severities, group tools."""

from datetime import timedelta
from decimal import Decimal

import pytest

from slim_quality.domain.enums import LimitMethod, Measure, Severity
from slim_quality.domain.limits import Limits, LimitSet
from slim_quality.domain.profile import MeasurePolicy, Profile, ToolSetting
from slim_quality.monitors.runner import ProfileError, check_profile, evaluate
from tests.factories import T0, key, series

CHECK = frozenset({Measure.CHECK_RECOVERY})
POLICY = {
    Measure.CHECK_RECOVERY: MeasurePolicy(
        baseline_points=4, window_low=Decimal("0.75"), window_high=Decimal("1.25")
    )
}


def profile(*tools, measures=POLICY):
    return Profile(tools=tools, measures=measures)


def westgard(**kw):
    return ToolSetting(tool="westgard@1", measures=CHECK, params={"rules": ["1_3s"]}, **kw)


def test_provisional_limits_from_the_first_points_then_rules():
    s = series(0.98, 1.0, 1.02, 1.0, 1.0, 1.3)
    (chart,) = evaluate([s], profile(westgard()))
    assert chart.current.provisional and chart.current.n == 4 and "first 4 points" in chart.current.basis
    assert [(f.code, f.index, f.severity) for f in chart.flags] == [("WG_1_3S", 5, Severity.ACTION)]
    assert chart.window.high == Decimal("1.25") and chart.trend is None


def test_too_few_points_or_no_spread_means_no_limits_yet():
    (short,) = evaluate([series(1, 2, 3)], profile(westgard()))
    (flat,) = evaluate([series(1, 1, 1, 1, 9)], profile(westgard()))
    assert short.current is None and flat.current is None and flat.flags == ()


def test_an_approved_set_starts_a_new_track_and_takes_over():
    s = series(1, 1, 1, 1, 1.1, 1.1)
    limits = Limits(method=LimitMethod.CERTIFICATE, center=Decimal("1.1"), sd=Decimal("0.01"))
    approved = LimitSet(series=s.key, limits=limits, effective_from=T0 + timedelta(days=4))
    (chart,) = evaluate([s], profile(westgard()), [approved])
    assert chart.limits[3] is None and chart.limits[4] == limits  # before: no provisional (only 4 points)
    assert chart.flags == ()


def test_excluded_points_are_drawn_never_judged_and_severity_comes_from_the_profile():
    s = series(1, 1.01, 0.99, 1.0, 5, excluded=(4,))
    (chart,) = evaluate([s], profile(westgard()))
    assert chart.flags == ()
    s = series(0.98, 1.0, 1.02, 1.0, 1.3)
    (chart,) = evaluate([s], profile(westgard(severities={"WG_1_3S": Severity.INFO})))
    assert chart.flags[0].severity == Severity.INFO


def test_tools_off_or_on_other_measures_stay_silent():
    s = series(0.98, 1.0, 1.02, 1.0, 1.3)
    assert evaluate([s], profile(westgard(enabled=False)))[0].flags == ()
    other = ToolSetting(tool="westgard@1", measures=frozenset({Measure.BLANK_LEVEL}))
    assert evaluate([s], profile(other))[0].flags == ()


def test_projection_draws_its_trend():
    tool = ToolSetting(tool="projection@1", measures=CHECK, params={"last": 3})
    (chart,) = evaluate([series(1.0, 1.01, 1.02, 1.03)], profile(tool))
    assert chart.trend.days_to_limit == Decimal("22") and chart.trend.limit == Decimal("1.25")
    (short,) = evaluate([series(1.0, 1.01)], profile(tool))
    assert short.trend is None


def test_a_shared_shift_across_instruments():
    tool = ToolSetting(tool="shared_shift@1", measures=CHECK, params={"days": 2, "threshold": "1.5"})
    base = (0.98, 1.0, 1.02, 1.0)
    one = series(*base, 1.06, 1.06, k=key("ICP-1"))
    two = series(*base, 1.06, 1.07, k=key("ICP-2"))
    three = series(*base, 1.0, 1.0, k=key("ICP-3"))
    lone = series(*base, 1.06, 1.06, k=key("ICP-1", analyte="Zn"))
    charts = evaluate([one, two, three, lone], profile(tool))
    flagged = {
        c.series.key.instrument + c.series.key.analyte: [(f.code, f.index) for f in c.flags] for c in charts
    }
    assert flagged == {
        "ICP-1Cu": [("SHARED_SHIFT", 5)],
        "ICP-2Cu": [("SHARED_SHIFT", 5)],
        "ICP-3Cu": [],
        "ICP-1Zn": [],
    }
    assert "ICP-1, ICP-2" in charts[0].flags[0].message
    off = tool.model_copy(update={"enabled": False})
    assert all(c.flags == () for c in evaluate([one, two], profile(off)))


@pytest.mark.parametrize(
    "setting, message",
    [
        (ToolSetting(tool="nope@1"), "no tool nope@1"),
        (ToolSetting(tool="ewma@1", params={"weight": "2"}), "ewma@1"),
        (ToolSetting(tool="ewma@1", severities={"WG_1_3S": Severity.INFO}), "raises no WG_1_3S"),
    ],
)
def test_check_profile_refuses(setting, message):
    with pytest.raises(ProfileError, match=message):
        check_profile(Profile(tools=(setting,)))


def test_empty_input():
    assert evaluate([], Profile()) == ()
