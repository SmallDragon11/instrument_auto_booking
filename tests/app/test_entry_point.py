import logging
import sys
import uuid

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from instrument_booking.app.main import main
from instrument_booking.app.main_window import MainWindow
from instrument_booking.app.single_instance import SingleInstance
from instrument_booking.service.housekeeping import LOGGER_NAME
from service.fakes import FakeSession


@pytest.fixture(autouse=True)
def clean_logger():
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def test_background_start_shows_only_tray_and_shuts_down_on_quit(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # main 會替換，測試後還原
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    seen = {}

    def inspect_then_quit():
        windows = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)]
        seen["visible"] = [w.isVisible() for w in windows]
        seen["second"] = SingleInstance(key).acquire()  # 第二個實例：通知後結束
        QApplication.quit()
    QTimer.singleShot(1500, inspect_then_quit)
    code = main(["app", "--background"], instance_key=key, data_dir=tmp_path / "data",
                profile_dir=tmp_path / "profile", session_factory=lambda url, profile: FakeSession([]))
    assert code == 0
    assert seen == {"visible": [False], "second": False}
    assert (tmp_path / "data" / "logs" / "app.log").exists()
    assert SingleInstance(key).acquire()  # 結束時已釋放


def test_startup_failure_is_shown_and_releases_instance(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # main 會替換，測試後還原
    key = f"InstrumentBooking.test.{uuid.uuid4().hex}"
    errors = []
    # 建立一個檔案而非目錄，讓 build_services 建立日誌資料夾時失敗
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x")
    code = main(["app"], instance_key=key, data_dir=not_a_dir / "data",
                profile_dir=tmp_path / "profile", session_factory=lambda url, profile: FakeSession([]),
                show_error=lambda title, msg: errors.append((title, msg)))
    assert code == 1
    assert len(errors) == 1
    assert "無法啟動" in errors[0][0]
    assert SingleInstance(key).acquire()  # 結束時已釋放


def test_self_check_writes_result_without_starting_the_app(tmp_path):
    out = tmp_path / "result.txt"
    assert main(["app", "--self-check", str(out)], instance_key="unused",
                self_check=lambda: (True, "Edge 自動化正常（版本 1）")) == 0
    assert out.read_text(encoding="utf-8") == "Edge 自動化正常（版本 1）\n"
    assert not (tmp_path / "data").exists()
    assert main(["app", "--self-check"], self_check=lambda: (False, "失敗")) == 1
