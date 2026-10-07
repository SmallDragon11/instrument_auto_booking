"""頁面共用的小工具：色塊圖示、確認對話框、通知條。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition, MessageBox


def color_icon(hex_color: str, size: int = 14) -> QIcon:
    """圓角色塊（氣體、烘箱顏色）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#" + hex_color)
    p.setPen(color.darker(140))
    p.setBrush(color)
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pixmap)


def ask(parent: QWidget, title: str, content: str) -> bool:
    """確認對話框：按「確定」回傳 True。"""
    box = MessageBox(title, content, parent.window())
    box.yesButton.setText("確定")
    box.cancelButton.setText("取消")
    return bool(box.exec())


def show_info(parent: QWidget, kind: str, title: str, content: str = "", *, duration: int = 4000) -> InfoBar:
    """kind：success／info／warning／error；duration=-1 表示不自動關閉。"""
    factory = {"success": InfoBar.success, "info": InfoBar.info, "warning": InfoBar.warning,
               "error": InfoBar.error}[kind]
    return factory(title, content, orient=Qt.Orientation.Vertical, isClosable=True, duration=duration,
                   position=InfoBarPosition.TOP_RIGHT, parent=parent)
