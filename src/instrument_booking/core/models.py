"""核心資料模型。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum

FIRST_HOUR = 9
LAST_HOUR = 24  # 最後一格「2300~」結束於 24:00
TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
_HEX6 = re.compile(r"^[0-9A-F]{6}$")


def to_taipei(dt: datetime) -> datetime:
    """轉為台北時間（實驗室的時刻一律以台北時間定義）。不帶時區的 datetime 無法判斷，拋 ValueError。"""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"時間必須帶時區（例如 tzinfo=TAIPEI）：{dt!r}")
    return dt.astimezone(TAIPEI)


class Instrument(Enum):
    TUBE_A = ("tube", 0, "A-牆")
    TUBE_B = ("tube", 1, "B-窗")
    TUBE_C = ("tube", 2, "C-小房間")
    OVEN_A = ("oven", 0, "ovenA")
    OVEN_B = ("oven", 1, "ovenB")

    def __init__(self, kind: str, sub_index: int, label: str) -> None:
        self.kind = kind
        self.sub_index = sub_index
        self.label = label


@dataclass(frozen=True)
class BookingRequest:
    id: str
    instrument: Instrument
    date: date
    start_hour: int
    end_hour: int
    gas: str | None
    color: str  # 選擇當時的圖例色碼；圖例找不到氣體時的備用色

    def __post_init__(self) -> None:
        if not (FIRST_HOUR <= self.start_hour < self.end_hour <= LAST_HOUR):
            raise ValueError(f"時段不合法：{self.start_hour}–{self.end_hour}")
        if self.instrument.kind == "tube" and not self.gas:
            raise ValueError("管型爐必須指定氣體")
        if self.instrument.kind == "oven" and self.gas is not None:
            raise ValueError("烘箱不可指定氣體")
        if not _HEX6.match(self.color):
            raise ValueError(f"色碼格式錯誤：{self.color!r}（應為 6 碼大寫十六進位）")

    @property
    def hours(self) -> range:
        return range(self.start_hour, self.end_hour)

    def overlaps(self, other: BookingRequest) -> bool:
        return (
            self.instrument == other.instrument
            and self.date == other.date
            and self.start_hour < other.end_hour
            and other.start_hour < self.end_hour
        )


@dataclass(frozen=True)
class CellState:
    value: object | None
    color: str | None  # 6 碼大寫 RGB；None＝無填色或白色；其他顏色型態以 "theme:N" 等表示


@dataclass(frozen=True)
class CellFont:
    """快照中儲存格原本的字型；貼上時逐格寫回，避免字型被重設（見 docs/spike-report.md）。"""
    size: float | None
    bold: bool
    h_align: str | None


class ItemStatus(Enum):
    SUCCESS = "成功"
    TAKEN_IN_SNAPSHOT = "快照中已被預約"
    SELF_OVERLAP = "與自己已成功的一筆重疊"
    LIVE_CONFLICT = "即時衝突"
    SUSPECTED_CLASH = "疑似撞車"
    FAILED = "失敗"


@dataclass(frozen=True)
class ItemResult:
    request_id: str
    status: ItemStatus
    reason: str | None = None
    warning: str | None = None
    written_at: datetime | None = None
