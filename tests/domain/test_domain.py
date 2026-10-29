from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from slim_quality.domain.changes import ChangeRequest, NotAllowed
from slim_quality.domain.enums import ChangeKind, ChangeStatus, EventKind, LimitMethod, Measure
from slim_quality.domain.limits import Limits, LimitSet, Window, baseline
from slim_quality.domain.profile import MeasurePolicy, Profile, ToolSetting
from slim_quality.domain.records import QcReview
from slim_quality.domain.series import SeriesKey
from tests.factories import MANAGER, OTHER_MANAGER, T0, USER, key, series

D = Decimal


def test_mean_and_sd_baseline_with_a_three_sd_upper_limit():
    limits = baseline([D("0.98"), D("1.00"), D("1.02")], LimitMethod.HISTORY, basis="b")
    assert limits.center == 1 and limits.sd == D("0.02") and limits.upper == D("1.06")
    assert limits.n == 3 and limits.basis == "b" and limits.z(D("1.04")) == 2


def test_robust_baseline_ignores_a_spike():
    values = [D(v) for v in ("1", "2", "3", "4", "100")]
    robust = baseline(values, LimitMethod.ROBUST)
    assert robust.center == 3 and robust.sd == D("1.4826")
    assert baseline(values[:4], LimitMethod.ROBUST).center == D("2.5")  # even count: the middle two
    assert baseline(values, LimitMethod.HISTORY).sd > 40


@pytest.mark.parametrize(
    "values, method, message",
    [
        (["1", "2"], LimitMethod.HISTORY, "at least 3"),
        (["1", "1", "1"], LimitMethod.HISTORY, "no spread"),
        (["1", "2", "3"], LimitMethod.CERTIFICATE, "entered"),
    ],
)
def test_baseline_refusals(values, method, message):
    with pytest.raises(ValueError, match=message):
        baseline([D(v) for v in values], method)


def test_window_and_policy_validate():
    with pytest.raises(ValidationError, match="below its high end"):
        Window(low=D("2"), high=D("1"))
    with pytest.raises(ValidationError, match="history or robust"):
        MeasurePolicy(method=LimitMethod.CERTIFICATE)
    with pytest.raises(ValidationError, match="below its high end"):
        MeasurePolicy(window_low=D("2"), window_high=D("1"))
    assert MeasurePolicy(window_high=D("5")).window == Window(high=D("5"))


def test_series_key_slug_round_trips_awkward_text():
    k = key(instrument="ICP~MS/1 %", conditions="CP-Off-None", material="CCV 5 ppb", level=D("5.0"))
    assert SeriesKey.from_slug(k.slug) == k
    assert SeriesKey.from_slug(key().slug) == key()
    assert "ICP~MS/1 %" in k.label and "(CP-Off-None)" in k.label and "@ 5.0" in k.label
    assert "all" in SeriesKey(measure=Measure.ISTD_RECOVERY, instrument="I").label
    with pytest.raises(ValueError, match="not a series id"):
        SeriesKey.from_slug("nope")


def test_series_judges_only_points_not_excluded():
    assert [p.ref for p in series(1, 2, 3, excluded=(1,)).judged] == ["p0", "p2"]


def test_profile_lookups_and_one_setting_per_tool():
    setting = ToolSetting(tool="ewma@1", measures=frozenset({Measure.CHECK_RECOVERY}))
    profile = Profile(tools=(setting,))
    assert profile.setting("ewma@1") == setting and profile.setting("cusum@1") is None
    assert profile.policy(Measure.BLANK_LEVEL) == MeasurePolicy()
    with pytest.raises(ValidationError, match="appears twice"):
        Profile(tools=(setting, setting))


def limit_change(proposer=USER):
    limits = Limits(method=LimitMethod.CERTIFICATE, center=D(1), sd=D("0.02"))
    return ChangeRequest(
        kind=ChangeKind.LIMITS,
        reason="new lot",
        proposed_by=proposer,
        limit_set=LimitSet(series=key(), limits=limits, effective_from=T0),
    )


def test_a_manager_decides_and_a_self_approval_shows():
    change = limit_change()
    assert change.status == ChangeStatus.PROPOSED and not change.self_approved
    with pytest.raises(NotAllowed, match="only a quality manager"):
        change.decide(USER, True)
    with pytest.raises(NotAllowed, match="say why"):
        change.decide(MANAGER, False, " ")
    decided = change.model_copy(update={"decision": change.decide(MANAGER, False, "no")})
    assert decided.status == ChangeStatus.REJECTED
    with pytest.raises(NotAllowed, match="already decided"):
        decided.decide(MANAGER, True)
    own = limit_change(MANAGER)
    approved = own.model_copy(update={"decision": own.decide(MANAGER, True)})
    assert approved.status == ChangeStatus.APPROVED and approved.self_approved
    other = own.model_copy(update={"decision": own.decide(OTHER_MANAGER, True)})
    assert not other.self_approved


def test_a_change_carries_exactly_its_payload():
    with pytest.raises(ValidationError, match="carries a profile"):
        ChangeRequest(kind=ChangeKind.PROFILE, reason="r", proposed_by=USER)
    with pytest.raises(ValidationError, match="carries a limit set"):
        ChangeRequest(kind=ChangeKind.LIMITS, reason="r", proposed_by=USER)


def test_a_review_is_signed_by_a_manager_over_a_real_period():
    QcReview(period_from=date(2026, 9, 1), period_to=date(2026, 9, 30), comment="ok", by=MANAGER)
    with pytest.raises(ValidationError, match="only a quality manager"):
        QcReview(period_from=date(2026, 9, 1), period_to=date(2026, 9, 30), comment="ok", by=USER)
    with pytest.raises(ValidationError, match="ends before"):
        QcReview(period_from=date(2026, 9, 2), period_to=date(2026, 9, 1), comment="ok", by=MANAGER)


def test_labels():
    assert Measure.CRM_RECOVERY.label == "CRM recovery" and Measure.CRM_RECOVERY.is_recovery
    assert EventKind.NEW_LOT.label == "New lot"
