"""設定、預約清單、執行紀錄、氣體圖例快取的 JSON 儲存（規格 §4 storage、§11：%APPDATA%\\InstrumentBooking\\）。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence

from instrument_booking.core.models import BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.service.settings import Settings


class StoreError(Exception):
    """儲存檔損毀或無法讀取（訊息為給使用者看的繁體中文）。"""


@dataclass(frozen=True)
class RunRecord:
    """一次自動預約的紀錄（執行紀錄頁顯示用）。"""
    target_monday: date
    started_at: datetime
    late: bool
    clock_source: str | None
    clock_diff: float | None           # 真實時間 − 本機系統時鐘（秒）
    preflight_error: str | None
    requests: tuple[BookingRequest, ...]
    results: tuple[ItemResult, ...]


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
            "clock_source": r.clock_source, "clock_diff": r.clock_diff, "preflight_error": r.preflight_error,
            "requests": [request_to_dict(q) for q in r.requests], "results": [result_to_dict(x) for x in r.results]}


def run_from_dict(d: dict) -> RunRecord:
    return RunRecord(target_monday=date.fromisoformat(d["target_monday"]),
                     started_at=datetime.fromisoformat(d["started_at"]), late=d["late"],
                     clock_source=d["clock_source"], clock_diff=d["clock_diff"], preflight_error=d["preflight_error"],
                     requests=tuple(request_from_dict(q) for q in d["requests"]),
                     results=tuple(result_from_dict(x) for x in d["results"]))


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
    def __init__(self, root: Path) -> None:
        self.root = root

    # --- 設定 ---
    def load_settings(self) -> Settings:
        data = self._read(self.root / "settings.json")
        return Settings() if data is None else self._convert(settings_from_dict, data, "settings.json")

    def save_settings(self, s: Settings) -> None:
        self._write(self.root / "settings.json", settings_to_dict(s))

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
        """最新的在前。"""
        runs_dir = self.root / "runs"
        if not runs_dir.is_dir():
            return []
        paths = sorted(runs_dir.glob("*.json"), reverse=True)
        return [self._convert(run_from_dict, self._read(p), p.name) for p in paths]

    def executed_weeks(self) -> set[date]:
        """已有執行紀錄（含預檢失敗而放棄）的目標週：每個目標週只自動執行一次。"""
        return {r.target_monday for r in self.load_runs()}

    # --- 氣體圖例快取（離線也能編輯）---
    def load_legend(self) -> dict[str, str]:
        data = self._read(self.root / "legend.json")
        return {} if data is None else dict(data)

    def save_legend(self, legend: dict[str, str]) -> None:
        self._write(self.root / "legend.json", legend)

    # --- 內部 ---
    def _bookings_path(self, monday: date) -> Path:
        return self.root / "bookings" / f"{monday.isoformat()}.json"

    @staticmethod
    def _read(path: Path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StoreError(f"無法讀取 {path.name}：{e}") from e

    @staticmethod
    def _convert(fn, data, filename: str):
        try:
            return fn(data)
        except (KeyError, TypeError, ValueError) as e:
            raise StoreError(f"{filename} 內容損毀：{e}") from e

    @staticmethod
    def _write(path: Path, data) -> None:
        """先寫入暫存檔再取代，避免寫到一半當機留下損毀的檔案。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
