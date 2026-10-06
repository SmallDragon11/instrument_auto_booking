"""自動預約服務：背景執行緒每秒檢查一次，依時序預檢、重試、寫入並記錄結果（規格 §7、§9）。

step() 會在預檢與寫入時阻塞（寫入時會等到開放時間），所以必須在專用的背景執行緒呼叫，不可在 GUI 執行緒。
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Protocol, Sequence

from instrument_booking.browser.downloader import file_id_from_url
from instrument_booking.core.models import TAIPEI, BookingRequest, ItemResult, ItemStatus
from instrument_booking.core.schedule import LATE_THRESHOLD
from instrument_booking.service.cycle import (PREFLIGHT_LEAD, RETRY_LEAD, Action, CycleState, current_run_at,
                                              next_action)
from instrument_booking.service.housekeeping import LOGGER_NAME, describe_error
from instrument_booking.service.runner import RunCancelled
from instrument_booking.service.settings import Settings, validate_settings
from instrument_booking.storage.json_store import RunRecord

log = logging.getLogger(LOGGER_NAME)

RETRY_MIN_GAP = timedelta(minutes=1)  # 預檢失敗後至少隔多久才重試（補跑時避免連續失敗）

# 寫入前取消的原因（記錄於執行紀錄的 error）
CANCELLED_BY_USER = "已手動取消"
CANCEL_SCHEDULE_CHANGED = "自動預約時間已變更，取消本次寫入"
CANCEL_URL_CHANGED = "預約表網址已變更，取消本次寫入"
CANCEL_INVALID_SETTINGS = "設定無效，取消本次寫入"

STATUS_LABEL = {
    ItemStatus.SUCCESS: "成功",
    ItemStatus.TAKEN_IN_SNAPSHOT: "已被預約",
    ItemStatus.SELF_OVERLAP: "與自己重疊",
    ItemStatus.LIVE_CONFLICT: "即時衝突",
    ItemStatus.SUSPECTED_CLASH: "疑似撞車",
    ItemStatus.FAILED: "失敗",
}


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None: ...


def summarize(results: Sequence[ItemResult]) -> str:
    """例：「2 成功、1 即時衝突」；依 STATUS_LABEL 的順序。"""
    counts = Counter(r.status for r in results)
    return "、".join(f"{counts[s]} {label}" for s, label in STATUS_LABEL.items() if counts[s]) or "沒有預約"


class AutomationService:
    def __init__(self, *, store, runner, notifier: Notifier,
                 now: Callable[[], datetime] = lambda: datetime.now(TAIPEI)) -> None:
        self._store = store
        self._runner = runner
        self._notifier = notifier
        self._now = now
        self._state: CycleState | None = None
        self._prepared = None                              # 預檢成功的規劃（plan_ready 時才有）
        self._planned: tuple[BookingRequest, ...] = ()     # 預檢時實際規劃的預約清單
        self._last_error: str | None = None                # 最近一次預檢失敗的說明
        self._service_error: str | None = None             # 已通知過的服務錯誤（同一訊息只通知一次）
        self._cancel = threading.Event()                   # 使用者要求取消本次自動預約（任何執行緒都可設定）
        self._lock = threading.Lock()

    @property
    def state(self) -> CycleState | None:
        return self._state

    def cancel_current(self) -> None:
        """取消進行中（T−10 到開放時間）的自動預約；該週不再自動執行。只設定旗標，不會阻塞（GUI 可直接呼叫）。

        開放時間到了之後寫入不會被中斷；T−10 之前呼叫則沒有作用。
        """
        self._cancel.set()

    def step(self) -> None:
        with self._lock:
            now = self._now()
            settings = self._store.load_settings()
            if validate_settings(settings):
                self._discard_plan("設定無效")
                return  # 尚未完成設定
            executed = self._store.executed_weeks()
            run_at = current_run_at(now, settings.run_weekday, settings.run_time, executed,
                                    lambda m: bool(self._store.load_bookings(m)),
                                    catch_up_since=self._store.schedule_since())
            if self._state is None or self._state.run_at != run_at:
                self._discard_plan("執行時間改變")
                self._state, self._last_error = CycleState(run_at), None
                self._cancel.clear()
            if not self._state.finished and self._state.target_monday in executed:
                # 該週已有紀錄（例如寫入前被取消後改了時間）：每個目標週只自動執行一次
                self._state.finished = True
            if self._prepared is not None and file_id_from_url(settings.spreadsheet_url) != self._prepared.file_id:
                self._discard_plan("預約表網址改變")
            requests = self._store.load_bookings(self._state.target_monday)
            if not requests:
                self._discard_plan("預約清單被清空")
                return
            if self._prepared is not None:
                # 已校時：寫入的時機、是否延遲、開始時間一律以校時後的時間判斷（本機時鐘可能偏慢）
                now = datetime.fromtimestamp(self._prepared.clock.now(), TAIPEI)
            if self._cancel.is_set() and self._cancel_cycle(now, requests):
                return
            action = next_action(now, self._state)
            if action is Action.PREFLIGHT:
                self._preflight(settings, requests)
            elif action is Action.GIVE_UP:
                self._give_up(now, requests)
            elif action is Action.EXECUTE:
                self._execute(now, settings)

    def run_forever(self, stop: threading.Event, interval: float = 1.0) -> None:
        while not stop.wait(interval):
            try:
                self.step()
            except Exception as e:
                log.exception("自動預約服務發生未預期的錯誤")
                self._report_service_error(describe_error(e))
            else:
                self._service_error = None

    def _report_service_error(self, message: str) -> None:
        """同一訊息只通知一次；訊息改變或恢復正常後再出錯才再通知。"""
        if message == self._service_error:
            return
        self._service_error = message
        try:
            self._notifier.notify("自動預約服務發生錯誤", message)
        except Exception:
            log.exception("無法顯示通知")

    def _discard_plan(self, reason: str) -> None:
        """放棄已準備好的規劃並關閉待命的 Edge；之後（例如清單恢復時）可重新預檢。"""
        if self._prepared is None:
            return
        log.info("丟棄已準備的規劃（%s）", reason)
        self._prepared, self._planned = None, ()
        if self._state is not None:
            self._state.plan_ready = False
            self._state.preflight_attempts = 0
            self._state.retry_at = None
        self._runner.abandon()

    def _cancel_cycle(self, now: datetime, requests: list[BookingRequest]) -> bool:
        """處理使用者的取消：T−10 之後、尚未結束的週期 → 關閉待命的 Edge、記錄並通知；否則忽略。"""
        state = self._state
        if state.finished or now < state.run_at - PREFLIGHT_LEAD:
            self._cancel.clear()
            log.info("沒有進行中的自動預約，忽略取消")
            return False
        planned = self._planned or tuple(requests)
        self._discard_plan(CANCELLED_BY_USER)
        self._cancelled(now, planned, CANCELLED_BY_USER, late=self._is_late(now))
        return True

    def _cancelled(self, now: datetime, requests, reason: str, *, late: bool) -> None:
        log.warning("自動預約已取消：%s", reason)
        self._finish(now, requests, (), error=reason, late=late,
                     title="自動預約已取消", message=f"{reason}（本週不會再自動執行）")

    def _guard(self, settings: Settings, file_id: str) -> Callable[[], str | None]:
        """寫入前的取消檢查（在瀏覽器執行緒呼叫）：回傳取消原因或 None。

        服務執行緒此時持有 _lock 並等待寫入結束，所以這裡絕不可取 _lock；只讀旗標與重新讀取設定檔。
        星期或時間與這次寫入所依據的不同，代表目前設定的開放時間已不是 run_at：提早寫入等同偷跑。
        """
        schedule = (settings.run_weekday, settings.run_time)

        def guard() -> str | None:
            if self._cancel.is_set():
                return CANCELLED_BY_USER
            try:
                current = self._store.load_settings()
            except Exception as e:  # 無法確認目前設定：寧可不寫入
                return f"無法讀取設定（{describe_error(e)}），取消本次寫入"
            if validate_settings(current):
                return CANCEL_INVALID_SETTINGS
            if (current.run_weekday, current.run_time) != schedule:
                return CANCEL_SCHEDULE_CHANGED
            if file_id_from_url(current.spreadsheet_url) != file_id:
                return CANCEL_URL_CHANGED
            return None
        return guard

    def _preflight(self, settings: Settings, requests: list[BookingRequest]) -> None:
        state = self._state
        state.preflight_attempts += 1
        try:
            self._prepared = self._runner.prepare(settings, requests)
            self._planned = tuple(requests)
            state.plan_ready = True
            log.info("預檢完成：%s", state.run_at)
        except Exception as e:
            self._last_error = describe_error(e)
            log.warning("第 %d 次預檢失敗：%s", state.preflight_attempts, self._last_error)
            again = ""
            if state.preflight_attempts == 1:
                state.retry_at = max(state.run_at - RETRY_LEAD, self._now() + RETRY_MIN_GAP)
                again = f"，{state.retry_at:%H:%M} 會再試一次"
            try:
                self._runner.abandon()
            finally:  # 關閉 Edge 失敗也要通知
                self._notifier.notify("自動預約預檢失敗", f"{self._last_error}{again}")

    def _give_up(self, now: datetime, requests: list[BookingRequest]) -> None:
        error = self._last_error or "預檢失敗"
        self._finish(now, requests, (), error=error, late=self._is_late(now),
                     title="自動預約未執行", message=error)

    def _execute(self, now: datetime, settings: Settings) -> None:
        state, requests = self._state, self._planned  # 紀錄的清單＝實際規劃的那份
        late = self._is_late(now)
        guard = self._guard(settings, self._prepared.file_id)
        try:
            results = self._runner.run(self._prepared, settings, state.run_at, guard=guard)
        except RunCancelled as e:
            self._cancelled(now, requests, str(e), late=late)
            return
        except Exception as e:
            error = describe_error(e)
            log.error("寫入階段失敗：%s", error)
            self._finish(now, requests, (), error=error, late=late, title="自動預約失敗", message=error)
            return
        prefix = "（延遲執行）" if late else ""
        self._finish(now, requests, results, error=None, late=late,
                     title="自動預約完成", message=f"{prefix}{summarize(results)}")

    def _is_late(self, now: datetime) -> bool:
        return now - self._state.run_at > LATE_THRESHOLD

    def _finish(self, now: datetime, requests, results, *, error: str | None, late: bool,
                title: str, message: str) -> None:
        """結束這個週期並通知：無論紀錄是否寫入成功，都不可再執行一次，也一定要通知使用者。"""
        prepared = self._prepared
        self._state.finished = True
        self._prepared, self._planned = None, ()
        self._cancel.clear()  # 寫入期間（開放時間後）的取消要求不再有意義
        try:
            self._store.append_run(RunRecord(
                target_monday=self._state.target_monday, started_at=now, late=late,
                clock_source=prepared.sync.source.value if prepared else None,
                clock_diff=prepared.clock_diff if prepared else None,
                error=error, requests=tuple(requests), results=tuple(results)))
        except Exception as e:
            log.exception("執行紀錄寫入失敗")
            message = f"{message}（紀錄寫入失敗：{describe_error(e)}）"
        self._notifier.notify(title, message)
