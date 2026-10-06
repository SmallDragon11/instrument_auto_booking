from datetime import date, datetime, time

import pytest

from instrument_booking.core.models import TAIPEI
from instrument_booking.service.cycle import Action, CycleState, current_run_at, next_action

FRI, AT = 4, time(13, 0)
RUN = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)


def t(day, hh, mm=0, ss=0):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TAIPEI)


def test_upcoming_run_when_previous_cycle_done_or_empty():
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: False, catch_up_since=None) == RUN
    assert current_run_at(t(7, 10), FRI, AT, {date(2026, 10, 5)}, lambda m: True, catch_up_since=None) == RUN


def test_missed_previous_cycle_with_requests_is_caught_up():
    # 10/2 那次（目標週 10/5）沒有執行、且有清單 → 現在（10/7）立即補跑那一次
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: m == date(2026, 10, 5), catch_up_since=None) == t(2, 13)
    # 設定早已存在（早於錯過的那次）→ 仍補跑
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: True, catch_up_since=t(1, 9)) == t(2, 13)
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: True, catch_up_since=t(2, 13)) == t(2, 13)


def test_schedule_changed_after_previous_run_time_is_not_caught_up():
    # 週三（10/7）10:00 把週五改成週一：本週一 13:00 已過去，但設定是之後才改的 → 不補跑，等下週一
    assert current_run_at(t(7, 10, 0, 1), 0, AT, set(), lambda m: True, catch_up_since=t(7, 10)) == t(12, 13)
    # 第一次存設定之前的週期也不補跑
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: True, catch_up_since=t(6, 8)) == RUN


def test_after_run_time_the_same_cycle_stays_current_until_executed():
    assert current_run_at(t(9, 13, 0, 5), FRI, AT, set(), lambda m: True, catch_up_since=t(1, 9)) == RUN
    assert current_run_at(t(9, 13, 5), FRI, AT, {date(2026, 10, 12)}, lambda m: True, catch_up_since=None) == t(16, 13)


@pytest.mark.parametrize("now,expected", [
    (t(9, 12, 49), None),
    (t(9, 12, 50), Action.PREFLIGHT),
    (t(9, 13, 30), Action.PREFLIGHT),  # 錯過時間：仍先預檢
])
def test_first_preflight_at_t_minus_10(now, expected):
    assert next_action(now, CycleState(RUN)) is expected


@pytest.mark.parametrize("now,expected", [(t(9, 12, 57), None), (t(9, 12, 58), Action.PREFLIGHT)])
def test_retry_at_t_minus_2(now, expected):
    assert next_action(now, CycleState(RUN, preflight_attempts=1)) is expected


@pytest.mark.parametrize("now,expected", [(t(9, 13, 30, 59), None), (t(9, 13, 31), Action.PREFLIGHT)])
def test_retry_waits_until_retry_at(now, expected):
    # 補跑時 13:30 第一次預檢失敗 → retry_at＝max(T−2, 失敗時間＋1 分)＝13:31
    assert next_action(now, CycleState(RUN, preflight_attempts=1, retry_at=t(9, 13, 31))) is expected


def test_give_up_after_two_failed_preflights():
    assert next_action(t(9, 12, 59), CycleState(RUN, preflight_attempts=2)) is Action.GIVE_UP


@pytest.mark.parametrize("now,expected", [(t(9, 12, 58, 59), None), (t(9, 12, 59), Action.EXECUTE),
                                          (t(9, 13, 20), Action.EXECUTE)])
def test_execute_from_t_minus_1_once_plan_ready(now, expected):
    assert next_action(now, CycleState(RUN, preflight_attempts=1, plan_ready=True)) is expected


def test_finished_cycle_does_nothing():
    assert next_action(t(9, 13, 5), CycleState(RUN, plan_ready=True, finished=True)) is None


def test_target_monday():
    assert CycleState(RUN).target_monday == date(2026, 10, 12)
