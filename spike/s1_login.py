"""以一般（非自動化）Edge 開啟專屬設定檔，讓使用者手動登入 Google。"""
import subprocess

from common import EDGE, PROFILE, TEST_URL

PROFILE.mkdir(parents=True, exist_ok=True)
subprocess.Popen([EDGE, f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", TEST_URL])
print("已開啟 Edge：請登入 Google、確認看得到試算表後，關閉這個 Edge 視窗。")
