"""週曆格的座標換算（純函式，不依賴 Qt）：7 天 × 15 個時段（09:00–24:00）。"""
from __future__ import annotations

from dataclasses import dataclass

from instrument_booking.core.models import FIRST_HOUR, LAST_HOUR

ROWS = LAST_HOUR - FIRST_HOUR  # 15
DAYS = 7


@dataclass(frozen=True)
class CalendarGeometry:
    width: float
    height: float
    time_width: float = 56.0      # 左側時間標籤欄
    header_height: float = 44.0   # 上方日期列
    gap: float = 2.0              # 區塊與格線的間距

    @property
    def day_width(self) -> float:
        return max(self.width - self.time_width, 0.0) / DAYS

    @property
    def hour_height(self) -> float:
        return max(self.height - self.header_height, 0.0) / ROWS

    def cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        """(第幾天 0–6, 起始小時 9–23)；不在格子區域內時為 None。"""
        if x < self.time_width or y < self.header_height or self.day_width <= 0 or self.hour_height <= 0:
            return None
        day = int((x - self.time_width) // self.day_width)
        row = int((y - self.header_height) // self.hour_height)
        if not (0 <= day < DAYS and 0 <= row < ROWS):
            return None
        return day, FIRST_HOUR + row

    def hour_at(self, y: float) -> int:
        """拖曳時的小時：超出上下邊界時取最近的一格。"""
        if self.hour_height <= 0:
            return FIRST_HOUR
        row = int((y - self.header_height) // self.hour_height)
        return FIRST_HOUR + min(max(row, 0), ROWS - 1)

    def column_rect(self, day: int) -> tuple[float, float, float, float]:
        return (self.time_width + day * self.day_width, self.header_height, self.day_width, ROWS * self.hour_height)

    def cell_rect(self, day: int, hour: int) -> tuple[float, float, float, float]:
        return (self.time_width + day * self.day_width, self.header_height + (hour - FIRST_HOUR) * self.hour_height,
                self.day_width, self.hour_height)

    def block_rect(self, day: int, start_hour: int, end_hour: int, lane: int = 0,
                   lanes: int = 1) -> tuple[float, float, float, float]:
        """(x, y, 寬, 高)；並排時每欄平分當天的寬度，四周留 gap。"""
        lane_width = self.day_width / lanes
        x = self.time_width + day * self.day_width + lane * lane_width
        y = self.header_height + (start_hour - FIRST_HOUR) * self.hour_height
        height = (end_hour - start_hour) * self.hour_height
        return (x + self.gap, y + self.gap, max(lane_width - 2 * self.gap, 1.0), max(height - 2 * self.gap, 1.0))
