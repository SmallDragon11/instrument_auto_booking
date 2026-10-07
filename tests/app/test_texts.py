from datetime import date, datetime, timedelta

from instrument_booking.app.texts import (
    clock_label,
    countdown,
    result_rows,
    run_meta,
    run_title,
    status_line,
    week_range_label,
    when_label,
)
from instrument_booking.core.models import TAIPEI, BookingRequest, Instrument, ItemResult, ItemStatus
from instrument_booking.service.automation import ServicePhase, ServiceStatus
from instrument_booking.storage.json_store import RunRecord

RUN = datetime(2026, 10, 9, 13, 0, tzinfo=TAIPEI)
MON = date(2026, 10, 12)


def test_week_and_time_labels():
    assert week_range_label(MON) == "10/12（一）– 10/18（日）"
    assert when_label(RUN) == "10/9（五）13:00"


def test_countdown_units():
    assert countdown(RUN - timedelta(days=2, hours=3, minutes=5), RUN) == "還有 2 天 3 小時"
    assert countdown(RUN - timedelta(hours=1, minutes=5, seconds=9), RUN) == "還有 1 小時 5 分"
    assert countdown(RUN - timedelta(minutes=4, seconds=30), RUN) == "還有 4 分 30 秒"
    assert countdown(RUN, RUN) == "時間已到"
    assert countdown(RUN + timedelta(seconds=3), RUN) == "時間已到"


def test_status_line_for_each_phase():
    now = RUN - timedelta(hours=1, minutes=5)
    assert "設定" in status_line(ServiceStatus(ServicePhase.NOT_CONFIGURED), now)
    assert status_line(ServiceStatus(ServicePhase.IDLE, run_at=RUN, target_monday=MON), now) == \
        "下次自動預約：10/9（五）13:00（還有 1 小時 5 分）"
    assert status_line(ServiceStatus(ServicePhase.IDLE), now) == "等待中"
    assert status_line(ServiceStatus(ServicePhase.READY, run_at=RUN), RUN - timedelta(seconds=40)) == \
        "已準備好，13:00 自動寫入（還有 0 分 40 秒）"
    retry = ServiceStatus(ServicePhase.RETRY_WAIT, run_at=RUN, retry_at=RUN - timedelta(minutes=2),
                          last_error="下載快照失敗")
    assert status_line(retry, now) == "預檢失敗，12:58 會再試一次：下載快照失敗"
    assert status_line(ServiceStatus(ServicePhase.ERROR, service_error="磁碟已滿"), now) == \
        "自動預約服務發生錯誤：磁碟已滿"
    assert status_line(ServiceStatus(ServicePhase.RUNNING, run_at=RUN), now) == "寫入中…"
    assert "執行紀錄" in status_line(ServiceStatus(ServicePhase.DONE, run_at=RUN), now)


def test_clock_label_sign_means_local_clock_slow_or_fast():
    assert clock_label(None, None) == "未校時"
    assert clock_label("NTP", 0.6) == "校時：NTP（本機時鐘慢 0.60 秒）"
    assert clock_label("NTP", -1.234) == "校時：NTP（本機時鐘快 1.23 秒）"
    assert clock_label("HTTPS Date", 0.001) == "校時：HTTPS Date（本機時鐘準確）"
    assert clock_label("本機時間", 0.0) == "未能網路校時，使用本機時間"


def record(**kw):
    a = BookingRequest("a", Instrument.TUBE_A, MON, 9, 12, "Ar", "A4C2F4")
    b = BookingRequest("b", Instrument.OVEN_B, MON, 13, 14, None, "A4C2F4")
    c = BookingRequest("c", Instrument.TUBE_C, MON, 9, 10, "Ar", "A4C2F4")
    base = dict(target_monday=MON, started_at=datetime(2026, 10, 9, 13, 0, 0, 120000, tzinfo=TAIPEI), late=False,
                clock_source="NTP", clock_diff=0.6, error=None, requests=(a, b, c),
                results=(ItemResult("a", ItemStatus.SUCCESS, written_at=datetime(2026, 10, 9, 13, 0, 0, 412345,
                                                                                tzinfo=TAIPEI)),
                         ItemResult("b", ItemStatus.LIVE_CONFLICT, reason="已有人預約：Amy",
                                    warning="圖例找不到 Ar")),
                snapshot_at=datetime(2026, 10, 9, 12, 50, 3, tzinfo=TAIPEI))
    base.update(kw)
    return RunRecord(**base)


def test_run_title_and_meta():
    r = record()
    assert run_title(r) == "10/12（一）– 10/18（日） 的預約"
    assert run_meta(r) == ["執行時間：10/9（五）13:00:00", "校時：NTP（本機時鐘慢 0.60 秒）", "預約表下載於 12:50:03"]
    late = record(late=True, clock_source=None, clock_diff=None, snapshot_at=None)
    assert run_meta(late) == ["執行時間：10/9（五）13:00:00（延遲執行）", "未校時"]


def test_result_rows_follow_priority_and_mark_missing_results():
    rows = result_rows(record())
    assert [(r.priority, r.kind) for r in rows] == [(1, "success"), (2, "skipped"), (3, "none")]
    assert rows[0].text == "10/12（一）A-牆 09:00–12:00 Ar"
    assert rows[0].status == "✅ 成功" and rows[0].detail == "寫入於 13:00:00.412"
    assert rows[1].status == "⚡ 即時衝突" and rows[1].detail == "已有人預約：Amy；圖例找不到 Ar"
    assert rows[2].status == "－ 未執行" and rows[2].detail == ""
