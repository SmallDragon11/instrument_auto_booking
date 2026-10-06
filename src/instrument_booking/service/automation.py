"""自動預約服務：背景執行緒每秒檢查一次，依時序預檢、重試、寫入並記錄結果（規格 §7、§9）。

step() 會在預檢與寫入時阻塞（寫入時會等到開放時間），所以必須在專用的背景執行緒呼叫，不可在 GUI 執行緒。
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from datetime import datetime
from typing import Callable, Protocol, Sequence

from instrument_booking.core.models import TAIPEI, BookingRequest, ItemResult, ItemStatus
from instrument_booking.core.schedule import LATE_THRESHOLD
from instrument_booking.service.cycle import Action, CycleState, current_run_at, next_action
from instrument_booking.service.housekeeping import LOGGER_NAME, describe_error
from instrument_booking.service.settings import Settings, validate_settings
from instrument_booking.storage.json_store import RunRecord

log = logging.getLogger(LOGGER_NAME)

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
        self._prepared = None
        self._last_error: str | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CycleState | None:
        return self._state

    def step(self) -> None:
        with self._lock:
            now = self._now()
            settings = self._store.load_settings()
            if validate_settings(settings):
                return  # 尚未完成設定
            run_at = current_run_at(now, settings.run_weekday, settings.run_time, self._store.executed_weeks(),
                                    lambda m: bool(self._store.load_bookings(m)))
            if self._state is None or self._state.run_at != run_at:
                self._state, self._prepared, self._last_error = CycleState(run_at), None, None
            requests = self._store.load_bookings(self._state.target_monday)
            if not requests:
                return
            action = next_action(now, self._state)
            if action is Action.PREFLIGHT:
                self._preflight(settings, requests)
            elif action is Action.GIVE_UP:
                self._give_up(now, requests)
            elif action is Action.EXECUTE:
                self._execute(now, settings, requests)

    def run_forever(self, stop: threading.Event, interval: float = 1.0) -> None:
        while not stop.wait(interval):
            try:
                self.step()
            except Exception:
                log.exception("自動預約服務發生未預期的錯誤")

    def _preflight(self, settings: Settings, requests: list[BookingRequest]) -> None:
        state = self._state
        state.preflight_attempts += 1
        try:
            self._prepared = self._runner.prepare(settings, requests)
            state.plan_ready = True
            log.info("預檢完成：%s", state.run_at)
        except Exception as e:
            self._last_error = describe_error(e)
            log.warning("第 %d 次預檢失敗：%s", state.preflight_attempts, self._last_error)
            self._runner.abandon()
            again = "，2 分鐘前會再試一次" if state.preflight_attempts == 1 else ""
            self._notifier.notify("自動預約預檢失敗", f"{self._last_error}{again}")

    def _give_up(self, now: datetime, requests: list[BookingRequest]) -> None:
        self._record(now, requests, (), error=self._last_error or "預檢失敗")
        self._notifier.notify("自動預約未執行", self._last_error or "預檢失敗")

    def _execute(self, now: datetime, settings: Settings, requests: list[BookingRequest]) -> None:
        state = self._state
        late = now - state.run_at > LATE_THRESHOLD
        try:
            results = self._runner.run(self._prepared, settings, state.run_at)
        except Exception as e:
            error = describe_error(e)
            log.error("寫入階段失敗：%s", error)
            self._record(now, requests, (), error=error)
            self._notifier.notify("自動預約失敗", error)
            return
        self._record(now, requests, results, error=None, late=late)
        prefix = "（延遲執行）" if late else ""
        self._notifier.notify("自動預約完成", f"{prefix}{summarize(results)}")

    def _record(self, now: datetime, requests, results, *, error: str | None, late: bool = False) -> None:
        prepared = self._prepared
        self._store.append_run(RunRecord(
            target_monday=self._state.target_monday, started_at=now, late=late,
            clock_source=prepared.sync.source.value if prepared else None,
            clock_diff=prepared.clock_diff if prepared else None,
            error=error, requests=tuple(requests), results=tuple(results)))
        self._state.finished = True
        self._prepared = None
