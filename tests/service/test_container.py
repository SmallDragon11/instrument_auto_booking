import logging

import pytest

from instrument_booking.service.container import build_services
from instrument_booking.service.housekeeping import LOGGER_NAME
from instrument_booking.service.settings import Settings
from service.fakes import FakeSession

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"


class Notes:
    def notify(self, title, message):
        pass


@pytest.fixture(autouse=True)
def clean_logger():
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def test_build_services_wires_everything_without_starting_browser(tmp_path):
    created = []

    def factory(url, profile_dir):
        created.append((url, profile_dir))
        return FakeSession([])
    services = build_services(Notes(), data_dir=tmp_path / "data", profile_dir=tmp_path / "profile",
                              session_factory=factory)
    try:
        assert created == []  # 尚未開啟瀏覽器
        assert (tmp_path / "data" / "logs").is_dir()
        services.store.save_settings(Settings(name="Zoe", spreadsheet_url=URL))
        services.worker.submit(lambda s: None).result()
        assert created == [(URL, tmp_path / "profile")]  # 開啟時讀取最新的網址設定
        assert services.snapshot_dir == tmp_path / "data" / "snapshots"
    finally:
        services.shutdown()
