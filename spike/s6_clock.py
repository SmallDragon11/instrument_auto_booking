"""量測 NTP 與 HTTPS Date 標頭的時鐘差，各 5 次；並檢查 Date 是否為捨去。"""
import email.utils
import socket
import struct
import time
import urllib.request

DELTA = 2208988800


def ntp(host="time.google.com"):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        t1 = time.time()
        s.sendto(b"\x1b" + 47 * b"\0", (host, 123))
        data, _ = s.recvfrom(1024)
        t4 = time.time()

    def ts(off):
        sec, frac = struct.unpack("!II", data[off:off + 8])
        return sec - DELTA + frac / 2**32

    t2, t3 = ts(32), ts(40)
    return ((t2 - t1) + (t3 - t4)) / 2, (t4 - t1) - (t3 - t2)


def http_date(url="https://www.google.com/generate_204"):
    t1 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=3) as r:
        header = r.headers["Date"]
    t4 = time.time()
    server = email.utils.parsedate_to_datetime(header).timestamp() + 0.5
    return server - (t1 + t4) / 2, t4 - t1


for name, fn in (("NTP", ntp), ("HTTPS Date", http_date)):
    for _ in range(5):
        try:
            off, rtt = fn()
            print(f"{name}: 時鐘差 {off:+.3f}s，來回 {rtt * 1000:.0f}ms")
        except Exception as e:  # spike：記錄任何失敗
            print(f"{name}: 失敗 {type(e).__name__}: {e}")


def date_is_floored(samples=40, url="https://www.google.com/generate_204"):
    """以 NTP 為基準，檢查 Date 標頭是否為捨去：捨去時 D 一定 ≤ 收到回應時的真實時間。"""
    ntp_off, _ = ntp()
    violations, worst = 0, -9.0
    for _ in range(samples):
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=3) as r:
            header = r.headers["Date"]
        true_t4 = time.time() + ntp_off
        d = email.utils.parsedate_to_datetime(header).timestamp()
        ahead = d - true_t4  # 捨去時應 ≤ 0（容許 NTP 誤差數十毫秒）
        worst = max(worst, ahead)
        violations += ahead > 0.05
        time.sleep(0.137)  # 錯開取樣的小數秒
    print(f"Date 捨去檢查：{samples} 次中 {violations} 次超前真實時間，最大超前 {worst:+.3f}s"
          f" → {'捨去（HTTPS 餘量 0.1 秒）' if violations == 0 else '非捨去（HTTPS 餘量改為 0.6 秒）'}")


try:
    date_is_floored()
except Exception as e:
    print(f"Date 捨去檢查失敗（需 NTP 可用）：{type(e).__name__}: {e}")
