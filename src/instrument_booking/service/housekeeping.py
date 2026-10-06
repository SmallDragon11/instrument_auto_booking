"""錯誤訊息翻譯、紀錄檔與快照清理（規格 §9）。"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.browser.downloader import DownloadError
from instrument_booking.browser.session import NotLoggedIn, SessionError
from instrument_booking.core.job import WriterCrashed, WriterError
from instrument_booking.storage.json_store import StoreError

LOGGER_NAME = "instrument_booking"
LOG_KEEP_DAYS = 30
SNAPSHOT_KEEP = 5


def describe_error(e: BaseException) -> str:
    """把例外轉成給使用者看的繁體中文說明。"""
    if isinstance(e, NotLoggedIn):
        return "需要重新登入 Google：請到「設定」頁按「重新登入」"
    if isinstance(e, (SessionError, DownloadError, StoreError, WriterError, WriterCrashed)):
        return str(e)
    if isinstance(e, PlaywrightTimeoutError):
        return "瀏覽器操作逾時，請確認網路連線"
    if isinstance(e, PlaywrightError):
        return "瀏覽器發生錯誤，請稍後再試"
    if isinstance(e, OSError):
        return f"網路或檔案錯誤：{e}"
    return f"未預期的錯誤：{type(e).__name__}：{e}"


def setup_logging(log_dir: Path) -> logging.Logger:
    """紀錄檔每天一個，保留 30 天；重複呼叫不會重複加入 handler。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    target = (log_dir / "app.log").resolve()
    for h in logger.handlers:
        if isinstance(h, TimedRotatingFileHandler) and Path(h.baseFilename).resolve() == target:
            return logger
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(target, when="midnight", backupCount=LOG_KEEP_DAYS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger


def prune_snapshots(snapshot_dir: Path, keep: int = SNAPSHOT_KEEP) -> None:
    """只保留最新的 keep 個快照（每個約 3.4 MB）。"""
    if not snapshot_dir.is_dir():
        return
    snapshots = sorted(snapshot_dir.glob("snapshot-*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snapshots[keep:]:
        try:
            old.unlink()
        except OSError:
            pass  # 檔案正被開啟時下次再清
