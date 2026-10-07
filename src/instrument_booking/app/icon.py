"""App 與系統匣圖示（以程式繪製，不需要圖檔）：藍色圓角方塊上的白色月曆。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

ACCENT = "#0F6CBD"


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 32
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(QRectF(1 * s, 1 * s, 30 * s, 30 * s), 7 * s, 7 * s)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(7 * s, 8 * s, 18 * s, 17 * s), 2.5 * s, 2.5 * s)
    p.setBrush(QColor(ACCENT))
    p.drawRect(QRectF(7 * s, 12 * s, 18 * s, 1.5 * s))
    for row in range(2):
        for col in range(3):
            p.drawRect(QRectF((10 + col * 4.5) * s, (16 + row * 4.5) * s, 2.5 * s, 2.5 * s))
    p.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 256):
        icon.addPixmap(_draw(size))
    return icon
