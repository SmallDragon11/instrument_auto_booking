"""排程計算：下次執行、目標週、錯過補跑（規格 §5、§7）。

所有時刻一律以台北時間計算：帶其他時區的 datetime 先換算為台北時間，不帶時區的拋 ValueError。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from instrument_booking.core.clock import ClockSync
from instrument_booking.core.models import TAIPEI, to_taipei

LATE_THRESHOLD = timedelta(minutes=1)


def next_run(now: datetime, weekday: int, at: time) -> datetime:
    now = to_taipei(now)
    days = (weekday - now.weekday()) % 7
    candidate = datetime.combine(now.date() + timedelta(days=days), at, tzinfo=TAIPEI)
    if candidate < now:
        candidate += timedelta(days=7)
    return candidate


def previous_run(now: datetime, weekday: int, at: time) -> datetime:
    now = to_taipei(now)
    days = (now.weekday() - weekday) % 7
    candidate = datetime.combine(now.date() - timedelta(days=days), at, tzinfo=TAIPEI)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def target_week(run_at: datetime) -> date:
    run_at = to_taipei(run_at)
    monday = run_at.date() - timedelta(days=run_at.weekday())
    return monday + timedelta(days=7)


def fire_at(run_at: datetime, sync: ClockSync) -> float:
    return to_taipei(run_at).timestamp() + sync.margin


@dataclass(frozen=True)
class Due:
    run_at: datetime
    target_monday: date
    late: bool


def due_run(now: datetime, weekday: int, at: time, executed: set[date]) -> Due | None:
    """最近一次已到點的執行，若其目標週尚未執行則回傳。

    上一次到點必在 7 天內，而目標週結束於其後至少 7 天，所以不必另外判斷「目標週已結束」；
    已過去的時段由 planner 以 FAILED（時段已開始或已過去）處理。
    """
    now = to_taipei(now)
    run_at = previous_run(now, weekday, at)
    monday = target_week(run_at)
    if monday in executed:
        return None
    return Due(run_at, monday, late=(now - run_at) > LATE_THRESHOLD)
