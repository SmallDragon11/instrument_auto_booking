from datetime import date, datetime, time

import pytest

from instrument_booking.core.clock import ClockSource, ClockSync
from instrument_booking.core.models import TAIPEI
from instrument_booking.core.schedule import Due, due_run, fire_at, next_run, previous_run, target_week

FRI, AT = 4, time(13, 0)


def t(day, hh=0, mm=0, ss=0, us=0):
    return datetime(2026, 10, day, hh, mm, ss, us, tzinfo=TAIPEI)


@pytest.mark.parametrize("now,expected", [
    (t(7, 10), t(9, 13)),         # 週三 → 本週五
    (t(9, 12, 59), t(9, 13)),     # 週五到點前
    (t(9, 13), t(9, 13)),         # 剛好到點
    (t(9, 13, 0, 1), t(16, 13)),  # 已過 → 下週五
])
def test_next_run(now, expected):
    assert next_run(now, FRI, AT) == expected


@pytest.mark.parametrize("now,expected", [
    (t(9, 13, 0, 1), t(9, 13)),
    (t(8, 10), t(2, 13)),
])
def test_previous_run(now, expected):
    assert previous_run(now, FRI, AT) == expected


def test_target_week_is_following_monday():
    assert target_week(t(9, 13)) == date(2026, 10, 12)
    assert target_week(t(5, 13)) == date(2026, 10, 12)  # 週一執行也是下一週


def test_fire_at_adds_margin():
    assert fire_at(t(9, 13), ClockSync(ClockSource.NTP, 0)) == pytest.approx(t(9, 13).timestamp() + 0.05)


def test_due_run_on_time():
    assert due_run(t(9, 13, 0, 0, 500_000), FRI, AT, set()) == Due(t(9, 13), date(2026, 10, 12), late=False)


def test_due_run_late():
    assert due_run(t(9, 13, 20), FRI, AT, set()).late is True


def test_due_run_already_executed():
    assert due_run(t(9, 13, 20), FRI, AT, {date(2026, 10, 12)}) is None


def test_due_run_before_this_weeks_run_returns_previous_cycle():
    # 週四時，最近一次已到點的是 10/2，其目標週 10/5–10/11 尚未執行 → 補跑（呼叫端再看有無清單）
    d = due_run(t(8, 10), FRI, AT, set())
    assert (d.run_at, d.target_monday, d.late) == (t(2, 13), date(2026, 10, 5), True)


def test_due_run_sunday_late_night_targets_next_day_week():
    d = due_run(datetime(2026, 10, 19, 0, 30, tzinfo=TAIPEI), 6, time(23, 0), set())
    assert (d.target_monday, d.late) == (date(2026, 10, 19), True)
