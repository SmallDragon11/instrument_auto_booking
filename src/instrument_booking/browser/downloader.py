"""以瀏覽器的登入狀態下載試算表快照（僅 export?format=xlsx 會反映最新編輯，見 docs/spike-report.md 第 6 項）。"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

_FILE_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


class DownloadError(Exception):
    """快照下載失敗（訊息為給使用者看的繁體中文）。"""


def file_id_from_url(url: str) -> str:
    m = _FILE_ID.search(url)
    if not m:
        raise ValueError(f"不是 Google 試算表網址：{url}")
    return m.group(1)


def export_url(file_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"


class SnapshotDownloader:
    """符合 core.job.SnapshotSource。request_provider 回傳與瀏覽器共用 cookie 的 APIRequestContext。"""

    def __init__(self, request_provider: Callable[[], object], file_id: str, dest_dir: Path, *,
                 timeout_ms: int = 60_000, clock: Callable[[], float] = time.time) -> None:
        self._request_provider = request_provider
        self._file_id = file_id
        self._dest_dir = dest_dir
        self._timeout_ms = timeout_ms
        self._clock = clock

    def download(self) -> Path:
        try:
            response = self._request_provider().get(export_url(self._file_id), timeout=self._timeout_ms)
            body = response.body()
        except Exception as e:
            raise DownloadError(f"下載快照失敗：{e}") from e
        if not response.ok:
            raise DownloadError(f"下載快照失敗：HTTP {response.status}")
        if body[:2] != b"PK":
            raise DownloadError("下載的內容不是 xlsx（可能需要重新登入 Google）")
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        path = self._dest_dir / f"snapshot-{int(self._clock() * 1000)}.xlsx"
        path.write_bytes(body)
        return path
