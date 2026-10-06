"""一次預約的執行流程：預檢規劃、到點寫入、寫入後驗證（規格 §7、§9）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, Sequence, TypeVar

import openpyxl

from instrument_booking.core.models import TAIPEI, BookingRequest, CellFont, CellState, ItemResult, ItemStatus
from instrument_booking.core.planner import Plan, PlannedWrite, describe_busy, make_plan
from instrument_booking.core.sheet_locator import SheetIndex, Target

T = TypeVar("T")


class SnapshotSource(Protocol):
    def download(self) -> Path: ...


class SheetWriter(Protocol):
    """操作線上試算表的寫入器（Plan 02 以瀏覽器實作）。

    共同約定：單筆操作失敗但瀏覽器仍可用時拋 WriterError（execute 會繼續下一筆）；
    瀏覽器異常、狀態不明時拋 WriterCrashed（execute 會呼叫 recover()）。
    拋出其他任何例外時，execute 一律視同 WriterCrashed（寫入狀態不確定）。
    """

    def prewarm(self, sheets: Sequence[str]) -> None:
        """依序切換到每個工作表，讓網頁先載入。失敗不影響之後的寫入。"""
        ...

    def read_range(self, sheet: str, a1: str) -> list[CellState]:
        """讀取範圍內每格的文字與底色（Ctrl+C），不可修改文件。

        - 必須回傳恰好與範圍列數相同的 CellState，依列由上而下；
          單格範圍需多選相鄰一列（下一列；當天最後一格改選上一列）再複製，回傳前丟棄多讀的那一列。
        - color 為 6 碼大寫 RGB（不含 #），白色或無填色為 None；不認得的顏色不可回傳 None。
        - 讀取結果（剪貼簿 HTML）須保留，作為隨後 paste_booking 的底稿。
        """
        ...

    def paste_booking(self, sheet: str, a1: str, color: str, name: str,
                      fonts: Sequence[CellFont]) -> None:
        """以 Ctrl+V 一次寫入名字與整段底色。

        - 只能貼上「由同一範圍剛剛 read_range 複製並改寫」的內容：每格改為 color、
          逐格帶入 fonts（與範圍各列一一對應）、第一格填 name；沒有對應的底稿就拋 WriterError，
          絕不貼上未驗證的內容。
        - 實作端也必須在開放時間前拒絕貼上（拋 EarlyWriteError；Plan 02 由建構子傳入開放時間），
          與 execute 的檢查形成雙重偷跑防護。
        - 失敗時拋 WriterError（確定沒有貼上、可繼續）或 WriterCrashed（不確定是否貼上、需 recover）。
        """
        ...

    def recover(self) -> None:
        """瀏覽器異常後重啟並回到試算表頁面。無法復原時拋出例外（execute 會放棄其餘寫入）。"""
        ...


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


class _Crash(Exception):
    """writer 拋出 WriterCrashed 或未知例外：寫入狀態不確定，需 recover()。"""


def _call(fn: Callable[..., T], *args) -> T:
    """呼叫 writer；WriterError、EarlyWriteError 原樣拋出，其他任何例外一律轉為 _Crash。"""
    try:
        return fn(*args)
    except (WriterError, EarlyWriteError):
        raise
    except Exception as e:
        raise _Crash(str(e)) from e


@dataclass
class _Pending:
    """待驗證的一筆。certain＝確定已貼上；否則由驗證結果決定成敗。"""
    write: PlannedWrite
    certain: bool
    note: str = "瀏覽器異常"  # 不確定時，說明原因


class _Execution:
    """execute 的執行狀態；每筆的結果都記在 results，確保最後能完整回報。"""

    def __init__(self, plan: Plan, writer: SheetWriter, name: str, clock: TimeSource, not_before: float):
        self.plan, self.writer, self.name = plan, writer, name
        self.clock, self.not_before = clock, not_before
        self.results: dict[str, ItemResult] = {r.request_id: r for r in plan.skipped}
        self.succeeded: list[BookingRequest] = []
        self.pending: list[_Pending] = []
        self.written_at: dict[str, datetime] = {}
        self.abort_reason: str | None = None

    def _fail(self, req: BookingRequest, reason: str) -> None:
        self.results[req.id] = ItemResult(req.id, ItemStatus.FAILED, reason=reason)

    def _unsure(self, w: PlannedWrite, note: str | None = None) -> None:
        self.pending.append(_Pending(w, certain=False, note=note or "瀏覽器異常"))

    def _recover(self) -> bool:
        """重啟瀏覽器；失敗時設定 abort_reason（其餘未處理的列為 FAILED）並回傳 False。"""
        try:
            self.writer.recover()
            return True
        except Exception as e:
            self.abort_reason = f"瀏覽器無法復原：{e}"
            return False

    def prewarm(self) -> None:
        try:
            _call(self.writer.prewarm, sorted({w.target.sheet for w in self.plan.writes}))
        except WriterError:
            pass  # 預熱失敗不影響寫入
        except Exception:
            self._recover()

    def write(self, w: PlannedWrite) -> None:
        req = w.request
        if self.abort_reason:
            self._fail(req, self.abort_reason)
            return
        clash = next((s for s in self.succeeded if req.overlaps(s)), None)
        if clash:
            self.results[req.id] = ItemResult(req.id, ItemStatus.SELF_OVERLAP, reason=f"與已成功寫入的 {clash.id} 重疊")
            return
        try:
            self._attempt(w, retry=False)
            return
        except _Crash:
            pass
        except EarlyWriteError as e:
            self._abort_early(req, e)
            return
        # 當掉（規格 §9）：重啟後同一筆重試一次；重試時重新即時檢查，已生效的不重寫
        if not self._recover():
            self._unsure(w, self.abort_reason)
            return
        try:
            self._attempt(w, retry=True)
        except _Crash:
            # 再次當掉：不再重試，交給驗證判斷是否已寫入
            self._unsure(w, None if self._recover() else self.abort_reason)
        except EarlyWriteError as e:
            self._abort_early(req, e)

    def _abort_early(self, req: BookingRequest, e: EarlyWriteError) -> None:
        self.abort_reason = str(e)
        self._fail(req, self.abort_reason)

    def _attempt(self, w: PlannedWrite, *, retry: bool) -> None:
        """即時檢查後貼上。當掉時拋 _Crash；偷跑時拋 EarlyWriteError。

        retry＝當掉復原後的重試：前一次可能已貼上，所以讀到自己的內容視為已寫入，
        而無法讀取或格數不符時不寫入、交給驗證判斷（不可直接回報 FAILED）。
        """
        req, t = w.request, w.target
        try:
            cells = _call(lambda: list(self.writer.read_range(t.sheet, t.a1)))
        except WriterError as e:
            if retry:
                self._unsure(w, f"復原後重新讀取失敗：{e}")
            else:
                self._fail(req, f"操作失敗：{e}")
            return
        if len(cells) != len(t.rows):
            if retry:
                self._unsure(w, f"復原後重新讀取 {t.a1} 得到 {len(cells)} 格（應為 {len(t.rows)} 格）")
            else:
                self._fail(req, f"讀取 {t.a1} 得到 {len(cells)} 格（應為 {len(t.rows)} 格），為安全起見不寫入")
            return
        if retry and _is_ours(cells, self.name, w.color):
            # 當掉前的貼上已生效：視為已寫入（寫入時間不確定）
            self.succeeded.append(req)
            self.pending.append(_Pending(w, certain=True))
            return
        busy = describe_busy(t, cells)
        if busy:
            self.results[req.id] = ItemResult(req.id, ItemStatus.LIVE_CONFLICT, reason=busy)
            return
        if self.clock.now() < self.not_before:
            raise EarlyWriteError("時間未到，拒絕寫入")
        try:
            _call(self.writer.paste_booking, t.sheet, t.a1, w.color, self.name, w.fonts)
        except WriterError as e:
            self._fail(req, f"操作失敗：{e}")
            return
        self.written_at[req.id] = datetime.fromtimestamp(self.clock.now(), TAIPEI)
        self.succeeded.append(req)
        self.pending.append(_Pending(w, certain=True))

    def verify(self) -> None:
        """逐筆讀回驗證。瀏覽器無法復原後不再讀取，其餘一律以「無法驗證」回報。"""
        usable = True
        for p in self.pending:
            w = p.write
            req, t = w.request, w.target
            cells = None
            if usable:
                try:
                    cells = _call(lambda: list(self.writer.read_range(t.sheet, t.a1)))
                except _Crash:
                    usable = self._recover()
                except Exception:
                    pass  # WriterError 等：這一筆無法驗證
            if cells is None or len(cells) != len(t.rows):
                self._unverified(p)
            elif _is_ours(cells, self.name, w.color):
                self.results[req.id] = ItemResult(req.id, ItemStatus.SUCCESS, warning=w.warning,
                                                  written_at=self.written_at.get(req.id))
            elif p.certain:
                self.results[req.id] = ItemResult(req.id, ItemStatus.SUSPECTED_CLASH,
                                                  reason=_clash_reason(t, cells, self.name),
                                                  warning=w.warning, written_at=self.written_at.get(req.id))
            else:
                self._fail(req, f"{p.note}，寫入未完成")

    def _unverified(self, p: _Pending) -> None:
        req = p.write.request
        if p.certain:
            self.results[req.id] = ItemResult(req.id, ItemStatus.SUCCESS, warning=_join(p.write.warning, "寫入後無法驗證"),
                                              written_at=self.written_at.get(req.id))
        else:
            self._fail(req, f"{p.note}，無法確認是否寫入")


def execute(plan: Plan, writer: SheetWriter, name: str, clock: TimeSource, not_before: float, *,
            sleep: Callable[[float], None], verify_delay: float = 5.0) -> list[ItemResult]:
    """依優先序到點寫入並驗證（規格 §7、§9）。

    writer 的任何例外都不會中斷流程：一律回傳完整結果清單（順序同 plan.order）。
    絕不在 not_before 之前貼上，也絕不貼上即時讀取為非空的範圍。
    """
    run = _Execution(plan, writer, name, clock, not_before)
    run.prewarm()
    wait_until(clock, not_before, sleep)
    for w in plan.writes:
        run.write(w)
    if run.pending:
        sleep(verify_delay)
    run.verify()
    return [run.results[rid] for rid in plan.order]
