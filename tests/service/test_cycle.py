from datetime import date, datetime, time

import pytest

from instrument_booking.core.models import TAIPEI
from instrument_booking.service.cycle import Action, CycleState, current_run_at, next_action

FRI, AT = 4, time(13, 0)
RUN = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)


def t(day, hh, mm=0, ss=0):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TAIPEI)


def test_upcoming_run_when_previous_cycle_done_or_empty():
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: False) == RUN
    assert current_run_at(t(7, 10), FRI, AT, {date(2026, 10, 5)}, lambda m: True) == RUN


def test_missed_previous_cycle_with_requests_is_caught_up():
    # 10/2 那次（目標週 10/5）沒有執行、且有清單 → 現在（10/7）立即補跑那一次
    assert current_run_at(t(7, 10), FRI, AT, set(), lambda m: m == date(2026, 10, 5)) == t(2, 13)


def test_after_run_time_the_same_cycle_stays_current_until_executed():
    assert current_run_at(t(9, 13, 0, 5), FRI, AT, set(), lambda m: True) == RUN
    assert current_run_at(t(9, 13, 5), FRI, AT, {date(2026, 10, 12)}, lambda m: True) == t(16, 13)


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
