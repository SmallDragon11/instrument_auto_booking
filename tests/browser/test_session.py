from pathlib import Path

import pytest

from instrument_booking.browser.session import (
    BACKGROUND_ARGS,
    EdgeSession,
    NotLoggedIn,
    SessionError,
    open_login_window,
)
from instrument_booking.browser.sheet_page import NAME_BOX, OP_TIMEOUT_MS

URL = "https://docs.google.com/spreadsheets/d/FILEID/edit"


class FakeLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1 if self.page.loaded_after is not None and self.page.elapsed >= self.page.loaded_after else 0


class FakePage:
    """loaded_after：經過幾秒後名稱方塊出現（None＝永遠不出現）；final_url：goto 後的網址。"""

    def __init__(self, final_url, loaded_after=0.0):
        self.url = "about:blank"
        self.final_url, self.loaded_after = final_url, loaded_after
        self.default_timeout = None
        self.elapsed = 0.0

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def goto(self, url, wait_until):
        self.url = self.final_url

    def locator(self, selector):
        assert selector == NAME_BOX
        return FakeLocator(self)

    def wait_for_timeout(self, ms):
        self.elapsed += ms / 1000


class FakeCDP:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, method, params=None):
        if self.fail:
            raise RuntimeError("CDP 失敗")
        self.sent.append((method, params))
        return {"windowId": 7} if method == "Browser.getWindowForTarget" else {}


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.granted = None
        self.closed = False
        self.request = "request-context"
        self.cdp = FakeCDP()

    def grant_permissions(self, perms, origin):
        self.granted = (perms, origin)

    def new_cdp_session(self, page):
        return self.cdp

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


def make(page, load_timeout_ms=60_000):
    context = FakeContext(page)
    pw = FakePlaywright(context)
    session = EdgeSession(URL, Path("C:/profile"), playwright_factory=lambda: pw,
                          load_timeout_ms=load_timeout_ms, monotonic=lambda: page.elapsed)
    return session, pw, context


def test_start_launches_minimized_edge_profile_without_automation_flag():
    session, pw, context = make(FakePage(URL))
    page = session.start()
    user_data_dir, kwargs = pw.chromium.launch_args
    assert Path(user_data_dir) == Path("C:/profile")
    assert kwargs["channel"] == "msedge"
    assert kwargs["headless"] is False
    assert kwargs["args"] == BACKGROUND_ARGS
    assert "--start-minimized" in BACKGROUND_ARGS and "--disable-background-timer-throttling" in BACKGROUND_ARGS
    assert kwargs["ignore_default_args"] == ["--enable-automation"]
    assert context.granted == (["clipboard-read", "clipboard-write"], "https://docs.google.com")
    assert context.cdp.sent == [
        ("Browser.getWindowForTarget", None),
        ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}}),
    ]
    assert page.url == URL
    assert session.request == "request-context"


def test_minimize_failure_does_not_stop_start():
    session, _, context = make(FakePage(URL))
    context.cdp.fail = True
    assert session.start() is not None


def test_waits_until_name_box_appears():
    page = FakePage(URL, loaded_after=2.0)
    session, _, _ = make(page)
    session.start()
    assert page.elapsed == pytest.approx(2.0)


def test_login_redirect_is_detected_immediately_and_closes():
    page = FakePage("https://accounts.google.com/signin", loaded_after=None)
    session, pw, context = make(page)
    with pytest.raises(NotLoggedIn):
        session.start()
    assert page.elapsed == 0.0  # 不必等到逾時
    assert context.closed and pw.stopped


def test_load_timeout_raises_session_error_and_releases_browser():
    page = FakePage(URL, loaded_after=None)
    session, pw, context = make(page, load_timeout_ms=5_000)
    with pytest.raises(SessionError, match="5 秒"):
        session.start()
    assert page.elapsed >= 5.0
    assert context.closed and pw.stopped


def test_restart_closes_then_starts_again():
    session, pw, context = make(FakePage(URL))
    session.start()
    session.restart()
    assert context.closed and pw.stopped
    assert session.sheet_page() is not None
    assert context.pages[0].default_timeout == OP_TIMEOUT_MS  # 網頁操作有時限


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
