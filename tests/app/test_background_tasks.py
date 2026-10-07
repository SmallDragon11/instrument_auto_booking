import threading

from instrument_booking.app.notifier import QtNotifier
from instrument_booking.app.tasks import BackgroundTasks


def test_result_is_delivered_on_gui_thread(qtbot):
    tasks = BackgroundTasks()
    gui = threading.get_ident()
    seen = []
    tasks.run(lambda: threading.get_ident(), lambda worker: seen.append((worker, threading.get_ident())))
    qtbot.waitUntil(lambda: len(seen) > 0)
    (worker, callback_thread), = seen
    assert worker != gui and callback_thread == gui
    tasks.shutdown()


def test_errors_go_to_on_error(qtbot):
    tasks = BackgroundTasks()
    errors = []

    def fail():
        raise ValueError("壞掉了")
    tasks.run(fail, lambda r: errors.append("不該成功"), errors.append)
    qtbot.waitUntil(lambda: len(errors) > 0)
    assert isinstance(errors[0], ValueError)
    tasks.shutdown()


def test_notifier_signal_crosses_threads(qtbot):
    notifier = QtNotifier()
    got = []
    notifier.notified.connect(lambda t, m: got.append((t, m, threading.get_ident())))
    t = threading.Thread(target=notifier.notify, args=("標題", "內容"))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: len(got) > 0)
    assert got == [("標題", "內容", threading.get_ident())]
