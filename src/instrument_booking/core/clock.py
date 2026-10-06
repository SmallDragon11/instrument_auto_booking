"""網路校時（規格 §7）。

offset 一律取「保守下界」：單調時鐘＋offset ≤ 真實 epoch 時間，
所以依此判斷「到點」只可能偏晚、不可能偷跑。本機計時使用 time.monotonic，
不受 Windows 自動對時造成的系統時鐘跳動影響。
"""
from __future__ import annotations

import email.utils
import http.client
import os
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from enum import Enum
from http.client import HTTPException
from typing import Callable

NTP_EPOCH_DELTA = 2_208_988_800  # 1900-01-01 → 1970-01-01


class ClockSource(Enum):
    NTP = "NTP"
    HTTP_DATE = "HTTPS Date"
    LOCAL = "本機時間"


SAFETY_MARGIN = {ClockSource.NTP: 0.05, ClockSource.HTTP_DATE: 0.1, ClockSource.LOCAL: 3.0}


@dataclass(frozen=True)
class ClockSync:
    source: ClockSource
    offset: float

    @property
    def margin(self) -> float:
        return SAFETY_MARGIN[self.source]


def ntp_offset(t1: float, t2: float, t3: float, t4: float) -> tuple[float, float]:
    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)
    return offset, delay


def build_ntp_request(nonce: bytes = bytes(8)) -> bytes:
    """LI=0、VN=3、Mode=3（client）；nonce 放在 transmit timestamp，伺服器會在 originate 原樣回傳。"""
    return b"\x1b" + 39 * b"\0" + nonce


def parse_ntp_response(data: bytes, nonce: bytes | None = None) -> tuple[float, float]:
    if len(data) < 48:
        raise ValueError("NTP 回應長度不足")
    li, mode, stratum = data[0] >> 6, data[0] & 0x07, data[1]
    if li == 3:
        raise ValueError("NTP 伺服器未同步")
    if mode != 4:
        raise ValueError(f"NTP 回應模式錯誤：{mode}")
    if not 1 <= stratum <= 15:
        raise ValueError(f"NTP 回應 stratum 不合法：{stratum}")
    if nonce is not None and data[24:32] != nonce:
        raise ValueError("NTP 回應與請求不符")
    if data[32:40] == bytes(8) or data[40:48] == bytes(8):
        raise ValueError("NTP 回應時間戳為零")

    def ts(offset: int) -> float:
        sec, frac = struct.unpack("!II", data[offset:offset + 8])
        return sec - NTP_EPOCH_DELTA + frac / 2**32

    return ts(32), ts(40)


def query_ntp(host: str = "time.google.com", timeout: float = 2.0, *,
              now: Callable[[], float] = time.monotonic, sock_factory=socket.socket) -> ClockSync:
    nonce = os.urandom(8)
    with sock_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t1 = now()
        sock.sendto(build_ntp_request(nonce), (host, 123))
        data, _ = sock.recvfrom(1024)
        t4 = now()
    t2, t3 = parse_ntp_response(data, nonce)
    offset, delay = ntp_offset(t1, t2, t3, t4)
    # 下界 offset − delay/2 在代數上恆等於 t3 − t4：伺服器送出回應（t3）必早於本機收到（t4），
    # 只要 t3 是誠實的伺服器時間（已由封包驗證把關），此下界與 delay 的正負無關。
    return ClockSync(ClockSource.NTP, offset - delay / 2)


def _fetch_date_header(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.headers["Date"]


def query_http_date(url: str = "https://www.google.com/generate_204", timeout: float = 3.0, *,
                    now: Callable[[], float] = time.monotonic, fetch=None) -> ClockSync:
    fetch = fetch or _fetch_date_header
    header = fetch(url, timeout)
    t4 = now()
    if not header:
        raise ValueError("回應沒有 Date 標頭")
    # Date 只到秒且為捨去：產生標頭的真實時間 ≥ D，且發生在本機收到回應（t4）之前 → 下界 D − t4
    server_dt = email.utils.parsedate_to_datetime(header)
    if server_dt.tzinfo is None:  # 「-0000」會得到 naive datetime；HTTP Date 一律是 UTC
        server_dt = server_dt.replace(tzinfo=timezone.utc)
    return ClockSync(ClockSource.HTTP_DATE, server_dt.timestamp() - t4)


def local_sync() -> ClockSync:
    """未校時：以系統時鐘為準，換算成相對於單調時鐘的差。"""
    return ClockSync(ClockSource.LOCAL, time.time() - time.monotonic())


def sync_clock(ntp: Callable[[], ClockSync] = query_ntp,
               http: Callable[[], ClockSync] = query_http_date,
               local: Callable[[], ClockSync] = local_sync) -> ClockSync:
    for attempt in (ntp, http):
        try:
            return attempt()
        except (OSError, ValueError, TypeError, HTTPException):
            continue
    return local()


class Clock:
    def __init__(self, sync: ClockSync, local_now: Callable[[], float] = time.monotonic) -> None:
        self.sync = sync
        self._local_now = local_now

    def now(self) -> float:
        return self._local_now() + self.sync.offset
