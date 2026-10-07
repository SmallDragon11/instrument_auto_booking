"""下週預約頁的純邏輯（不依賴 Qt）：拖曳 → 預約、重疊並排、優先序、複製上週（規格 §8.1、§10.4）。

預約清單的順序就是優先序（第 1 筆最優先），所有儀器共用同一份清單。
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Callable, Sequence

from instrument_booking.core.legend import OVEN_DEFAULT
from instrument_booking.core.models import FIRST_HOUR, LAST_HOUR, BookingRequest, Instrument
from instrument_booking.core.schedule import next_run, target_week
from instrument_booking.service.occupancy import WeekView
from instrument_booking.service.settings import Settings

WEEKDAY_NAMES = "一二三四五六日"
SLOT_HOURS = range(FIRST_HOUR, LAST_HOUR)  # 每一格的起始小時：9..23


def new_id() -> str:
    return uuid.uuid4().hex


def day_label(d: date) -> str:
    """例：「10/12（一）」。"""
    return f"{d.month}/{d.day}（{WEEKDAY_NAMES[d.weekday()]}）"


def hours_label(start_hour: int, end_hour: int) -> str:
    """例：「09:00–12:00」；24 顯示為 24:00。"""
    return f"{start_hour:02d}:00–{end_hour:02d}:00"


def describe(r: BookingRequest) -> str:
    """例：「10/12（一）A-牆 09:00–12:00 Ar」。"""
    text = f"{day_label(r.date)}{r.instrument.label} {hours_label(r.start_hour, r.end_hour)}"
    return f"{text} {r.gas}" if r.gas else text


def drag_hours(a: int, b: int) -> tuple[int, int]:
    """拖曳的起點格與終點格（各為該格的起始小時，順序不拘）→ (start_hour, end_hour)，含兩端的格子。"""
    start, last = sorted((a, b))
    if start < FIRST_HOUR or last >= LAST_HOUR:
        raise ValueError(f"時段超出範圍：{a}、{b}")
    return start, last + 1


def make_request(instrument: Instrument, day: date, start_hour: int, end_hour: int, gas: str | None,
                 legend: dict[str, str], *, id_factory: Callable[[], str] = new_id) -> BookingRequest:
    """管型爐用所選氣體在圖例中的色碼（記錄下來作為執行時圖例找不到氣體的備用色）；烘箱用固定色。"""
    if instrument.kind == "oven":
        return BookingRequest(id_factory(), instrument, day, start_hour, end_hour, None,
                              OVEN_DEFAULT[instrument.sub_index])
    if not gas:
        raise ValueError("請先選擇氣體")
    if gas not in legend:
        raise ValueError(f"圖例中沒有氣體 {gas}")
    return BookingRequest(id_factory(), instrument, day, start_hour, end_hour, gas, legend[gas])


def change_gas(requests: Sequence[BookingRequest], request_id: str, gas: str,
               legend: dict[str, str]) -> list[BookingRequest]:
    if gas not in legend:
        raise ValueError(f"圖例中沒有氣體 {gas}")
    return [replace(r, gas=gas, color=legend[gas]) if r.id == request_id else r for r in requests]


def remove(requests: Sequence[BookingRequest], request_id: str) -> list[BookingRequest]:
    return [r for r in requests if r.id != request_id]


def reorder(requests: Sequence[BookingRequest], ids: Sequence[str]) -> list[BookingRequest]:
    """依 ids 的順序重排（清單拖曳後）；ids 必須剛好是同一組預約。"""
    by_id = {r.id: r for r in requests}
    if sorted(ids) != sorted(by_id):
        raise ValueError("重排後的預約與原清單不一致")
    return [by_id[i] for i in ids]


def _same_slot(a: BookingRequest, b: BookingRequest) -> bool:
    return (a.instrument, a.date, a.start_hour, a.end_hour, a.gas) == (b.instrument, b.date, b.start_hour,
                                                                         b.end_hour, b.gas)


def copy_previous_week(previous: Sequence[BookingRequest], current: Sequence[BookingRequest], *,
                       id_factory: Callable[[], str] = new_id) -> tuple[list[BookingRequest], int]:
    """把上週清單的日期 +7 天，依原順序加在目前清單之後；已有相同時段的略過。回傳 (新清單, 加入幾筆)。"""
    result = list(current)
    added = 0
    for r in previous:
        moved = replace(r, id=id_factory(), date=r.date + timedelta(days=7))
        if any(_same_slot(moved, x) for x in result):
            continue
        result.append(moved)
        added += 1
    return result, added


def layout_lanes(requests: Sequence[BookingRequest]) -> dict[str, tuple[int, int]]:
    """同一儀器、同一天重疊的區塊並排（類似 Google 日曆）：id → (第幾欄, 共幾欄)。

    彼此（直接或間接）重疊的區塊為一組，組內依起始時間、較長者優先，放進第一個空著的欄。
    """
    lanes: dict[str, tuple[int, int]] = {}
    groups: dict[tuple[Instrument, date], list[BookingRequest]] = {}
    for r in requests:
        groups.setdefault((r.instrument, r.date), []).append(r)
    for items in groups.values():
        clusters: list[tuple[list[tuple[BookingRequest, int]], list[int]]] = []  # (區塊與欄, 每欄的結束時間)
        cluster_end = 0
        for r in sorted(items, key=lambda r: (r.start_hour, -r.end_hour)):
            if not clusters or r.start_hour >= cluster_end:
                clusters.append(([], []))
                cluster_end = 0
            members, lane_ends = clusters[-1]
            lane = next((i for i, end in enumerate(lane_ends) if end <= r.start_hour), len(lane_ends))
            if lane == len(lane_ends):
                lane_ends.append(r.end_hour)
            else:
                lane_ends[lane] = r.end_hour
            members.append((r, lane))
            cluster_end = max(cluster_end, r.end_hour)
        for members, lane_ends in clusters:
            for r, lane in members:
                lanes[r.id] = (lane, len(lane_ends))
    return lanes


def slot_warnings(r: BookingRequest, view: WeekView | None) -> list[str]:
    """新增的預約與最近一次下載的表格比對：已被佔用或找不到該日（仍可加入，執行時會照常檢查）。"""
    if view is None:
        return []
    warnings = []
    if (r.instrument.kind, r.date) in view.unavailable:
        warnings.append(f"預約表中找不到 {day_label(r.date)}（工作表可能尚未建立）")
    busy = [h for h in r.hours if (r.instrument, r.date, h) in view.occupied]
    if busy:
        times = "、".join(f"{h:02d}:00" for h in busy)
        warnings.append(f"{times} 目前已被預約，到時這筆會被略過")
    return warnings


def displayed_week(service_target: date | None, now: datetime, settings: Settings) -> date:
    """頁面要編輯的目標週：服務已決定的目標週（含補跑）優先；服務尚未排程時依設定的星期與時間推算。"""
    if service_target is not None:
        return service_target
    defaults = Settings()
    weekday = settings.run_weekday if 0 <= settings.run_weekday <= 6 else defaults.run_weekday
    return target_week(next_run(now, weekday, settings.run_time))
