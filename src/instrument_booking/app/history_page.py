"""「執行紀錄」頁：每次執行一張卡片（規格 §8.2）。一律讀 load_runs()（服務的 DONE 狀態只維持約 1 秒）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon, PushButton, ScrollArea, SimpleCardWidget,
                            StrongBodyLabel, SubtitleLabel)

from instrument_booking.app.tasks import BackgroundTasks
from instrument_booking.app.texts import result_rows, run_meta, run_title
from instrument_booking.service.housekeeping import describe_error
from instrument_booking.storage.json_store import RunRecord

MAX_CARDS = 30
KIND_COLOR = {"success": "#0F7B0F", "skipped": "#9D5D00", "warning": "#C42B1C", "failed": "#C42B1C", "none": ""}


class RunCard(SimpleCardWidget):
    def __init__(self, record: RunRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.title_label = StrongBodyLabel(run_title(record))
        layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel("　".join(run_meta(record)))
        layout.addWidget(self.meta_label)
        if record.error:
            self.error_label = BodyLabel(f"未完成：{record.error}")
            self.error_label.setWordWrap(True)
            self.error_label.setTextColor("#C42B1C", "#FF99A4")
            layout.addWidget(self.error_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        self.rows = result_rows(record)
        for i, row in enumerate(self.rows):
            grid.addWidget(BodyLabel(f"{row.priority}."), i, 0)
            grid.addWidget(BodyLabel(row.text), i, 1)
            status = BodyLabel(row.status)
            if KIND_COLOR[row.kind]:
                status.setTextColor(KIND_COLOR[row.kind], KIND_COLOR[row.kind])
            grid.addWidget(status, i, 2)
            detail = CaptionLabel(row.detail)
            detail.setWordWrap(True)
            grid.addWidget(detail, i, 3)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)


class HistoryPage(QWidget):
    def __init__(self, store, tasks: BackgroundTasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        self._store = store
        self._tasks = tasks
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("執行紀錄"))
        header.addStretch(1)
        self.reload_button = PushButton(FluentIcon.SYNC, "重新整理")
        self.reload_button.clicked.connect(self.reload)
        header.addWidget(self.reload_button)
        root.addLayout(header)
        self.notice_label = CaptionLabel("")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        root.addWidget(self.notice_label)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._cards = QVBoxLayout(self._container)
        self._cards.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._cards.setSpacing(10)
        self.scroll.setWidget(self._container)
        root.addWidget(self.scroll, 1)
        self.empty_label = BodyLabel("還沒有執行紀錄。")
        self._cards.addWidget(self.empty_label)

    def cards(self) -> list[RunCard]:
        return self._container.findChildren(RunCard)

    def reload(self) -> None:
        """在背景讀取紀錄（會讀檔，不可在 GUI 執行緒）。"""
        if self._loading:
            return
        self._loading = True
        self.reload_button.setEnabled(False)
        self._tasks.run(lambda: (self._store.load_runs(), self._store.corrupt_runs()), self._show, self._failed)

    def _show(self, loaded: tuple[list[RunRecord], list[str]]) -> None:
        self._done()
        runs, corrupt = loaded
        for card in self.cards():
            card.setParent(None)
            card.deleteLater()
        self.empty_label.setVisible(not runs)
        for record in runs[:MAX_CARDS]:
            self._cards.addWidget(RunCard(record, self._container))
        if corrupt:
            self._notice(f"有 {len(corrupt)} 個執行紀錄檔損毀，已略過：{'、'.join(corrupt)}。"
                         "這些週可能會被當成尚未執行。")
        else:
            self.notice_label.hide()

    def _failed(self, error: Exception) -> None:
        self._done()
        self._notice(f"無法讀取執行紀錄：{describe_error(error)}")

    def _done(self) -> None:
        self._loading = False
        self.reload_button.setEnabled(True)

    def _notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.setTextColor("#C42B1C", "#FF99A4")
        self.notice_label.show()
