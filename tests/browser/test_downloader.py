import pytest

from instrument_booking.browser.downloader import (
    DownloadError,
    SnapshotDownloader,
    export_url,
    file_id_from_url,
)

FILE_ID = "1621E29JjZYnJGj1lnLgJ-GiU_bCVJCdh"


class FakeResponse:
    def __init__(self, status=200, body=b"PK\x03\x04data"):
        self.status, self.ok, self._body = status, 200 <= status < 300, body

    def body(self):
        return self._body


class FakeRequest:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or FakeResponse(), error, []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return self.response


def test_file_id_from_url():
    url = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit?gid=701232019#gid=701232019"
    assert file_id_from_url(url) == FILE_ID
    with pytest.raises(ValueError):
        file_id_from_url("https://example.com/")


def test_export_url():
    assert export_url(FILE_ID) == f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=xlsx"


def test_download_saves_xlsx(tmp_path):
    request = FakeRequest()
    path = SnapshotDownloader(lambda: request, FILE_ID, tmp_path / "snap", clock=lambda: 1.5).download()
    assert path == tmp_path / "snap" / "snapshot-1500.xlsx"
    assert path.read_bytes() == b"PK\x03\x04data"
    assert request.calls == [(export_url(FILE_ID), 60_000)]


def test_http_error(tmp_path):
    downloader = SnapshotDownloader(lambda: FakeRequest(FakeResponse(status=403)), FILE_ID, tmp_path)
    with pytest.raises(DownloadError, match="HTTP 403"):
        downloader.download()


def test_non_xlsx_body_means_login_needed(tmp_path):
    downloader = SnapshotDownloader(lambda: FakeRequest(FakeResponse(body=b"<html>")), FILE_ID, tmp_path)
    with pytest.raises(DownloadError, match="重新登入"):
        downloader.download()


def test_network_error(tmp_path):
    downloader = SnapshotDownloader(lambda: FakeRequest(error=OSError("offline")), FILE_ID, tmp_path)
    with pytest.raises(DownloadError, match="offline"):
        downloader.download()
