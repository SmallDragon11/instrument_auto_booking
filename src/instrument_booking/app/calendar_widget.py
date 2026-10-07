"""週曆格元件：拖曳新增時段、點區塊開選單、斜線表示已被佔用（規格 §8.1）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme, themeColor

from instrument_booking.app.calendar_geometry import DAYS, CalendarGeometry
from instrument_booking.app.week_model import WEEKDAY_NAMES, drag_hours, layout_lanes
from instrument_booking.core.models import FIRST_HOUR, LAST_HOUR, BookingRequest


@dataclass(frozen=True)
class _Block:
    request: BookingRequest
    priority: int
    rect: QRectF


def text_color_for(hex_color: str) -> QColor:
    """依底色亮度選黑字或白字。"""
    c = QColor("#" + hex_color)
    luminance = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor(0, 0, 0) if luminance > 150 else QColor(255, 255, 255)


class WeekCalendar(QWidget):
    """顯示「一個儀器」一週的預約。rangeSelected(第幾天, 起始小時, 結束小時)；blockClicked(預約 id, 全域座標)。"""

    rangeSelected = Signal(int, int, int)
    blockClicked = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(56 + DAYS * 72, 44 + (LAST_HOUR - FIRST_HOUR) * 26)
        self.setMouseTracking(True)
        self._monday = date.today()
        self._requests: list[BookingRequest] = []
        self._priorities: dict[str, int] = {}
        self._occupied: set[tuple[int, int]] = set()
        self._unavailable: set[int] = set()
        self._drag: tuple[int, int, int] | None = None  # (第幾天, 起點小時, 目前小時)

    # --- 資料 ---
    def set_week(self, monday: date) -> None:
        self._monday = monday
        self.update()

    def set_bookings(self, requests: list[BookingRequest], priorities: dict[str, int]) -> None:
        self._requests = list(requests)
        self._priorities = dict(priorities)
        self.update()

    def set_occupancy(self, occupied: set[tuple[int, int]], unavailable: set[int]) -> None:
        """occupied：(第幾天, 小時)；unavailable：找不到工作表的第幾天。"""
        self._occupied = set(occupied)
        self._unavailable = set(unavailable)
        self.update()

    def geometry_model(self) -> CalendarGeometry:
        return CalendarGeometry(self.width(), self.height())

    def blocks(self) -> list[_Block]:
        geo = self.geometry_model()
        lanes = layout_lanes(self._requests)
        result = []
        for r in self._requests:
            day = (r.date - self._monday).days
            if not 0 <= day < DAYS:
                continue
            lane, count = lanes[r.id]
            result.append(_Block(r, self._priorities.get(r.id, 0),
                                 QRectF(*geo.block_rect(day, r.start_hour, r.end_hour, lane, count))))
        return result

    def block_at(self, x: float, y: float) -> BookingRequest | None:
        for block in reversed(self.blocks()):
            if block.rect.contains(x, y):
                return block.request
        return None

    # --- 滑鼠 ---
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        pos = event.position()
        hit = self.block_at(pos.x(), pos.y())
        if hit is not None:
            self.blockClicked.emit(hit.id, event.globalPosition().toPoint())
            return
        cell = self.geometry_model().cell_at(pos.x(), pos.y())
        if cell is not None:
            self._drag = (cell[0], cell[1], cell[1])
            self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None:
            day, start, _ = self._drag
            self._drag = (day, start, self.geometry_model().hour_at(pos.y()))
            self.update()
            return
        over_block = self.isEnabled() and self.block_at(pos.x(), pos.y()) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if over_block else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None or event.button() != Qt.MouseButton.LeftButton:
            return
        day, a, b = self._drag
        self._drag = None
        self.update()
        start, end = drag_hours(a, b)
        self.rangeSelected.emit(day, start, end)

    # --- 繪製 ---
    def paintEvent(self, event) -> None:
        geo = self.geometry_model()
        dark = isDarkTheme()
        text = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
        muted = QColor(text)
        muted.setAlpha(140)
        grid = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 28)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.55)

        font = QFont(self.font())
        font.setPixelSize(12)
        p.setFont(font)

        # 日期列與無法預約的日期
        for day in range(DAYS):
            d = self._monday + timedelta(days=day)
            x, y, w, h = geo.column_rect(day)
            if day in self._unavailable:
                p.fillRect(QRectF(x, y, w, h), QColor(128, 128, 128, 40))
            header = QRectF(x, 0, w, geo.header_height)
            p.setPen(muted if day in self._unavailable else text)
            label = f"{WEEKDAY_NAMES[day]}\n{d.month}/{d.day}"
            if day in self._unavailable:
                label = f"{WEEKDAY_NAMES[day]}（無表）\n{d.month}/{d.day}"
            p.drawText(header, Qt.AlignmentFlag.AlignCenter, label)

        # 時間標籤與格線
        p.setPen(QPen(grid, 1))
        for row in range(LAST_HOUR - FIRST_HOUR + 1):
            y = geo.header_height + row * geo.hour_height
            p.drawLine(int(geo.time_width), int(y), self.width(), int(y))
        for day in range(DAYS + 1):
            x = geo.time_width + day * geo.day_width
            p.drawLine(int(x), int(geo.header_height), int(x), self.height())
        p.setPen(muted)
        for hour in range(FIRST_HOUR, LAST_HOUR):
            y = geo.header_height + (hour - FIRST_HOUR) * geo.hour_height
            p.drawText(QRectF(0, y, geo.time_width - 8, geo.hour_height),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{hour:02d}:00")

        # 已被佔用（斜線）
        hatch = QBrush(QColor(150, 150, 150, 160), Qt.BrushStyle.BDiagPattern)
        for day, hour in self._occupied:
            p.fillRect(QRectF(*geo.cell_rect(day, hour)), hatch)

        # 預約區塊
        for block in self.blocks():
            color = QColor("#" + block.request.color)
            p.setPen(QPen(color.darker(140), 1))
            p.setBrush(color)
            p.drawRoundedRect(block.rect, 4, 4)
            p.setPen(text_color_for(block.request.color))
            label = f"#{block.priority}"
            if block.request.gas:
                label += f" {block.request.gas}"
            p.drawText(block.rect.adjusted(4, 2, -4, -2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, label)

        # 拖曳中的預覽
        if self._drag is not None:
            day, a, b = self._drag
            start, end = drag_hours(a, b)
            accent = QColor(themeColor())
            fill = QColor(accent)
            fill.setAlpha(90)
            p.setPen(QPen(accent, 2))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(*geo.block_rect(day, start, end)), 4, 4)
        p.end()
