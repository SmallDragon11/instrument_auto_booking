"""組裝 App 用的所有服務（GUI 只需呼叫 build_services）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from instrument_booking.browser.session import EdgeSession, default_profile_dir
from instrument_booking.service.automation import AutomationService, Notifier
from instrument_booking.service.housekeeping import setup_logging
from instrument_booking.service.occupancy import OccupancyService
from instrument_booking.service.runner import BookingRunner
from instrument_booking.service.worker import BrowserWorker
from instrument_booking.storage.json_store import JsonStore, default_data_dir


@dataclass
class Services:
    data_dir: Path
    profile_dir: Path
    snapshot_dir: Path
    store: JsonStore
    worker: BrowserWorker
    runner: BookingRunner
    occupancy: OccupancyService
    automation: AutomationService

    def shutdown(self) -> None:
        self.worker.shutdown()


def build_services(notifier: Notifier, *, data_dir: Path | None = None, profile_dir: Path | None = None,
                   session_factory=EdgeSession) -> Services:
    """不會啟動瀏覽器：BrowserWorker 在第一個瀏覽器工作時才開啟 Edge，且每次都讀取最新的網址設定。"""
    data_dir = data_dir or default_data_dir()
    profile_dir = profile_dir or default_profile_dir()
    snapshot_dir = data_dir / "snapshots"
    setup_logging(data_dir / "logs")
    store = JsonStore(data_dir)
    worker = BrowserWorker(lambda: session_factory(store.load_settings().spreadsheet_url, profile_dir))
    runner = BookingRunner(worker, snapshot_dir=snapshot_dir)
    occupancy = OccupancyService(worker, snapshot_dir=snapshot_dir, store=store)
    automation = AutomationService(store=store, runner=runner, notifier=notifier)
    return Services(data_dir, profile_dir, snapshot_dir, store, worker, runner, occupancy, automation)
