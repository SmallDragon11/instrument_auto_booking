"""設定、預約清單、執行紀錄、氣體圖例快取的 JSON 儲存（規格 §4 storage、§11：%APPDATA%\\InstrumentBooking\\）。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time as time_module
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence

from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus, to_taipei
from instrument_booking.service.settings import Settings


log = logging.getLogger("instrument_booking.storage")

# Windows：另一個執行緒正開著檔案（讀取或取代中）時，開檔或 os.replace 會拋 PermissionError，稍後重試即可
RETRY_TIMES = 10
RETRY_DELAY = 0.05  # 秒

CORRUPT_SUFFIX = ".corrupt"


class StoreError(Exception):
    """儲存檔損毀或無法讀取（訊息為給使用者看的繁體中文）。"""


@dataclass(frozen=True)
class RunRecord:
    """一次自動預約的紀錄（執行紀錄頁顯示用）。"""
    target_monday: date
    started_at: datetime               # 寫入工作開始的時間（放棄或取消時為當下時間）
    late: bool
    clock_source: str | None
    clock_diff: float | None           # 真實時間 − 本機系統時鐘（秒）
    error: str | None                  # 預檢或寫入階段的錯誤（成功時為 None）
    requests: tuple[BookingRequest, ...]
    results: tuple[ItemResult, ...]
    snapshot_at: datetime | None = None  # 預檢下載快照的時間（沒有預檢成功、或舊版紀錄時為 None）


def default_data_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "InstrumentBooking"


def request_to_dict(r: BookingRequest) -> dict:
    return {"id": r.id, "instrument": r.instrument.name, "date": r.date.isoformat(),
            "start_hour": r.start_hour, "end_hour": r.end_hour, "gas": r.gas, "color": r.color}


def request_from_dict(d: dict) -> BookingRequest:
    return BookingRequest(id=d["id"], instrument=Instrument[d["instrument"]], date=date.fromisoformat(d["date"]),
                          start_hour=d["start_hour"], end_hour=d["end_hour"], gas=d["gas"], color=d["color"])


def result_to_dict(r: ItemResult) -> dict:
    return {"request_id": r.request_id, "status": r.status.name, "reason": r.reason, "warning": r.warning,
            "written_at": r.written_at.isoformat() if r.written_at else None}


def result_from_dict(d: dict) -> ItemResult:
    return ItemResult(request_id=d["request_id"], status=ItemStatus[d["status"]], reason=d["reason"],
                      warning=d["warning"],
                      written_at=datetime.fromisoformat(d["written_at"]) if d["written_at"] else None)


def run_to_dict(r: RunRecord) -> dict:
    return {"target_monday": r.target_monday.isoformat(), "started_at": r.started_at.isoformat(), "late": r.late,
            "clock_source": r.clock_source, "clock_diff": r.clock_diff, "error": r.error,
            "requests": [request_to_dict(q) for q in r.requests], "results": [result_to_dict(x) for x in r.results],
            "snapshot_at": r.snapshot_at.isoformat() if r.snapshot_at else None}


def run_from_dict(d: dict) -> RunRecord:
    return RunRecord(target_monday=date.fromisoformat(d["target_monday"]),
                     started_at=datetime.fromisoformat(d["started_at"]), late=d["late"],
                     clock_source=d["clock_source"], clock_diff=d["clock_diff"],
                     error=d["error"] if "error" in d else d["preflight_error"],  # 相容舊版欄位名稱
                     requests=tuple(request_from_dict(q) for q in d["requests"]),
                     results=tuple(result_from_dict(x) for x in d["results"]),
                     snapshot_at=datetime.fromisoformat(d["snapshot_at"]) if d.get("snapshot_at") else None)


# settings.json 中不屬於 Settings 的欄位：自動預約的星期或時間最後一次被設定的時間（台北時間）。
# 早於此時間的週期不補跑，避免使用者把時間改早時立即為下一週寫入（等同偷跑）。
SCHEDULE_SINCE = "schedule_since"


def settings_to_dict(s: Settings) -> dict:
    d = asdict(s)
    d["run_time"] = s.run_time.strftime("%H:%M")
    return d


def settings_from_dict(d: dict) -> Settings:
    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in d.items() if k in known}
    if "run_time" in values:
        values["run_time"] = time.fromisoformat(values["run_time"])
    return Settings(**values)


class JsonStore:
    """GUI 與背景服務會同時使用同一個 JsonStore：每次寫入用唯一的暫存檔，遇到檔案被占用時重試。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._unrenamed: set[str] = set()  # 損毀但無法改名的執行紀錄檔（本次執行期間）

    # --- 設定 ---
    def load_settings(self) -> Settings:
        data = self._read(self.root / "settings.json")
        return Settings() if data is None else self._convert(settings_from_dict, data, "settings.json")

    def save_settings(self, s: Settings, now: datetime | None = None) -> None:
        """星期或時間與已存的不同（含第一次存檔、舊檔損毀）時，把 schedule_since 設為現在。"""
        path = self.root / "settings.json"
        data = settings_to_dict(s)
        try:
            old = self._read(path)
            old_settings = None if old is None else settings_from_dict(old)
        except (StoreError, KeyError, TypeError, ValueError, AttributeError):
            old, old_settings = None, None
        if old_settings is None or (old_settings.run_weekday, old_settings.run_time) != (s.run_weekday, s.run_time):
            data[SCHEDULE_SINCE] = to_taipei(now or datetime.now(TAIPEI)).isoformat()
        elif SCHEDULE_SINCE in old:
            data[SCHEDULE_SINCE] = old[SCHEDULE_SINCE]
        self._write(path, data)

    def schedule_since(self) -> datetime | None:
        """自動預約的星期或時間最後一次被設定的時間；從未記錄時為 None。"""
        data = self._read(self.root / "settings.json")
        if data is None:
            return None
        return self._convert(lambda d: to_taipei(datetime.fromisoformat(d[SCHEDULE_SINCE]))
                             if SCHEDULE_SINCE in d and d[SCHEDULE_SINCE] is not None else None,
                             data, "settings.json")

    # --- 預約清單（每個目標週一個檔，清單順序＝優先序）---
    def load_bookings(self, monday: date) -> list[BookingRequest]:
        path = self._bookings_path(monday)
        data = self._read(path)
        if data is None:
            return []
        return self._convert(lambda d: [request_from_dict(x) for x in d["requests"]], data, path.name)

    def save_bookings(self, monday: date, requests: Sequence[BookingRequest]) -> None:
        self._write(self._bookings_path(monday), {"requests": [request_to_dict(r) for r in requests]})

    # --- 執行紀錄（每次一個檔）---
    def append_run(self, record: RunRecord) -> None:
        name = record.started_at.strftime("%Y%m%d-%H%M%S-%f")
        self._write(self.root / "runs" / f"{name}.json", run_to_dict(record))

    def load_runs(self) -> list[RunRecord]:
        """最新的在前。

        無法解析的紀錄檔改名為 *.json.corrupt 隔離後略過（改名失敗也略過），不可讓自動化因此停擺；
        讀不到檔案（例如被占用）則照常拋 StoreError——那不代表損毀，略過可能讓該週被重跑。
        """
        runs_dir = self.root / "runs"
        if not runs_dir.is_dir():
            return []
        runs = []
        for p in sorted(runs_dir.glob("*.json"), reverse=True):
            raw = self._read_bytes(p)
            if raw is None:
                continue  # 列出後被刪除
            try:
                runs.append(run_from_dict(json.loads(raw.decode("utf-8"))))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                self._quarantine(p, e)
        return runs

    def corrupt_runs(self) -> list[str]:
        """被隔離（損毀）的執行紀錄檔名，供 GUI 顯示警告。"""
        runs_dir = self.root / "runs"
        on_disk = {p.name for p in runs_dir.glob(f"*.json{CORRUPT_SUFFIX}")} if runs_dir.is_dir() else set()
        with self._lock:
            unrenamed = {n for n in self._unrenamed if (runs_dir / n).exists()}
        return sorted(on_disk | unrenamed)

    def executed_weeks(self) -> set[date]:
        """已有執行紀錄（含預檢失敗而放棄）的目標週：每個目標週只自動執行一次。"""
        return {r.target_monday for r in self.load_runs()}

    def _quarantine(self, path: Path, error: Exception) -> None:
        log.error("執行紀錄 %s 損毀，已略過：%s", path.name, error)
        try:
            path.rename(path.with_name(path.name + CORRUPT_SUFFIX))
        except OSError:
            log.exception("無法隔離損毀的執行紀錄 %s", path.name)
            with self._lock:
                self._unrenamed.add(path.name)

    # --- 氣體圖例快取（離線也能編輯）---
    def load_legend(self) -> dict[str, str]:
        data = self._read(self.root / "legend.json")
        return {} if data is None else self._convert(dict, data, "legend.json")

    def save_legend(self, legend: dict[str, str]) -> None:
        self._write(self.root / "legend.json", legend)

    # --- 內部 ---
    def _bookings_path(self, monday: date) -> Path:
        return self.root / "bookings" / f"{monday.isoformat()}.json"

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        """檔案不存在時回傳 None；被占用時重試；仍失敗拋 StoreError。"""
        for attempt in range(RETRY_TIMES):
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None
            except PermissionError as e:
                if attempt == RETRY_TIMES - 1:
                    raise StoreError(f"無法讀取 {path.name}：{e}") from e
                time_module.sleep(RETRY_DELAY)
            except OSError as e:
                raise StoreError(f"無法讀取 {path.name}：{e}") from e

    @staticmethod
    def _read(path: Path):
        raw = JsonStore._read_bytes(path)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise StoreError(f"無法讀取 {path.name}：{e}") from e

    @staticmethod
    def _convert(fn, data, filename: str):
        try:
            return fn(data)
        except (KeyError, TypeError, ValueError) as e:
            raise StoreError(f"{filename} 內容損毀：{e}") from e

    @staticmethod
    def _write(path: Path, data) -> None:
        """先寫入唯一的暫存檔（多個執行緒同時寫同一檔也不互相覆蓋）、確實寫入磁碟後再取代。

        取代時檔案正被其他執行緒開啟會拋 PermissionError，重試；仍失敗的 OSError 一律包成 StoreError。
        """
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(RETRY_TIMES):
                try:
                    os.replace(tmp, path)
                    tmp = None
                    return
                except PermissionError:
                    if attempt == RETRY_TIMES - 1:
                        raise
                    time_module.sleep(RETRY_DELAY)
        except OSError as e:
            raise StoreError(f"無法寫入 {path.name}：{e}") from e
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
