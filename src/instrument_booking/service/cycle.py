"""每週自動預約的時序決策（純邏輯；規格 §7：T−10 預檢、T−2 重試、T−1 待命寫入）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Callable

from instrument_booking.core.schedule import next_run, previous_run, target_week

PREFLIGHT_LEAD = timedelta(minutes=10)
RETRY_LEAD = timedelta(minutes=2)
EXECUTE_LEAD = timedelta(minutes=1)


class Action(Enum):
    PREFLIGHT = "預檢"
    EXECUTE = "寫入"
    GIVE_UP = "放棄"


@dataclass
class CycleState:
    """一次執行週期（一個 run_at）的進度。"""
    run_at: datetime
    preflight_attempts: int = 0
    plan_ready: bool = False
    finished: bool = False

    @property
    def target_monday(self) -> date:
        return target_week(self.run_at)


def current_run_at(now: datetime, weekday: int, at: time, executed: set[date],
                   has_requests: Callable[[date], bool]) -> datetime:
    """要處理的執行時間：上一次到點的週期若尚未執行且有預約清單 → 立即補跑；否則下一次。"""
    prev = previous_run(now, weekday, at)
    monday = target_week(prev)
    if monday not in executed and has_requests(monday):
        return prev
    return next_run(now, weekday, at)


def next_action(now: datetime, state: CycleState) -> Action | None:
    if state.finished:
        return None
    if state.plan_ready:
        return Action.EXECUTE if now >= state.run_at - EXECUTE_LEAD else None
    if state.preflight_attempts == 0:
        return Action.PREFLIGHT if now >= state.run_at - PREFLIGHT_LEAD else None
    if state.preflight_attempts == 1:
        return Action.PREFLIGHT if now >= state.run_at - RETRY_LEAD else None
    return Action.GIVE_UP
