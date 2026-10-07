"""主視窗：左側導覽三頁、系統匣、常駐提示、每秒更新狀態、結束流程（規格 §8、§9）。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QSystemTrayIcon, QVBoxLayout, QWidget
from qfluentwidgets import (Action, BodyLabel, FluentIcon, FluentWindow, NavigationItemPosition, PushButton,
                            SystemTrayMenu, Theme, setTheme, setThemeColor)

from instrument_booking.app.autostart import Registry, apply_autostart
from instrument_booking.app.history_page import HistoryPage
from instrument_booking.app.icon import ACCENT, app_icon
from instrument_booking.app.notifier import QtNotifier
from instrument_booking.app.settings_page import SettingsPage
from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.app.texts import CLIPBOARD_HINT, status_line
from instrument_booking.app.week_model import displayed_week
from instrument_booking.app.week_page import WeekPage
from instrument_booking.app.widgets import ask, show_info
from instrument_booking.core.models import TAIPEI
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.connection import run_connection_test, start_login
from instrument_booking.service.container import Services
from instrument_booking.service.housekeeping import LOGGER_NAME
from instrument_booking.service.settings import Settings
from instrument_booking.storage.json_store import StoreError

log = logging.getLogger(LOGGER_NAME)

APP_TITLE = "實驗規劃助手"
TICK_MS = 1000
SHUTDOWN_TIMEOUT_MS = 60_000  # 結束時最多等瀏覽器工作（例如寫入中）多久
AUTOMATION_JOIN_TIMEOUT = 45  # 結束時最多等自動化執行緒記錄取消多久（秒；須小於 SHUTDOWN_TIMEOUT_MS）
THEME = {"system": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


class StatusBanner(QFrame):
    """所有頁面上方的常駐提示（設定未完成、服務出錯、設定檔損毀）。"""

    def __init__(self, on_open_settings: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBanner")
        self.setStyleSheet("#statusBanner { background: rgba(196, 43, 28, 0.14); border-radius: 6px; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        self.label = BodyLabel("")
        self.label.setWordWrap(True)
        self.button = PushButton(FluentIcon.SETTING, "前往設定")
        self.button.clicked.connect(on_open_settings)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button)
        self.hide()

    def show_message(self, text: str | None) -> None:
        if text:
            self.label.setText(text)
        self.setVisible(bool(text))


def banner_text(status: ServiceStatus, settings_error: str | None) -> str | None:
    if settings_error:
        return f"設定檔無法讀取：{settings_error}。請到「設定」頁重新儲存。"
    if status.phase is ServicePhase.NOT_CONFIGURED:
        return "尚未完成設定，不會自動預約：請到「設定」頁填寫名字與預約表網址，並做一次連線測試。"
    if status.service_error:
        return f"自動預約服務發生錯誤：{status.service_error}"
    return None


class MainWindow(FluentWindow):
    _shutdown_finished = Signal()

    def __init__(self, services: Services, notifier: QtNotifier, *, stop: threading.Event,
                 autostart_command: str | None, autostart_registry: Registry | None,
                 now: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
                 confirm: Callable[[QWidget, str, str], bool] = ask,
                 quit_app: Callable[[], None] = QApplication.quit,
                 automation_thread: threading.Thread | None = None) -> None:
        super().__init__()
        self._services = services
        self._stop = stop
        self._automation_thread = automation_thread
        self._now = now
        self._confirm = confirm
        self._quit_app = quit_app
        self._autostart_command = autostart_command
        self._autostart_registry = autostart_registry
        self._quitting = False
        self._quit_called = False
        self._asking = False
        self._last_phase: ServicePhase | None = None
        self._ready_notified_for: datetime | None = None
        self._told_tray = False
        self._settings_error: str | None = None
        try:
            self._settings = services.store.load_settings()
        except StoreError as e:
            self._settings, self._settings_error = Settings(), str(e)

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.tasks = BackgroundTasks(self)

        self.week_page = WeekPage(services.store, self.tasks, refresh_occupancy=services.occupancy.refresh,
                                  cancel_run=services.automation.cancel_current, confirm=confirm)
        self.history_page = HistoryPage(services.store, self.tasks)
        self.settings_page = SettingsPage(
            services.store, self.tasks,
            connection_test=lambda: run_connection_test(services.store, services.worker, services.snapshot_dir),
            start_login=lambda: start_login(services.worker, services.store.load_settings(), services.profile_dir),
            on_saved=self._on_settings_saved, confirm=confirm)
        self.addSubInterface(self.week_page, FluentIcon.CALENDAR, "下週預約")
        self.addSubInterface(self.history_page, FluentIcon.HISTORY, "執行紀錄")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "設定", NavigationItemPosition.BOTTOM)
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

        # 常駐提示放在頁面上方：把 stackedWidget 包進垂直版面
        self.banner = StatusBanner(lambda: self.switchTo(self.settings_page))
        self.widgetLayout.removeWidget(self.stackedWidget)
        column = QVBoxLayout()
        column.setContentsMargins(12, 0, 12, 0)
        column.setSpacing(6)
        column.addWidget(self.banner)
        column.addWidget(self.stackedWidget, 1)
        self.widgetLayout.addLayout(column)

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        menu = SystemTrayMenu(parent=self)
        open_action = Action(FluentIcon.HOME, "開啟視窗", self)
        open_action.triggered.connect(self.show_window)
        quit_action = Action(FluentIcon.CLOSE, "結束", self)
        quit_action.triggered.connect(self.request_quit)
        menu.addActions([open_action, quit_action])
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        notifier.notified.connect(self._on_notified)
        self._shutdown_finished.connect(self._finish_quit)
        self._apply_theme(self._settings.theme)
        if self._settings_error is None:  # 設定檔損毀時用的是預設值，不能據此改動開機自動啟動
            self._apply_autostart(self._settings.autostart)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)
        self.tick()
        self.history_page.reload()

    # --- 每秒更新 ---
    def tick(self) -> None:
        try:
            status = self._services.automation.status()
            now = self._now()
            self.week_page.set_week(displayed_week(status.target_monday, now, self._settings))
            self.week_page.apply_status(status, now)
            self.settings_page.apply_status(status)
            self.banner.show_message(banner_text(status, self._settings_error))
            self.tray.setToolTip(f"{APP_TITLE}\n{status_line(status, now)}")
            if (status.phase is ServicePhase.READY and self._last_phase is not ServicePhase.READY
                    and status.run_at != self._ready_notified_for):  # 每個預約時間只通知一次
                self._ready_notified_for = status.run_at
                self.notify("自動預約已準備好", f"{status.run_at:%H:%M} 會自動寫入。{CLIPBOARD_HINT}")
            self._last_phase = status.phase
        except Exception:
            log.exception("更新畫面狀態失敗")

    # --- 通知 ---
    def notify(self, title: str, message: str) -> None:
        log.info("通知：%s｜%s", title, message)
        self.tray.showMessage(title, message, app_icon(), 10_000)

    def _on_notified(self, title: str, message: str) -> None:
        self.notify(title, message)
        self.history_page.reload()  # 結束一個週期（完成、放棄、取消）時一定會通知

    # --- 設定 ---
    def _on_settings_saved(self, settings: Settings) -> None:
        self._settings, self._settings_error = settings, None
        self._apply_theme(settings.theme)
        self._apply_autostart(settings.autostart, report=True)
        self.tick()

    def _apply_theme(self, theme: str) -> None:
        setTheme(THEME.get(theme, Theme.AUTO))
        setThemeColor(ACCENT)

    def _apply_autostart(self, enabled: bool, *, report: bool = False) -> None:
        if self._autostart_registry is None:
            return
        try:
            apply_autostart(enabled, command=self._autostart_command, registry=self._autostart_registry)
        except OSError as e:
            log.warning("無法設定開機自動啟動：%s", e)
            if report:
                show_info(self.settings_page, "warning", "無法設定開機自動啟動", str(e))

    # --- 視窗與系統匣 ---
    def _on_page_changed(self, _index: int) -> None:
        if self.stackedWidget.currentWidget() is self.history_page:
            self.history_page.reload()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        if self._quitting:  # 結束中不讓第二個實例把關閉中的視窗叫回來
            return
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        event.ignore()
        if self._settings.minimize_to_tray:
            self.hide()
            if not self._told_tray:
                self._told_tray = True
                self.notify(APP_TITLE, "仍在系統匣執行，時間到會自動預約。要完全結束請在系統匣圖示按右鍵 →「結束」。")
        else:
            self.request_quit()

    # --- 結束 ---
    def request_quit(self) -> None:
        if self._quitting or self._asking:
            return
        self.show_window()  # 視窗隱藏在系統匣時，確認對話框（子視窗）會看不到
        status = self._services.automation.status()
        if status.editing_locked:
            dialog_title = "自動預約進行中"
        else:
            dialog_title = f"結束{APP_TITLE}"
        self._asking = True
        try:
            ok = self._confirm(self, dialog_title,
                               "現在結束會取消本次自動預約（若已開始寫入，會等寫完才結束）。確定要結束嗎？" if status.editing_locked
                               else "結束後就不會自動預約，直到下次開啟。確定要結束嗎？")
        finally:
            self._asking = False
        if not ok:
            return
        status = self._services.automation.status()  # 重新讀取狀態
        if status.editing_locked:
            self._services.automation.cancel_current()
        self._quitting = True
        self._stop.set()  # 先停止自動化執行緒，再關閉瀏覽器
        self.timer.stop()
        self.tasks.shutdown()
        self.tray.hide()
        self.hide()
        threading.Thread(target=self._shutdown_services, name="shutdown", daemon=True).start()
        QTimer.singleShot(SHUTDOWN_TIMEOUT_MS, self._finish_quit)

    def _shutdown_services(self) -> None:
        thread = self._automation_thread
        if thread is not None and thread.is_alive():
            thread.join(AUTOMATION_JOIN_TIMEOUT)  # 等它處理完待處理的取消，再關閉瀏覽器
        try:
            self._services.shutdown()
        except Exception:
            log.exception("關閉服務時發生錯誤")
        self._shutdown_finished.emit()

    def _finish_quit(self) -> None:
        if self._quit_called:
            return
        self._quit_called = True
        self._quit_app()
