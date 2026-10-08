import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.browser.downloader import DownloadError
from instrument_booking.browser.session import NotLoggedIn, SessionError
from instrument_booking.service.runner import RunnerError
from instrument_booking.service.housekeeping import (
    LOGGER_NAME,
    describe_error,
    prune_snapshots,
    setup_logging,
)


@pytest.mark.parametrize("error,expected", [
    (NotLoggedIn("x"), "重新登入"),
    (SessionError("試算表在 60 秒內沒有載入完成"), "60 秒"),
    (DownloadError("下載快照失敗：HTTP 403"), "HTTP 403"),
    (PlaywrightTimeoutError("Timeout 3000ms exceeded"), "逾時"),
    (PlaywrightError("Target closed"), "瀏覽器發生錯誤"),
    (OSError("連線被拒"), "連線被拒"),
    (KeyError("boom"), "未預期的錯誤：KeyError"),
])
def test_describe_error(error, expected):
    assert expected in describe_error(error)


def test_not_logged_in_tells_user_how_to_log_in_again():
    assert describe_error(NotLoggedIn("x")) == (
        "需要重新登入 Google：請到「設定」頁按「重新登入」，登入完成後關閉該 Edge 視窗")


def test_describe_runner_error_shows_message_as_is():
    msg = "預約表網址在預檢之後被改變，為安全起見不寫入"
    assert describe_error(RunnerError(msg)) == msg


@pytest.fixture
def clean_logger():
    logger = logging.getLogger(LOGGER_NAME)
    yield logger
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def test_setup_logging_writes_utf8_and_keeps_30_days(tmp_path, clean_logger):
    logger = setup_logging(tmp_path / "logs")
    setup_logging(tmp_path / "logs")  # 重複呼叫不加第二個 handler
    handlers = [h for h in logger.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(handlers) == 1 and handlers[0].backupCount == 30
    logger.info("預約完成")
    handlers[0].flush()
    assert "預約完成" in (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")


def test_prune_snapshots_keeps_newest(tmp_path):
    for i in range(7):
        p = tmp_path / f"snapshot-{i}.xlsx"
        p.write_bytes(b"PK")
        os.utime(p, (1000 + i, 1000 + i))
    (tmp_path / "other.txt").write_text("keep")
    prune_snapshots(tmp_path, keep=5)
    assert sorted(p.name for p in tmp_path.glob("snapshot-*.xlsx")) == [f"snapshot-{i}.xlsx" for i in range(2, 7)]
    assert (tmp_path / "other.txt").exists()


def test_prune_snapshots_missing_dir_is_fine(tmp_path):
    prune_snapshots(tmp_path / "nope")


def make_snapshots(tmp_path, n=7):
    for i in range(n):
        p = tmp_path / f"snapshot-{i}.xlsx"
        p.write_bytes(b"PK")
        os.utime(p, (1000 + i, 1000 + i))


def test_prune_snapshots_ignores_snapshot_deleted_while_listing(tmp_path, monkeypatch):
    make_snapshots(tmp_path)
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.name == "snapshot-3.xlsx":
            raise FileNotFoundError(2, "其他執行緒剛刪除", str(self))
        return real_stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    prune_snapshots(tmp_path, keep=5)  # 不可拋出例外（否則成功的預檢會被當成失敗）
    monkeypatch.undo()
    assert not (tmp_path / "snapshot-0.xlsx").exists()
    assert (tmp_path / "snapshot-1.xlsx").exists() and (tmp_path / "snapshot-6.xlsx").exists()


def test_prune_snapshots_ignores_unlink_errors(tmp_path, monkeypatch):
    make_snapshots(tmp_path)

    def unlink(self, *args, **kwargs):
        raise FileNotFoundError(2, "其他執行緒剛刪除", str(self))
    monkeypatch.setattr(Path, "unlink", unlink)
    prune_snapshots(tmp_path, keep=5)


def test_describe_not_configured_shows_message_as_is():
    from instrument_booking.service.settings import NotConfigured
    assert describe_error(NotConfigured("請先完成設定：請填寫要寫入表格的名字")) == "請先完成設定：請填寫要寫入表格的名字"


def test_describe_error_passes_login_blocked_message_through():
    from instrument_booking.service.connection import LoginBlocked
    assert describe_error(LoginBlocked("寫入結束前不能重新登入")) == "寫入結束前不能重新登入"
