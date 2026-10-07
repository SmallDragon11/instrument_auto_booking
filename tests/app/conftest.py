"""GUI 測試在背景執行（不開視窗）；必須在建立 QApplication 之前設定。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
