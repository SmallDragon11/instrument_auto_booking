"""依優先序比對快照，產生寫入清單（規格 §7 預檢）。自我重疊留到執行時判斷。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Sequence

from instrument_booking.core.legend import resolve_color
from instrument_booking.core.models import (
    TAIPEI,
    BookingRequest,
    CellFont,
    CellState,
    ItemResult,
    ItemStatus,
    to_taipei,
)
from instrument_booking.core.occupancy import cell_font, cell_state, is_empty
from instrument_booking.core.sheet_locator import LocateError, SheetIndex, Target


@dataclass(frozen=True)
class PlannedWrite:
    request: BookingRequest
    target: Target
    color: str
    fonts: tuple[CellFont, ...]  # 與 target.rows 一一對應
    warning: str | None = None


@dataclass(frozen=True)
class Plan:
    writes: tuple[PlannedWrite, ...]
    skipped: tuple[ItemResult, ...]
    order: tuple[str, ...]


def describe_busy(target: Target, cells: Sequence[CellState]) -> str | None:
    for row, cell in zip(target.rows, cells):
        if not is_empty(cell):
            where = target.cell_name(row)
            if cell.value not in (None, ""):
                return f"{where} 已有「{cell.value}」"
            return f"{where} 已塗色"
    return None


def make_plan(wb, index: SheetIndex, requests: Sequence[BookingRequest], now: datetime) -> Plan:
    """now 必須帶時區（換算為台北時間比較；時段一律是台北時間）。"""
    now = to_taipei(now)
    writes: list[PlannedWrite] = []
    skipped: list[ItemResult] = []
    for req in requests:
        start = datetime.combine(req.date, time(req.start_hour), tzinfo=TAIPEI)
        if start <= now:
            skipped.append(ItemResult(req.id, ItemStatus.FAILED, reason="時段已開始或已過去"))
            continue
        try:
            target = index.locate(req)
        except LocateError as e:
            skipped.append(ItemResult(req.id, ItemStatus.FAILED, reason=str(e)))
            continue
        ws = wb[target.sheet]
        busy = describe_busy(target, [cell_state(ws.cell(r, target.column)) for r in target.rows])
        if busy:
            skipped.append(ItemResult(req.id, ItemStatus.TAKEN_IN_SNAPSHOT, reason=busy))
            continue
        color, warning = resolve_color(ws, req)
        fonts = tuple(cell_font(ws.cell(r, target.column)) for r in target.rows)
        writes.append(PlannedWrite(req, target, color, fonts, warning))
    return Plan(tuple(writes), tuple(skipped), tuple(r.id for r in requests))
