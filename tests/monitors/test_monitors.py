"""Each tool on hand-made z values (centre 0, SD 1): the pattern, its onset, and silence without limits."""

from datetime import timedelta
from decimal import Decimal

import pytest

from slim_quality.domain.limits import Window
from slim_quality.monitors.base import onset
from slim_quality.monitors.cusum import Cusum, CusumParams
from slim_quality.monitors.ewma import Ewma, EwmaParams
from slim_quality.monitors.exceedance import Exceedance, ExceedanceParams
from slim_quality.monitors.projection import Projection, ProjectionParams, fit
from slim_quality.monitors.westgard import Westgard, WestgardParams
from tests.factories import UNIT, points, track

WG = Westgard()


def codes(hits):
    return [(h.code, h.index) for h in hits]


def westgard(*values, rules=None, n=10):
    params = WestgardParams(n=n) if rules is None else WestgardParams(rules=frozenset(rules), n=n)
    return codes(WG.check(track(*values), params))


def test_one_point_rules():
    assert westgard(0, 2.5, -3.5, rules={"1_2s", "1_3s"}) == [("WG_1_2S", 1), ("WG_1_3S", 2)]
    assert westgard(0, 3.5, rules={"1_2s"}) == [("WG_1_2S", 1)]  # 1_3s off: a 3.5 is still beyond 2


def test_two_two_s_and_range_rules():
    assert westgard(2.1, 2.2, 2.3, rules={"2_2s"}) == [("WG_2_2S", 1)]  # flagged once, where it starts
    assert westgard(-2.1, -2.2, rules={"2_2s"}) == [("WG_2_2S", 1)]
    assert westgard(2.1, -2.1, 2.1, rules={"R_4s"}) == [("WG_R_4S", 1), ("WG_R_4S", 2)]


def test_runs_four_one_s_and_nx():
    assert westgard(1.1, 1.2, 1.3, 1.4, 0, rules={"4_1s"}) == [("WG_4_1S", 3)]
    assert westgard(*[-0.1] * 8, rules={"nx"}, n=8) == [("WG_NX", 7)]
    assert westgard(*[0.1] * 9, rules={"nx"}) == []


def test_no_limits_no_westgard_ewma_or_cusum():
    silent = track(5, 5, 5, limits=None)
    assert WG.check(silent, WestgardParams()) == []
    assert Ewma().check(silent, EwmaParams()) == []
    assert Cusum().check(silent, CusumParams()) == []


def test_ewma_catches_a_small_sustained_shift_single_points_miss():
    values = [0] * 5 + [1.2] * 10
    assert westgard(*values, rules={"1_2s", "1_3s"}) == []
    hits = Ewma().check(track(*values), EwmaParams())
    assert [h.code for h in hits] == ["EWMA_HIGH"] and "above" in hits[0].message
    low = Ewma().check(track(*[-v for v in values]), EwmaParams())
    assert [h.code for h in low] == ["EWMA_LOW"]


def test_cusum_flags_and_restarts():
    hits = Cusum().check(track(*[1.5] * 12), CusumParams())
    assert [(h.code, h.index) for h in hits] == [("CUSUM_HIGH", 5), ("CUSUM_HIGH", 11)]
    assert [h.code for h in Cusum().check(track(*[-1.5] * 6), CusumParams())] == ["CUSUM_LOW"]


def test_exceedance_uses_the_upper_limit_then_the_window():
    limits = UNIT.model_copy(update={"upper": Decimal(3)})
    hits = Exceedance().check(track(1, 4, 5, 6, 1, limits=limits), ExceedanceParams(last=5, count=3))
    assert codes(hits) == [("ABOVE_LIMIT", 1), ("ABOVE_LIMIT", 2), ("ABOVE_LIMIT", 3), ("EXCEEDANCE_RATE", 3)]
    windowed = Exceedance().check(
        track(1, 9, limits=None, window=Window(high=Decimal(5))), ExceedanceParams()
    )
    assert codes(windowed) == [("ABOVE_LIMIT", 1)]
    assert Exceedance().check(track(1, 9, limits=None), ExceedanceParams()) == []


def test_projection_forecasts_the_crossing():
    window = Window(low=Decimal("0.75"), high=Decimal("1.25"))
    rising = [1 + 0.01 * i for i in range(12)]  # +1 % a day: 1.11 today, 1.25 in 14 days
    hits = Projection().check(
        track(*rising, window=window), ProjectionParams(last=10, horizon_days=Decimal(14))
    )
    assert codes(hits) == [("LIMIT_AHEAD", 11)] and "1.25 in about 14 days" in hits[0].message
    falling = Projection().check(track(*[1 - 0.02 * i for i in range(12)], window=window), ProjectionParams())
    assert codes(falling) == [("LIMIT_AHEAD", 9)]
    assert Projection().check(track(*rising), ProjectionParams()) == []  # no window: nothing to forecast


def test_fit_edge_cases():
    window = Window(high=Decimal(2))
    assert fit(points(1, 1), window) is None
    assert fit(points(1, 2, 3, step=timedelta(0)), window) is None  # no time passed: no slope
    trend = fit(points(1, 1.5, 3), window)
    assert trend.days_to_limit is None and trend.limit is None  # already past the window
    flat = fit(points(1, 1, 1), window)
    assert flat.slope_per_day == 0 and flat.days_to_limit is None


@pytest.mark.parametrize("held, starts", [([True, True, False, True], [0, 3]), ([False], [])])
def test_onset(held, starts):
    assert onset(held) == starts
