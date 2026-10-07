"""「設定」頁：名字、預約時間、網址、Google 帳號（重新登入、連線測試）、開機啟動、系統匣、外觀（規格 §8.3）。

安全餘量等進階參數不開放設定，避免誤設造成偷跑。
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Callable

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon, LineEdit, PrimaryPushButton, PushButton,
                            ScrollArea, SimpleCardWidget, StrongBodyLabel, SubtitleLabel, SwitchButton, TimePicker)

from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.app.texts import clock_label
from instrument_booking.app.week_model import WEEKDAY_NAMES
from instrument_booking.app.widgets import ask, set_fluent_tooltip, show_info
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.connection import NOT_SYNCED, ConnectionReport
from instrument_booking.service.housekeeping import describe_error
from instrument_booking.service.settings import THEMES, Settings, validate_settings
from instrument_booking.storage.json_store import StoreError

THEME_LABELS = {"system": "跟隨系統", "light": "淺色", "dark": "深色"}
LOCK_TEXT = "自動預約進行中，暫時不能重新登入或測試連線。"
LOGIN_STARTED = "已開啟 Edge：請登入 Google 並確認看得到預約表，完成後關閉該視窗，再按「連線測試」確認。"


def _card(title: str) -> tuple[SimpleCardWidget, QFormLayout]:
    card = SimpleCardWidget()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 14, 20, 16)
    layout.addWidget(StrongBodyLabel(title))
    form = QFormLayout()
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    layout.addLayout(form)
    return card, form


class SettingsPage(QWidget):
    def __init__(self, store, tasks: BackgroundTasks, *, connection_test: Callable[[], ConnectionReport],
                 start_login: Callable[[], object], on_saved: Callable[[Settings], None],
                 confirm: Callable[[QWidget, str, str], bool] = ask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._store = store
        self._tasks = tasks
        self._connection_test = connection_test
        self._start_login = start_login
        self._on_saved = on_saved
        self._confirm = confirm
        self._locked = False
        self._busy = False
        self._retry_wait = False  # 預檢失敗等待重試中：執行緒已放棄 Edge，可重新登入
        self._build()
        self.load()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)
        root.addWidget(SubtitleLabel("設定"))

        card, form = _card("預約")
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("寫入表格的名字，例如：小融")
        form.addRow(BodyLabel("名字"), self.name_edit)
        self.url_edit = LineEdit()
        self.url_edit.setPlaceholderText("https://docs.google.com/spreadsheets/d/…")
        form.addRow(BodyLabel("預約表網址"), self.url_edit)
        when = QHBoxLayout()
        self.weekday_combo = ComboBox()
        for name in WEEKDAY_NAMES:
            self.weekday_combo.addItem(f"每週{name}")
        self.time_picker = TimePicker()
        when.addWidget(self.weekday_combo)
        when.addWidget(self.time_picker)
        when.addStretch(1)
        form.addRow(BodyLabel("自動預約時間"), when)
        root.addWidget(card)

        card, form = _card("Google 帳號")
        self.account_label = BodyLabel("尚未測試連線")
        self.account_label.setWordWrap(True)
        form.addRow(BodyLabel("狀態"), self.account_label)
        buttons = QHBoxLayout()
        self.login_button = PushButton(FluentIcon.PEOPLE, "重新登入")
        self.login_button.clicked.connect(self.start_login)
        self.test_button = PushButton(FluentIcon.SYNC, "連線測試")
        set_fluent_tooltip(self.test_button, "校時、登入、下載預約表、讀取氣體圖例（不寫入任何東西）")
        self.test_button.clicked.connect(self.run_connection_test)
        buttons.addWidget(self.login_button)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        form.addRow("", buttons)
        self.lock_label = CaptionLabel(LOCK_TEXT)
        self.lock_label.hide()
        form.addRow("", self.lock_label)
        root.addWidget(card)

        card, form = _card("App")
        self.autostart_switch = SwitchButton()
        self.autostart_switch.setOnText("開")
        self.autostart_switch.setOffText("關")
        form.addRow(BodyLabel("開機自動啟動（縮到系統匣）"), self.autostart_switch)
        self.tray_switch = SwitchButton()
        self.tray_switch.setOnText("開")
        self.tray_switch.setOffText("關")
        form.addRow(BodyLabel("關閉視窗時縮到系統匣"), self.tray_switch)
        self.theme_combo = ComboBox()
        self.theme_combo.setMinimumWidth(160)
        for key in THEMES:
            self.theme_combo.addItem(THEME_LABELS[key])
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        form.addRow(BodyLabel("外觀"), theme_row)
        root.addWidget(card)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_button = PrimaryPushButton(FluentIcon.SAVE, "儲存設定")
        self.save_button.clicked.connect(self.save)
        save_row.addWidget(self.save_button)
        root.addLayout(save_row)
        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    # --- 讀寫設定 ---
    def load(self) -> None:
        try:
            s = self._store.load_settings()
        except StoreError as e:
            s = Settings()
            show_info(self, "error", "設定檔損毀，已顯示預設值", str(e), duration=-1)
        self.name_edit.setText(s.name)
        self.url_edit.setText(s.spreadsheet_url)
        self.weekday_combo.setCurrentIndex(s.run_weekday if 0 <= s.run_weekday <= 6 else Settings().run_weekday)
        self.time_picker.setTime(QTime(s.run_time.hour, s.run_time.minute))
        self.autostart_switch.setChecked(s.autostart)
        self.tray_switch.setChecked(s.minimize_to_tray)
        self.theme_combo.setCurrentIndex(THEMES.index(s.theme) if s.theme in THEMES else 0)

    def current(self) -> Settings:
        t = self.time_picker.time
        return Settings(name=self.name_edit.text().strip(), spreadsheet_url=self.url_edit.text().strip(),
                        run_weekday=self.weekday_combo.currentIndex(), run_time=time(t.hour(), t.minute()),
                        autostart=self.autostart_switch.isChecked(), minimize_to_tray=self.tray_switch.isChecked(),
                        theme=THEMES[self.theme_combo.currentIndex()])

    def save(self) -> bool:
        new = self.current()
        problems = validate_settings(new)
        if problems:
            show_info(self, "warning", "設定尚未儲存", "\n".join(problems), duration=6000)
            return False
        try:
            old = self._store.load_settings()
        except StoreError:
            old = None
        changes_run = old is None or (old.run_weekday, old.run_time, old.spreadsheet_url) != \
            (new.run_weekday, new.run_time, new.spreadsheet_url)
        if self._locked and changes_run and not self._confirm(
                self, "自動預約進行中", "現在修改預約時間或網址，本次自動預約會取消並改依新設定執行。確定要儲存嗎？"):
            return False
        try:
            self._store.save_settings(new)
        except StoreError as e:
            show_info(self, "error", "無法儲存設定", str(e))
            return False
        self._on_saved(new)
        show_info(self, "success", "設定已儲存")
        return True

    # --- 狀態 ---
    def apply_status(self, status: ServiceStatus) -> None:
        self._locked = status.editing_locked
        self._retry_wait = status.editing_locked and status.phase is ServicePhase.RETRY_WAIT
        if self._retry_wait:
            until = f" {status.retry_at:%H:%M} 前" if status.retry_at is not None else "下次重試前"
            self.lock_label.setText(f"預檢失敗，等待重試中：如需重新登入，請在{until}登入完成並關閉該 Edge 視窗。")
        else:
            self.lock_label.setText(LOCK_TEXT)
        self.lock_label.setVisible(self._locked)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.login_button.setEnabled(self._login_allowed())
        self.test_button.setEnabled(not self._locked and not self._busy)

    def _login_allowed(self) -> bool:
        return (not self._locked or self._retry_wait) and not self._busy

    def _has_unsaved_changes(self) -> bool:
        """檢查設定是否有未儲存的變更。"""
        try:
            saved = self._store.load_settings()
        except StoreError:
            return True
        return self.current() != saved

    # --- Google 帳號 ---
    def start_login(self) -> None:
        if not self._login_allowed():
            return
        if self._has_unsaved_changes():
            show_info(self, "warning", "請先儲存設定", "設定有尚未儲存的變更，請先按「儲存設定」再測試連線或重新登入。")
            return
        self._set_busy(True)
        self._tasks.run(self._start_login, self._login_started, self._login_failed)

    def _login_started(self, _result) -> None:
        self._set_busy(False)
        show_info(self, "info", "請在 Edge 視窗登入", LOGIN_STARTED, duration=10000)

    def _login_failed(self, error: Exception) -> None:
        self._set_busy(False)
        show_info(self, "error", "無法開啟登入視窗", describe_error(error), duration=8000)

    def run_connection_test(self) -> None:
        if self._locked or self._busy:
            return
        if self._has_unsaved_changes():
            show_info(self, "warning", "請先儲存設定", "設定有尚未儲存的變更，請先按「儲存設定」再測試連線或重新登入。")
            return
        self._set_busy(True)
        self.account_label.setText("測試中：校時、開啟試算表、下載預約表…")
        self._tasks.run(self._connection_test, self._tested, self._test_crashed)

    def _tested(self, report: ConnectionReport) -> None:
        self._set_busy(False)
        stamp = f"{datetime.now():%H:%M} "
        detail = report.message
        if report.clock_source != NOT_SYNCED:
            detail += f"（{clock_label(report.clock_source, report.clock_diff)}）"
        self.account_label.setText(stamp + ("✅ " if report.ok else "❌ ") + detail)
        show_info(self, "success" if report.ok else "error", "連線正常" if report.ok else "連線測試失敗",
                  report.message, duration=6000)

    def _test_crashed(self, error: Exception) -> None:
        self._set_busy(False)
        self.account_label.setText(f"❌ {describe_error(error)}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()
