"""service 層測試共用的假物件。"""
from __future__ import annotations

import io
import threading
from concurrent.futures import Future


class FakeSession:
    """記錄每個動作發生在哪條執行緒。"""

    def __init__(self, log: list, *, fail_start: Exception | None = None, request=None,
                 url: str = "https://docs.google.com/spreadsheets/d/FILEID/edit"):
        self.log, self.fail_start, self.request, self.url = log, fail_start, request, url

    def start(self):
        self.log.append(("start", threading.get_ident()))
        if self.fail_start:
            raise self.fail_start

    def close(self):
        self.log.append(("close", threading.get_ident()))

    def sheet_page(self):
        return "sheet-page"

    def restart_sheet_page(self):
        return "sheet-page"


class InlineWorker:
    """同步執行工作的 BrowserWorker 替身（測試 runner／automation 用）。"""

    def __init__(self, session):
        self.session = session
        self.jobs: list[bool] = []  # 每個工作的 keep_open
        self.holds: list[bool | None] = []  # 每個工作的 hold
        self.closed = 0

    def submit(self, job, *, keep_open=False, hold=None):
        self.jobs.append(keep_open)
        self.holds.append(hold)
        f = Future()
        try:
            f.set_result(job(self.session))
        except BaseException as e:  # noqa: BLE001 — 與 Future 行為一致
            f.set_exception(e)
        return f

    def close_idle_session(self):
        f = Future()
        if getattr(self, "held", False):
            f.set_result(False)
        else:
            self.closed += 1
            f.set_result(True)
        return f

    def close_session(self):
        self.closed += 1
        f = Future()
        f.set_result(None)
        return f


class XlsxResponse:
    def __init__(self, body: bytes):
        self.status, self.ok, self._body = 200, True, body

    def body(self):
        return self._body


class XlsxRequest:
    """回傳指定活頁簿 xlsx 位元組的假 APIRequestContext。"""

    def __init__(self, wb):
        buf = io.BytesIO()
        wb.save(buf)
        self.body = buf.getvalue()
        self.urls: list[str] = []

    def get(self, url, timeout):
        self.urls.append(url)
        return XlsxResponse(self.body)
