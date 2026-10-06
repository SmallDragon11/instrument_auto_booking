"""一次預約的執行流程：預檢規劃、到點寫入、寫入後驗證（規格 §7、§9）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, Sequence

import openpyxl

from instrument_booking.core.models import TAIPEI, BookingRequest, CellFont, CellState, ItemResult, ItemStatus
from instrument_booking.core.planner import Plan, PlannedWrite, describe_busy, make_plan
from instrument_booking.core.sheet_locator import SheetIndex, Target


class SnapshotSource(Protocol):
    def download(self) -> Path: ...


class SheetWriter(Protocol):
    def prewarm(self, sheets: Sequence[str]) -> None: ...
    def read_range(self, sheet: str, a1: str) -> list[CellState]: ...
    def paste_booking(self, sheet: str, a1: str, color: str, name: str,
                      fonts: Sequence[CellFont]) -> None: ...
    def recover(self) -> None: ...


class TimeSource(Protocol):
    def now(self) -> float: ...


class WriterError(Exception):
    """單筆操作失敗（例如逾時），可繼續下一筆。"""


class WriterCrashed(Exception):
    """瀏覽器異常，需呼叫 recover()。"""


class EarlyWriteError(Exception):
    """時間未到卻要寫入（偷跑防護）。"""


def load_snapshot(path: Path) -> tuple[openpyxl.Workbook, SheetIndex]:
    wb = openpyxl.load_workbook(path, data_only=True)
    return wb, SheetIndex.build(wb)


def preflight(source: SnapshotSource, requests: Sequence[BookingRequest], now: datetime) -> Plan:
    wb, index = load_snapshot(source.download())
    return make_plan(wb, index, requests, now)


def wait_until(clock: TimeSource, deadline: float, sleep: Callable[[float], None]) -> None:
    while (remaining := deadline - clock.now()) > 0:
        sleep(min(remaining, 0.05))


def _is_ours(cells: Sequence[CellState], name: str, color: str) -> bool:
    return (
        bool(cells)
        and str(cells[0].value or "").strip() == name.strip()
        and all(c.value in (None, "") for c in cells[1:])
        and all(c.color == color for c in cells)
    )


def _clash_reason(target: Target, cells: Sequence[CellState], name: str) -> str:
    first = str(cells[0].value or "").strip() if cells else ""
    if first != name.strip():
        return f"驗證時第一格為「{first}」" if first else "驗證時第一格是空的"
    for row, cell in zip(target.rows[1:], cells[1:]):
        if cell.value not in (None, ""):
            return f"驗證時 {target.cell_name(row)} 有「{cell.value}」"
    return "驗證時底色不符"


def _join(*parts: str | None) -> str | None:
    text = "；".join(p for p in parts if p)
    return text or None


def execute(plan: Plan, writer: SheetWriter, name: str, clock: TimeSource, not_before: float, *,
            sleep: Callable[[float], None], verify_delay: float = 5.0) -> list[ItemResult]:
    results: dict[str, ItemResult] = {r.request_id: r for r in plan.skipped}

    try:
        writer.prewarm(sorted({w.target.sheet for w in plan.writes}))
    except WriterCrashed:
        writer.recover()
    except WriterError:
        pass  # 預熱失敗不影響寫入

    wait_until(clock, not_before, sleep)

    succeeded: list[BookingRequest] = []
    to_verify: list[tuple[PlannedWrite, bool]] = []  # (寫入, 是否確定已貼上)
    written_at: dict[str, datetime] = {}
    abort_reason: str | None = None

    for w in plan.writes:
        req, t = w.request, w.target
        if abort_reason:
            results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason=abort_reason)
            continue
        clash = next((s for s in succeeded if req.overlaps(s)), None)
        if clash:
            results[req.id] = ItemResult(req.id, ItemStatus.SELF_OVERLAP, reason=f"與已成功寫入的 {clash.id} 重疊")
            continue
        try:
            cells = writer.read_range(t.sheet, t.a1)
            if len(cells) != len(t.rows):
                results[req.id] = ItemResult(req.id, ItemStatus.FAILED,
                                             reason=f"讀取 {t.a1} 得到 {len(cells)} 格（應為 {len(t.rows)} 格），為安全起見不寫入")
                continue
            busy = describe_busy(t, cells)
            if busy:
                results[req.id] = ItemResult(req.id, ItemStatus.LIVE_CONFLICT, reason=busy)
                continue
            if clock.now() < not_before:
                raise EarlyWriteError("時間未到，拒絕寫入")
            writer.paste_booking(t.sheet, t.a1, w.color, name, w.fonts)
        except EarlyWriteError as e:
            abort_reason = str(e)
            results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason=abort_reason)
            continue
        except WriterCrashed:
            writer.recover()
            to_verify.append((w, False))
            continue
        except WriterError as e:
            results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason=f"操作失敗：{e}")
            continue
        written_at[req.id] = datetime.fromtimestamp(clock.now(), TAIPEI)
        succeeded.append(req)
        to_verify.append((w, True))

    if to_verify:
        sleep(verify_delay)

    for w, certain in to_verify:
        req, t = w.request, w.target
        try:
            cells = writer.read_range(t.sheet, t.a1)
            if len(cells) != len(t.rows):
                raise WriterError("讀取格數不符")
        except (WriterError, WriterCrashed) as e:
            if isinstance(e, WriterCrashed):
                writer.recover()
            if certain:
                results[req.id] = ItemResult(req.id, ItemStatus.SUCCESS, warning=_join(w.warning, "寫入後無法驗證"),
                                             written_at=written_at[req.id])
            else:
                results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason="瀏覽器異常，無法確認是否寫入")
            continue
        if _is_ours(cells, name, w.color):
            results[req.id] = ItemResult(req.id, ItemStatus.SUCCESS, warning=w.warning,
                                         written_at=written_at.get(req.id))
        elif certain:
            results[req.id] = ItemResult(req.id, ItemStatus.SUSPECTED_CLASH, reason=_clash_reason(t, cells, name),
                                         warning=w.warning, written_at=written_at[req.id])
        else:
            results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason="瀏覽器異常，寫入未完成")

    return [results[rid] for rid in plan.order]
