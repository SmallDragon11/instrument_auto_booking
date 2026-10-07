"""GUI 顯示用的文字（純函式，不依賴 Qt）：倒數、服務狀態、執行紀錄。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from instrument_booking.app.week_model import WEEKDAY_NAMES, day_label, describe
from instrument_booking.core.clock import ClockSource
from instrument_booking.core.models import ItemStatus, to_taipei
from instrument_booking.service.automation import STATUS_LABEL, ServicePhase, ServiceStatus
from instrument_booking.storage.json_store import RunRecord

STATUS_ICON = {
    ItemStatus.SUCCESS: "✅",
    ItemStatus.TAKEN_IN_SNAPSHOT: "⏭",
    ItemStatus.SELF_OVERLAP: "⏭",
    ItemStatus.LIVE_CONFLICT: "⚡",
    ItemStatus.SUSPECTED_CLASH: "⚠️",
    ItemStatus.FAILED: "❌",
}

CLIPBOARD_HINT = "預約時間前 10 分鐘到寫入完成，App 會在背景使用 Edge 與系統剪貼簿，這段時間請勿複製貼上。"


def week_range_label(monday: date) -> str:
    """例：「10/12（一）– 10/18（日）」。"""
    return f"{day_label(monday)}– {day_label(monday + timedelta(days=6))}"


def when_label(dt: datetime) -> str:
    """例：「10/09（五）13:00」（台北時間）。"""
    dt = to_taipei(dt)
    return f"{dt.month}/{dt.day}（{WEEKDAY_NAMES[dt.weekday()]}）{dt:%H:%M}"


def countdown(now: datetime, run_at: datetime) -> str:
    """例：「還有 2 天 3 小時」「還有 1 小時 5 分」「還有 4 分 30 秒」；時間到了為「時間已到」。"""
    seconds = int((run_at - now).total_seconds())
    if seconds <= 0:
        return "時間已到"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"還有 {days} 天 {hours} 小時"
    if hours:
        return f"還有 {hours} 小時 {minutes} 分"
    return f"還有 {minutes} 分 {secs} 秒"


def status_line(status: ServiceStatus, now: datetime) -> str:
    """下週預約頁頂部的一行狀態。"""
    phase = status.phase
    if phase is ServicePhase.NOT_CONFIGURED:
        return "尚未完成設定：請到「設定」頁填寫名字與預約表網址"
    if phase is ServicePhase.ERROR:
        return f"自動預約服務發生錯誤：{status.service_error or '未知的錯誤'}"
    if phase is ServicePhase.PREPARING:
        return "預檢中：校時並下載最新的預約表…"
    if phase is ServicePhase.RUNNING:
        return "寫入中…"
    if phase is ServicePhase.DONE:
        return "本週已處理，結果請看「執行紀錄」"
    if phase is ServicePhase.RETRY_WAIT and status.retry_at is not None:
        return f"預檢失敗，{status.retry_at:%H:%M} 會再試一次：{status.last_error or ''}".rstrip("：")
    if status.run_at is None:
        return "等待中"
    if phase is ServicePhase.READY:
        return f"已準備好，{status.run_at:%H:%M} 自動寫入（{countdown(now, status.run_at)}）"
    return f"下次自動預約：{when_label(status.run_at)}（{countdown(now, status.run_at)}）"


def clock_label(source: str | None, diff: float | None) -> str:
    """diff＝真實時間 − 本機時鐘：正值代表本機時鐘慢。"""
    if source is None:
        return "未校時"
    if source == ClockSource.LOCAL.value:
        return "未能網路校時，使用本機時間"
    if diff is None:
        return f"校時：{source}"
    if abs(diff) < 0.005:
        return f"校時：{source}（本機時鐘準確）"
    return f"校時：{source}（本機時鐘{'慢' if diff > 0 else '快'} {abs(diff):.2f} 秒）"


@dataclass(frozen=True)
class ResultRow:
    priority: int
    text: str            # 預約內容
    status: str          # 例：「✅ 成功」
    detail: str          # 原因、警告、寫入時間（可為空字串）
    kind: str            # "success" | "skipped" | "warning" | "failed" | "none"（顏色用）


_KIND = {
    ItemStatus.SUCCESS: "success",
    ItemStatus.TAKEN_IN_SNAPSHOT: "skipped",
    ItemStatus.SELF_OVERLAP: "skipped",
    ItemStatus.LIVE_CONFLICT: "skipped",
    ItemStatus.SUSPECTED_CLASH: "warning",
    ItemStatus.FAILED: "failed",
}


def run_title(record: RunRecord) -> str:
    return f"{week_range_label(record.target_monday)} 的預約"


def run_meta(record: RunRecord) -> list[str]:
    """執行時間、是否延遲、校時與快照時間。"""
    started = to_taipei(record.started_at)
    lines = [f"執行時間：{when_label(started)}:{started:%S}" + ("（延遲執行）" if record.late else "")]
    lines.append(clock_label(record.clock_source, record.clock_diff))
    if record.snapshot_at is not None:
        lines.append(f"預約表下載於 {to_taipei(record.snapshot_at):%H:%M:%S}")
    return lines


def result_rows(record: RunRecord) -> list[ResultRow]:
    """依優先序列出每一筆；沒有結果的（例如預檢失敗）為「未執行」。"""
    results = {r.request_id: r for r in record.results}
    rows = []
    for i, req in enumerate(record.requests, start=1):
        res = results.get(req.id)
        if res is None:
            rows.append(ResultRow(i, describe(req), "－ 未執行", "", "none"))
            continue
        details = [x for x in (res.reason, res.warning) if x]
        if res.written_at is not None:
            written = to_taipei(res.written_at)
            details.append(f"寫入於 {written:%H:%M:%S}.{written.microsecond // 1000:03d}")
        rows.append(ResultRow(i, describe(req), f"{STATUS_ICON[res.status]} {STATUS_LABEL[res.status]}",
                              "；".join(details), _KIND[res.status]))
    return rows
