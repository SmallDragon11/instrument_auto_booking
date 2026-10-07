"""App 進入點：單一執行個體 → 組裝服務 → 主視窗 → 背景自動化執行緒。

以 --background 啟動（開機自動啟動）時只顯示系統匣圖示，不開視窗。
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from instrument_booking.app.autostart import BACKGROUND_ARG, RunKey, launch_command
from instrument_booking.app.icon import app_icon
from instrument_booking.app.main_window import APP_TITLE, MainWindow
from instrument_booking.app.notifier import QtNotifier
from instrument_booking.app.single_instance import SingleInstance
from instrument_booking.service.container import build_services
from instrument_booking.service.housekeeping import LOGGER_NAME, describe_error

INSTANCE_KEY = "InstrumentBooking.single-instance"

log = logging.getLogger(LOGGER_NAME)


def _log_uncaught(exc_type, exc, tb) -> None:
    log.critical("未處理的例外", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


def main(argv: list[str] | None = None, *, instance_key: str = INSTANCE_KEY, data_dir: Path | None = None,
         profile_dir: Path | None = None, session_factory=None,
         show_error: Callable[[str, str], None] | None = None) -> int:
    """回傳結束代碼；已有實例在執行時通知它顯示視窗並回傳 0。測試可指定資料夾與假的瀏覽器。啟動失敗時顯示錯誤訊息。"""
    if show_error is None:
        show_error = lambda title, msg: QMessageBox.critical(None, title, msg)
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance()
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)  # 關閉視窗後仍在系統匣執行

    instance = SingleInstance(instance_key)
    if not instance.acquire():
        return 0

    sys.excepthook = _log_uncaught
    services = None
    window = None
    stop = threading.Event()
    try:
        notifier = QtNotifier()
        extra = {} if session_factory is None else {"session_factory": session_factory}
        services = build_services(notifier, data_dir=data_dir, profile_dir=profile_dir, **extra)
        window = MainWindow(services, notifier, stop=stop, autostart_command=launch_command(),
                            autostart_registry=RunKey())
        instance.activated.connect(window.show_window)
        threading.Thread(target=services.automation.run_forever, args=(stop,), name="automation", daemon=True).start()
        background = BACKGROUND_ARG in argv
        log.info("App 啟動%s", "（背景）" if background else "")
        if not background:
            window.show()
        try:
            return app.exec()
        finally:
            # 任何方式結束（含 Windows 登出）都先停止自動化，再關閉瀏覽器；已關閉時立即返回
            stop.set()
            try:
                if services is not None:
                    services.shutdown()
            except Exception:
                log.exception("關閉服務時發生錯誤")
            try:
                instance.release()
            except Exception:
                log.exception("釋放單一執行個體時發生錯誤")
            if window is not None:
                window.deleteLater()
            log.info("App 結束")
    except Exception as e:
        log.exception("App 啟動失敗")
        show_error(f"{APP_TITLE} 無法啟動", describe_error(e))
        try:
            stop.set()
        except Exception:
            log.exception("設定停止事件時發生錯誤")
        try:
            if services is not None:
                services.shutdown()
        except Exception:
            log.exception("關閉服務時發生錯誤")
        try:
            instance.release()
        except Exception:
            log.exception("釋放單一執行個體時發生錯誤")
        return 1
