import threading

import pytest

from instrument_booking.browser.session import NotLoggedIn
from instrument_booking.service.worker import BrowserWorker
from service.fakes import FakeSession


@pytest.fixture
def setup():
    log = []
    sessions = []

    def factory():
        s = FakeSession(log)
        sessions.append(s)
        return s
    worker = BrowserWorker(factory)
    yield worker, log, sessions
    worker.shutdown()


def test_all_browser_work_runs_on_one_dedicated_thread(setup):
    worker, log, _ = setup
    ids = [worker.submit(lambda s: threading.get_ident()).result() for _ in range(3)]
    assert len(set(ids)) == 1 and ids[0] != threading.get_ident()
    assert {tid for _, tid in log} == {ids[0]}  # start／close 也在同一條執行緒


def test_session_starts_lazily_and_closes_after_job_by_default(setup):
    worker, log, sessions = setup
    assert log == []
    assert worker.submit(lambda s: "ok").result() == "ok"
    assert [e for e, _ in log] == ["start", "close"]


def test_keep_open_reuses_session_until_a_closing_job(setup):
    worker, log, sessions = setup
    worker.submit(lambda s: s, keep_open=True).result()
    worker.submit(lambda s: s, keep_open=True).result()
    worker.submit(lambda s: s).result()
    assert [e for e, _ in log] == ["start", "close"]
    assert len(sessions) == 1


def test_failed_keep_open_job_leaves_session_for_next_job(setup):
    worker, log, _ = setup

    def boom(s):
        raise RuntimeError("網頁錯誤")
    with pytest.raises(RuntimeError):
        worker.submit(boom, keep_open=True).result()
    worker.submit(lambda s: None).result()
    assert [e for e, _ in log] == ["start", "close"]  # keep_open 的工作失敗時不關閉，下一個工作沿用


def test_failed_job_without_keep_open_closes_browser(setup):
    worker, log, _ = setup

    def boom(s):
        raise RuntimeError("下載失敗")
    with pytest.raises(RuntimeError):
        worker.submit(boom).result()
    assert [e for e, _ in log] == ["start", "close"]  # 失敗也不會留下開著的 Edge


def test_start_failure_propagates_and_next_job_retries():
    log = []
    attempts = []

    def factory():
        attempts.append(1)
        return FakeSession(log, fail_start=NotLoggedIn("x") if len(attempts) == 1 else None)
    worker = BrowserWorker(factory)
    try:
        with pytest.raises(NotLoggedIn):
            worker.submit(lambda s: None).result()
        assert worker.submit(lambda s: "ok").result() == "ok"
        assert len(attempts) == 2
    finally:
        worker.shutdown()


def test_close_session_runs_after_queued_jobs(setup):
    worker, log, _ = setup
    worker.submit(lambda s: None, keep_open=True)
    worker.close_session().result()
    assert [e for e, _ in log] == ["start", "close"]
