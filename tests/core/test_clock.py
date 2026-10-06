import struct
import time

import pytest

from instrument_booking.core.clock import (
    NTP_EPOCH_DELTA,
    Clock,
    ClockSource,
    ClockSync,
    build_ntp_request,
    ntp_offset,
    parse_ntp_response,
    query_http_date,
    query_ntp,
    sync_clock,
)


def ntp_bytes(t2: float, t3: float) -> bytes:
    def ts(t):
        sec = int(t) + NTP_EPOCH_DELTA
        return struct.pack("!II", sec, int((t % 1) * 2**32))
    return bytes(32) + ts(t2) + ts(t3)


def seq(*values):
    it = iter(values)
    return lambda: next(it)


def test_ntp_offset_formula():
    offset, delay = ntp_offset(100.0, 105.0, 105.001, 100.011)
    assert offset == pytest.approx(4.995)
    assert delay == pytest.approx(0.010)


def test_build_ntp_request():
    data = build_ntp_request()
    assert len(data) == 48 and data[0] == 0x1B


def test_parse_ntp_response():
    t2, t3 = parse_ntp_response(ntp_bytes(1_791_000_000.25, 1_791_000_000.5))
    assert t2 == pytest.approx(1_791_000_000.25, abs=1e-6)
    assert t3 == pytest.approx(1_791_000_000.5, abs=1e-6)


def test_parse_ntp_response_rejects_short_data():
    with pytest.raises(ValueError):
        parse_ntp_response(b"\0" * 10)


class FakeSocket:
    def __init__(self, reply):
        self.reply, self.sent = reply, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        self.sent = (data, addr)

    def recvfrom(self, n):
        return self.reply, ("x", 123)


def test_query_ntp_returns_conservative_lower_bound():
    sock = FakeSocket(ntp_bytes(1005.0, 1005.0))
    sync = query_ntp(now=seq(1000.0, 1000.02), sock_factory=lambda *a: sock)
    assert sync.source is ClockSource.NTP
    # 中點估計 4.99、來回延遲 0.02 → 下界 4.99 − 0.01 = 4.98
    assert sync.offset == pytest.approx(4.98)
    assert sock.sent[1] == ("time.google.com", 123)


def test_query_http_date_returns_conservative_lower_bound():
    header = "Fri, 09 Oct 2026 05:00:00 GMT"  # = 1791522000
    sync = query_http_date(now=seq(500.2), fetch=lambda url, timeout: header)
    assert sync.source is ClockSource.HTTP_DATE
    # 產生標頭的真實時間 ≥ D，且發生在 t4 之前 → 下界 D − t4
    assert sync.offset == pytest.approx(1791522000 - 500.2)


def test_lower_bound_never_runs_ahead_of_true_time():
    # 伺服器在請求期間任一時刻、任一小數秒產生標頭，下界都不會超過真實時鐘差
    header = "Fri, 09 Oct 2026 05:00:00 GMT"
    t1, t4 = 500.0, 500.2  # 請求送出與收到回應的本機（單調）時間
    sync = query_http_date(now=seq(t4), fetch=lambda url, timeout: header)
    for local_at_gen in (t1, 500.1, t4):
        for frac in (0.0, 0.5, 0.999):
            true_offset = (1791522000 + frac) - local_at_gen
            assert sync.offset <= true_offset + 1e-9


def boom():
    raise OSError("blocked")


def test_sync_clock_prefers_ntp():
    assert sync_clock(ntp=lambda: ClockSync(ClockSource.NTP, 1.0), http=boom).source is ClockSource.NTP


def test_sync_clock_falls_back_to_http_then_local():
    assert sync_clock(ntp=boom, http=lambda: ClockSync(ClockSource.HTTP_DATE, 2.0)).offset == 2.0
    local = sync_clock(ntp=boom, http=boom)
    assert local.source is ClockSource.LOCAL
    assert Clock(local).now() == pytest.approx(time.time(), abs=0.05)  # 本機時間＝系統時鐘


def test_margins():
    assert ClockSync(ClockSource.NTP, 0).margin == 0.05
    assert ClockSync(ClockSource.HTTP_DATE, 0).margin == 0.1
    assert ClockSync(ClockSource.LOCAL, 0).margin == 3.0


def test_clock_now_applies_offset():
    assert Clock(ClockSync(ClockSource.NTP, -2.5), local_now=lambda: 100.0).now() == 97.5
