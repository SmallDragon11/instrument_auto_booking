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
