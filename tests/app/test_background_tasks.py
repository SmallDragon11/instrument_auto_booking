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


def test_no_delivery_after_shutdown(qtbot):
    """關閉後，進行中的工作不應回呼 GUI（避免在拆卸的 widget 上 emit）。"""
    tasks = BackgroundTasks()
    results = []
    gate = threading.Event()

    # 提交工作：等待 gate，完成時回呼 results.append
    future = tasks.run(lambda: gate.wait(5) or "done", results.append)

    # 立即關閉，工作仍在執行
    tasks.shutdown()

    # 放開 gate 讓工作完成
    gate.set()

    # 確保工作確實完成（等待 future）
    future.result(timeout=5)

    # 等待一點時間讓 signal 有機會排入隊列（如果有的話）
    qtbot.wait(200)

    # 驗證：即使工作完成，也不應呼叫回呼（因為已關閉）
    assert results == []
