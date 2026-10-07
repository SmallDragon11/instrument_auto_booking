"""「下週預約」頁：週曆拖曳新增、點區塊改氣體或刪除、優先序清單、佔用顯示（規格 §8.1）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (Action, BodyLabel, CaptionLabel, ComboBox, FluentIcon, ListWidget, PushButton, RoundMenu,
                            SegmentedWidget, StrongBodyLabel, SubtitleLabel)

from instrument_booking.app.calendar_widget import WeekCalendar
from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.app.texts import CLIPBOARD_HINT, status_line, week_range_label
from instrument_booking.app.week_model import (change_gas, copy_previous_week, describe, make_request, remove,
                                               reorder, slot_warnings)
from instrument_booking.app.widgets import ask, color_icon, set_fluent_tooltip, show_info
from instrument_booking.core.models import BookingRequest, Instrument
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.service.housekeeping import describe_error
from instrument_booking.service.occupancy import WeekView
from instrument_booking.storage.json_store import StoreError

ID_ROLE = Qt.ItemDataRole.UserRole


class WeekPage(QWidget):
    def __init__(self, store, tasks: BackgroundTasks, *, refresh_occupancy: Callable[[date], WeekView],
                 cancel_run: Callable[[], None], confirm: Callable[[QWidget, str, str], bool] = ask,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("weekPage")
        self._store = store
        self._tasks = tasks
        self._refresh_occupancy = refresh_occupancy
        self._cancel_run = cancel_run
        self._confirm = confirm
        self._monday: date | None = None
        self._requests: list[BookingRequest] = []
        self._view: WeekView | None = None
        self._refreshing_for: date | None = None
        self._instrument = Instrument.TUBE_A
        self._locked = False
        self._pending_auto_refresh = False  # 切換週後待自動下載佔用（等 apply_status 確認未鎖定才下載）
        self._load_error: str | None = None
        try:
            self._legend = store.load_legend()
        except StoreError:
            self._legend = {}
        self._build()
        self._fill_gases()

    # --- 版面 ---
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title_label = SubtitleLabel("下週預約")
        self.week_label = BodyLabel("")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.week_label)
        header.addLayout(title_box)
        header.addStretch(1)
        status_box = QVBoxLayout()
        self.status_label = StrongBodyLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hint_label = CaptionLabel(CLIPBOARD_HINT)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_box.addWidget(self.status_label)
        status_box.addWidget(self.hint_label)
        header.addLayout(status_box)
        root.addLayout(header)

        self.lock_bar = QFrame()
        self.lock_bar.setObjectName("lockBar")
        self.lock_bar.setStyleSheet("#lockBar { background: rgba(255, 185, 0, 0.18); border-radius: 6px; }")
        lock_layout = QHBoxLayout(self.lock_bar)
        lock_layout.setContentsMargins(12, 6, 12, 6)
        self.lock_label = BodyLabel("自動預約進行中，這一週的清單暫時不能修改。")
        self.cancel_button = PushButton(FluentIcon.CLOSE, "取消本次預約")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        lock_layout.addWidget(self.lock_label, 1)
        lock_layout.addWidget(self.cancel_button)
        self.lock_bar.hide()
        root.addWidget(self.lock_bar)

        toolbar = QHBoxLayout()
        self.instrument_tabs = SegmentedWidget()
        for inst in Instrument:
            self.instrument_tabs.addItem(inst.name, inst.label, onClick=lambda *_, i=inst: self._set_instrument(i))
        self.instrument_tabs.setCurrentItem(self._instrument.name)
        toolbar.addWidget(self.instrument_tabs)
        toolbar.addSpacing(12)
        self.gas_label = BodyLabel("氣體")
        self.gas_combo = ComboBox()
        self.gas_combo.setMinimumWidth(140)
        toolbar.addWidget(self.gas_label)
        toolbar.addWidget(self.gas_combo)
        toolbar.addStretch(1)
        self.occupancy_label = CaptionLabel("")
        self.refresh_button = PushButton(FluentIcon.SYNC, "重新整理")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.occupancy_label)
        toolbar.addWidget(self.refresh_button)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        self.calendar = WeekCalendar()
        self.calendar.rangeSelected.connect(self._on_range_selected)
        self.calendar.blockClicked.connect(self._on_block_clicked)
        body.addWidget(self.calendar, 3)

        side = QVBoxLayout()
        side.addWidget(StrongBodyLabel("優先序（拖曳調整，第 1 筆最先搶）"))
        self.priority_list = ListWidget()
        self.priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.priority_list.setMinimumWidth(300)
        self.priority_list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._on_rows_moved))
        self.priority_list.itemClicked.connect(self._on_item_clicked)
        side.addWidget(self.priority_list, 1)
        buttons = QHBoxLayout()
        self.delete_button = PushButton(FluentIcon.DELETE, "刪除所選")
        self.delete_button.clicked.connect(self._delete_selected)
        self.copy_button = PushButton(FluentIcon.COPY, "複製上週清單")
        set_fluent_tooltip(self.copy_button, "把上週的預約日期 +7 天加進這週")
        self.copy_button.clicked.connect(self._copy_previous_week)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.copy_button)
        side.addLayout(buttons)
        body.addLayout(side, 1)
        root.addLayout(body, 1)

    # --- 由主視窗呼叫 ---
    @property
    def monday(self) -> date | None:
        return self._monday

    @property
    def requests(self) -> list[BookingRequest]:
        return list(self._requests)

    def set_week(self, monday: date) -> None:
        """切換到另一個目標週：讀取該週清單；佔用的自動下載交給 apply_status，確認未鎖定、狀態有效後才進行。"""
        if monday == self._monday:
            return
        self._monday = monday
        self._view = None
        self._load_error = None
        self.week_label.setText(f"目標週：{week_range_label(monday)}")
        self.calendar.set_week(monday)
        try:
            self._requests = self._store.load_bookings(monday)
        except StoreError as e:
            self._requests = []
            self._load_error = str(e)
            show_info(self, "error", "無法讀取預約清單", str(e), duration=-1)
        self._refresh_views()
        self._pending_auto_refresh = True

    def apply_status(self, status: ServiceStatus, now: datetime) -> None:
        """每秒由主視窗呼叫：狀態文字、編輯鎖定、取消按鈕。"""
        self.status_label.setText(status_line(status, now))
        locked = status.editing_locked and status.target_monday == self._monday
        can_cancel = locked and status.run_at is not None and now < status.run_at and \
            status.phase is not ServicePhase.DONE
        self.cancel_button.setVisible(can_cancel)
        if locked != self._locked:
            self._locked = locked
            self.lock_bar.setVisible(locked)
            for w in (self.calendar, self.priority_list, self.delete_button, self.copy_button, self.refresh_button,
                      self.gas_combo):
                w.setEnabled(not locked)
            if not locked:
                self._pending_auto_refresh = False
                self.refresh()
        # 服務尚未跑過第一次 step（沒有 run_at 也不是未設定）時鎖定狀態不可信，先不下載，避免排進鎖定期間
        if (self._pending_auto_refresh and not locked
                and (status.run_at is not None or status.phase is ServicePhase.NOT_CONFIGURED)):
            self._pending_auto_refresh = False
            self.refresh()

    def refresh(self) -> None:
        """在背景重新下載預約表並更新佔用與氣體選項；失敗只顯示提示，不影響編輯。"""
        if self._monday is None or self._locked or self._refreshing_for == self._monday:
            return
        monday = self._monday
        self._refreshing_for = monday
        self.refresh_button.setEnabled(False)
        self.occupancy_label.setText("正在下載預約表…")
        self._tasks.run(lambda: self._refresh_occupancy(monday),
                        lambda view: self._on_refreshed(monday, view),
                        lambda e: self._on_refresh_failed(monday, e))

    # --- 佔用 ---
    def _on_refreshed(self, monday: date, view: WeekView) -> None:
        self._refresh_done(monday)
        if monday != self._monday:
            return  # 下載期間已切換到別週
        self._view = view
        if view.legend:
            self._legend = dict(view.legend)
            self._fill_gases()
        self.occupancy_label.setText(f"佔用資料更新於 {datetime.now():%H:%M}")
        self._refresh_views()

    def _on_refresh_failed(self, monday: date, error: Exception) -> None:
        self._refresh_done(monday)
        if monday == self._monday:
            self.occupancy_label.setText(f"無法更新佔用：{describe_error(error)}")

    def _refresh_done(self, monday: date) -> None:
        if self._refreshing_for == monday:
            self._refreshing_for = None
        self.refresh_button.setEnabled(not self._locked and self._refreshing_for is None)

    # --- 儀器與氣體 ---
    def _set_instrument(self, instrument: Instrument) -> None:
        self._instrument = instrument
        self.instrument_tabs.setCurrentItem(instrument.name)
        is_tube = instrument.kind == "tube"
        self.gas_label.setVisible(is_tube)
        self.gas_combo.setVisible(is_tube)
        self._refresh_views()

    def _fill_gases(self) -> None:
        current = self.gas_combo.currentText()
        self.gas_combo.clear()
        for name, color in self._legend.items():
            self.gas_combo.addItem(name, color_icon(color))
        if not self._legend:
            self.gas_combo.setPlaceholderText("沒有氣體圖例，請按重新整理")
        elif current in self._legend:
            self.gas_combo.setCurrentText(current)

    # --- 編輯 ---
    def _commit(self, requests: list[BookingRequest]) -> bool:
        """存檔成功才更新畫面；失敗時保留原本的清單。"""
        if self._locked or self._monday is None or self._load_error is not None:
            self._refresh_views()
            if self._load_error is not None:
                show_info(self, "error", "無法修改預約清單", "這週的預約清單讀取失敗，為避免覆蓋原本的檔案，暫時不能修改：" + self._load_error)
            return False
        try:
            self._store.save_bookings(self._monday, requests)
        except StoreError as e:
            show_info(self, "error", "無法儲存預約清單", str(e))
            self._refresh_views()
            return False
        self._requests = list(requests)
        self._refresh_views()
        return True

    def _on_range_selected(self, day: int, start: int, end: int) -> None:
        if self._locked or self._monday is None:
            return
        gas = self.gas_combo.currentText() if self._instrument.kind == "tube" else None
        try:
            request = make_request(self._instrument, self._monday + timedelta(days=day), start, end, gas,
                                   self._legend)
        except ValueError as e:
            show_info(self, "warning", "無法新增", str(e))
            return
        if not self._commit(self._requests + [request]):
            return
        warnings = slot_warnings(request, self._view)
        if warnings:
            show_info(self, "warning", "已加入，但請注意", "\n".join(warnings), duration=6000)

    def _on_block_clicked(self, request_id: str, pos) -> None:
        if self._locked:
            return
        request = next((r for r in self._requests if r.id == request_id), None)
        if request is None:
            return
        menu = RoundMenu(parent=self)
        if request.instrument.kind == "tube" and self._legend:
            gas_menu = RoundMenu("改用氣體", self)
            gas_menu.setIcon(FluentIcon.PALETTE)
            for name, color in self._legend.items():
                action = Action(color_icon(color), name, self)
                action.triggered.connect(lambda _=False, n=name: self._change_gas(request_id, n))
                gas_menu.addAction(action)
            menu.addMenu(gas_menu)
        delete = Action(FluentIcon.DELETE, "刪除", self)
        delete.triggered.connect(lambda: self._commit(remove(self._requests, request_id)))
        menu.addAction(delete)
        menu.exec(pos)

    def _change_gas(self, request_id: str, gas: str) -> None:
        self._commit(change_gas(self._requests, request_id, gas, self._legend))

    def _on_rows_moved(self) -> None:
        ids = [self.priority_list.item(i).data(ID_ROLE) for i in range(self.priority_list.count())]
        try:
            self._commit(reorder(self._requests, ids))
        except ValueError:
            self._refresh_views()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        request = next((r for r in self._requests if r.id == item.data(ID_ROLE)), None)
        if request is not None and request.instrument != self._instrument:
            self._set_instrument(request.instrument)

    def _delete_selected(self) -> None:
        item = self.priority_list.currentItem()
        if item is None or self._locked:
            return
        self._commit(remove(self._requests, item.data(ID_ROLE)))

    def _copy_previous_week(self) -> None:
        if self._monday is None or self._locked:
            return
        try:
            previous = self._store.load_bookings(self._monday - timedelta(days=7))
        except StoreError as e:
            show_info(self, "error", "無法讀取上週清單", str(e))
            return
        combined, added = copy_previous_week(previous, self._requests)
        if not added:
            show_info(self, "info", "沒有可複製的預約", "上週清單是空的，或這週已有相同的時段")
            return
        if self._commit(combined):
            show_info(self, "success", f"已加入上週的 {added} 筆預約")

    def _on_cancel_clicked(self) -> None:
        if self._confirm(self, "取消本次自動預約？", "取消後這一週不會再自動預約，需要時請自己到表單上預約。"):
            self._cancel_run()
            show_info(self, "info", "已要求取消", "開放時間前會停止，不會寫入任何東西")

    # --- 畫面 ---
    def _refresh_views(self) -> None:
        priorities = {r.id: i for i, r in enumerate(self._requests, start=1)}
        self.calendar.set_bookings([r for r in self._requests if r.instrument == self._instrument], priorities)
        if self._view is not None and self._monday is not None:
            occupied = {((d - self._monday).days, h) for inst, d, h in self._view.occupied
                        if inst == self._instrument and 0 <= (d - self._monday).days < 7}
            unavailable = {(d - self._monday).days for kind, d in self._view.unavailable
                           if kind == self._instrument.kind and 0 <= (d - self._monday).days < 7}
            self.calendar.set_occupancy(occupied, unavailable)
        else:
            self.calendar.set_occupancy(set(), set())
        self.priority_list.blockSignals(True)
        self.priority_list.clear()
        for i, r in enumerate(self._requests, start=1):
            item = QListWidgetItem(color_icon(r.color), f"{i}. {describe(r)}")
            item.setData(ID_ROLE, r.id)
            self.priority_list.addItem(item)
        self.priority_list.blockSignals(False)
