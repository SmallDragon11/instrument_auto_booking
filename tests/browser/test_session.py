from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from instrument_booking.browser.session import EdgeSession, NotLoggedIn, open_login_window

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"


class FakePage:
    def __init__(self, final_url, loads=True):
        self.url = "about:blank"
        self.final_url, self.loads = final_url, loads

    def goto(self, url, wait_until):
        self.url = self.final_url

    def wait_for_selector(self, selector, timeout):
        if not self.loads:
            raise PlaywrightTimeoutError("Timeout")


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.granted = None
        self.closed = False
        self.request = "request-context"

    def grant_permissions(self, perms, origin):
        self.granted = (perms, origin)

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.launch_args = None
        self.fail = None

    def launch_persistent_context(self, user_data_dir, **kwargs):
        if self.fail:
            raise self.fail
        self.launch_args = (user_data_dir, kwargs)
        return self.context


class FakePlaywright:
    def __init__(self, context):
        self.chromium = FakeChromium(context)
        self.stopped = False

    def start(self):
        return self

    def stop(self):
        self.stopped = True


def make(page):
    context = FakeContext(page)
    pw = FakePlaywright(context)
    return EdgeSession(URL, Path("C:/profile"), playwright_factory=lambda: pw), pw, context


def test_start_launches_edge_profile_without_automation_flag():
    session, pw, context = make(FakePage(URL))
    page = session.start()
    user_data_dir, kwargs = pw.chromium.launch_args
    assert Path(user_data_dir) == Path("C:/profile")
    assert kwargs["channel"] == "msedge"
    assert kwargs["headless"] is False
    assert kwargs["ignore_default_args"] == ["--enable-automation"]
    assert context.granted == (["clipboard-read", "clipboard-write"], "https://docs.google.com")
    assert page.url == URL
    assert session.request == "request-context"


def test_login_redirect_raises_not_logged_in_and_closes():
    session, pw, context = make(FakePage("https://accounts.google.com/signin", loads=False))
    with pytest.raises(NotLoggedIn):
        session.start()
    assert context.closed and pw.stopped


def test_other_load_timeout_propagates_and_releases_browser():
    session, pw, context = make(FakePage(URL, loads=False))
    with pytest.raises(PlaywrightTimeoutError):
        session.start()
    assert context.closed and pw.stopped


def test_restart_closes_then_starts_again():
    session, pw, context = make(FakePage(URL))
    session.start()
    session.restart()
    assert context.closed and pw.stopped
    assert session.sheet_page() is not None


def test_open_login_window_uses_plain_edge_with_profile(tmp_path):
    calls = []
    open_login_window(URL, tmp_path / "profile", edge_path=Path("C:/edge.exe"), popen=calls.append)
    (args,) = calls
    assert args[0] == str(Path("C:/edge.exe"))
    assert f"--user-data-dir={tmp_path / 'profile'}" in args
    assert args[-1] == URL
    assert (tmp_path / "profile").is_dir()


def test_launch_failure_stops_playwright():
    session, pw, _ = make(FakePage(URL))
    pw.chromium.fail = RuntimeError("設定檔被另一個 Edge 占用")
    with pytest.raises(RuntimeError):
        session.start()
    assert pw.stopped
