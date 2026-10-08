"""以暫存資料夾實際執行 install.ps1（不碰真正的安裝位置、桌面與開始功能表，也不啟動程式）。"""
import ctypes
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def make_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "ExperimentPlanner" / "_internal").mkdir(parents=True)
    (pkg / "ExperimentPlanner" / "ExperimentPlanner.exe").write_bytes(b"MZ fake")
    (pkg / "ExperimentPlanner" / "_internal" / "lib.dll").write_bytes(b"lib")
    for name in ("install.ps1", "uninstall.ps1", "解除安裝.cmd"):
        shutil.copy(TOOLS / name, pkg / name)
    return pkg


def run_install(pkg: Path, tmp_path: Path, process_name: str = None) -> subprocess.CompletedProcess:
    if process_name is None:
        process_name = f"ExperimentPlanner-test-{uuid.uuid4().hex}"
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(pkg / "install.ps1"),
         "-InstallDir", str(tmp_path / "inst"), "-DesktopDir", str(tmp_path / "desk"),
         "-StartMenuDir", str(tmp_path / "start"), "-NoLaunch", "-Quiet", "-ProcessName", process_name],
        capture_output=True, timeout=120)


def test_install_script_is_utf8_with_bom_for_windows_powershell():
    assert (TOOLS / "install.ps1").read_bytes()[:3] == b"\xef\xbb\xbf"
    cmd = (TOOLS / "安裝.cmd").read_bytes()
    assert b"install.ps1" in cmd
    cmd.decode("ascii")
    assert b"if errorlevel 1 pause" in cmd
    assert cmd.endswith(b"\r\n") and b"\n" not in cmd.replace(b"\r\n", b"")


def test_install_copies_program_and_creates_shortcuts(tmp_path):
    pkg = make_package(tmp_path)
    result = run_install(pkg, tmp_path)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert (tmp_path / "inst" / "ExperimentPlanner.exe").read_bytes() == b"MZ fake"
    assert (tmp_path / "inst" / "_internal" / "lib.dll").exists()
    assert (tmp_path / "desk" / "實驗規劃助手.lnk").exists()
    assert (tmp_path / "start" / "實驗規劃助手.lnk").exists()


def test_reinstall_replaces_old_program_files(tmp_path):
    pkg = make_package(tmp_path)
    assert run_install(pkg, tmp_path).returncode == 0
    (pkg / "ExperimentPlanner" / "_internal" / "lib.dll").unlink()
    (pkg / "ExperimentPlanner" / "_internal" / "new.dll").write_bytes(b"new")
    assert run_install(pkg, tmp_path).returncode == 0
    assert not (tmp_path / "inst" / "_internal" / "lib.dll").exists()
    assert (tmp_path / "inst" / "_internal" / "new.dll").exists()


def test_missing_program_folder_fails_with_message(tmp_path):
    pkg = make_package(tmp_path)
    shutil.rmtree(pkg / "ExperimentPlanner")
    result = run_install(pkg, tmp_path)
    assert result.returncode == 1
    assert not (tmp_path / "inst").exists()


@contextmanager
def exclusive_lock(path: Path):
    """以不共用模式（share=0）開啟檔案，模擬防毒掃描或殘留程序佔用檔案。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
    try:
        yield
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_locked_file_makes_install_fail_quickly_instead_of_hanging(tmp_path):
    pkg = make_package(tmp_path)
    assert run_install(pkg, tmp_path).returncode == 0
    (pkg / "ExperimentPlanner" / "_internal" / "lib.dll").write_bytes(b"a different, longer lib")
    out_file = tmp_path / "out.txt"
    process_name = f"ExperimentPlanner-test-{uuid.uuid4().hex}"
    with exclusive_lock(tmp_path / "inst" / "_internal" / "lib.dll"):
        # 輸出寫進檔案而不是管線：逾時時不會因殘留的 robocopy 子程序握著管線而卡住測試。
        with out_file.open("wb") as out:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(pkg / "install.ps1"),
                 "-InstallDir", str(tmp_path / "inst"), "-DesktopDir", str(tmp_path / "desk"),
                 "-StartMenuDir", str(tmp_path / "start"), "-NoLaunch", "-Quiet", "-ProcessName", process_name],
                stdout=out, stderr=subprocess.STDOUT, timeout=60)
    assert result.returncode == 1
    assert b"robocopy" in out_file.read_bytes()


def test_refuses_to_mirror_over_unrelated_folder(tmp_path):
    pkg = make_package(tmp_path)
    (tmp_path / "inst").mkdir()
    (tmp_path / "inst" / "keep.txt").write_text("keep", encoding="utf-8")
    result = run_install(pkg, tmp_path)
    assert result.returncode == 1
    assert (tmp_path / "inst" / "keep.txt").exists()
    assert not (tmp_path / "inst" / "ExperimentPlanner.exe").exists()


def test_install_works_when_package_path_contains_brackets(tmp_path):
    root = tmp_path / "br[1]"
    root.mkdir()
    pkg = make_package(root)
    result = run_install(pkg, root)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert (root / "inst" / "ExperimentPlanner.exe").exists()


def test_install_places_uninstaller_and_start_menu_shortcut(tmp_path):
    pkg = make_package(tmp_path)
    assert run_install(pkg, tmp_path).returncode == 0
    assert (tmp_path / "inst" / "uninstall.ps1").exists()
    assert (tmp_path / "inst" / "解除安裝.cmd").exists()
    assert (tmp_path / "start" / "解除安裝實驗規劃助手.lnk").exists()
    assert not (tmp_path / "desk" / "解除安裝實驗規劃助手.lnk").exists()


def test_reinstall_keeps_uninstaller(tmp_path):
    pkg = make_package(tmp_path)
    assert run_install(pkg, tmp_path).returncode == 0
    assert run_install(pkg, tmp_path).returncode == 0
    assert (tmp_path / "inst" / "uninstall.ps1").exists()


# ---- 解除安裝 ----

def test_uninstall_script_files_are_encoded_for_windows():
    assert (TOOLS / "uninstall.ps1").read_bytes()[:3] == b"\xef\xbb\xbf"
    cmd = (TOOLS / "解除安裝.cmd").read_bytes()
    assert b"uninstall.ps1" in cmd
    cmd.decode("ascii")
    assert b"pause" in cmd
    crlf = b"\r\n"
    assert cmd.endswith(crlf) and b"\n" not in cmd.replace(crlf, b"")


@contextmanager
def fake_run_key(name: str):
    import winreg
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, '"x.exe" --background')
    try:
        yield name
    finally:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass
        key.Close()


def run_key_exists(name: str) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, name)
            return True
    except FileNotFoundError:
        return False


def installed(tmp_path: Path) -> Path:
    pkg = make_package(tmp_path)
    assert run_install(pkg, tmp_path).returncode == 0
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "cookie").write_text("c", encoding="utf-8")
    return tmp_path / "inst" / "uninstall.ps1"


def run_uninstall(script: Path, tmp_path: Path, *extra: str, process_name: str = None, run_key: str = "ExperimentPlannerTest"):
    if process_name is None:
        process_name = f"ExperimentPlanner-test-{uuid.uuid4().hex}"
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-InstallDir", str(tmp_path / "inst"), "-DesktopDir", str(tmp_path / "desk"),
         "-StartMenuDir", str(tmp_path / "start"), "-DataDir", str(tmp_path / "data"),
         "-ProfileDir", str(tmp_path / "profile"), "-RunKeyName", run_key,
         "-Quiet", "-ProcessName", process_name, *extra],
        capture_output=True, timeout=120)


def test_uninstall_removes_program_shortcuts_and_autostart_but_keeps_data(tmp_path):
    script = installed(tmp_path)
    with fake_run_key("ExperimentPlannerTest"):
        result = run_uninstall(script, tmp_path)
        assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
        assert not run_key_exists("ExperimentPlannerTest")
    assert not (tmp_path / "inst").exists()
    assert not (tmp_path / "desk" / "實驗規劃助手.lnk").exists()
    assert not (tmp_path / "start" / "實驗規劃助手.lnk").exists()
    assert not (tmp_path / "start" / "解除安裝實驗規劃助手.lnk").exists()
    assert (tmp_path / "data" / "settings.json").exists()
    assert (tmp_path / "profile" / "cookie").exists()


def test_uninstall_with_remove_data_deletes_settings_and_profile(tmp_path):
    script = installed(tmp_path)
    result = run_uninstall(script, tmp_path, "-RemoveData")
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "profile").exists()


def test_uninstall_refuses_while_program_is_running(tmp_path):
    script = installed(tmp_path)
    result = run_uninstall(script, tmp_path, process_name="powershell")
    assert result.returncode == 1
    assert (tmp_path / "inst" / "ExperimentPlanner.exe").exists()
    assert (tmp_path / "desk" / "實驗規劃助手.lnk").exists()


def test_uninstall_refuses_to_delete_unrelated_folder(tmp_path):
    script = installed(tmp_path)
    (tmp_path / "inst" / "ExperimentPlanner.exe").unlink()
    (tmp_path / "inst" / "keep.txt").write_text("keep", encoding="utf-8")
    result = run_uninstall(script, tmp_path)
    assert result.returncode == 1
    assert (tmp_path / "inst" / "keep.txt").exists()


def test_uninstall_twice_is_not_an_error(tmp_path):
    script = installed(tmp_path)
    copy = tmp_path / "uninstall-copy.ps1"
    shutil.copy(script, copy)
    assert run_uninstall(copy, tmp_path).returncode == 0
    assert run_uninstall(copy, tmp_path).returncode == 0


def test_uninstall_cmd_from_installed_folder_removes_the_folder_it_lives_in(tmp_path):
    """從開始功能表捷徑執行：捷徑的起始位置就是安裝資料夾，cmd 的目前目錄不能擋住資料夾的刪除。"""
    import time
    installed(tmp_path)
    inst = tmp_path / "inst"
    result = subprocess.run(
        ["cmd", "/c", str(inst / "解除安裝.cmd"),
         "-InstallDir", str(inst), "-DesktopDir", str(tmp_path / "desk"), "-StartMenuDir", str(tmp_path / "start"),
         "-DataDir", str(tmp_path / "data"), "-ProfileDir", str(tmp_path / "profile"),
         "-RunKeyName", "ExperimentPlannerCmdTest", "-Quiet",
         "-ProcessName", f"ExperimentPlanner-test-{uuid.uuid4().hex}"],
        cwd=inst, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    deadline = time.time() + 30
    while inst.exists() and time.time() < deadline:
        time.sleep(0.5)
    assert not inst.exists()
    assert not (tmp_path / "start" / "解除安裝實驗規劃助手.lnk").exists()
    assert (tmp_path / "data" / "settings.json").exists()
    # cmd 檔自己也被刪掉了：之後不能再讀它的下一行，否則會多出「系統找不到指定的路徑」且結束碼為 1。
    assert result.returncode == 0, result.stdout.decode("cp950", "replace") + result.stderr.decode("cp950", "replace")
    assert result.stderr == b""


def test_failed_uninstall_leaves_shortcuts_and_autostart_so_it_can_be_retried(tmp_path):
    script = installed(tmp_path)
    with fake_run_key("ExperimentPlannerTest"):
        with exclusive_lock(tmp_path / "inst" / "_internal" / "lib.dll"):
            result = run_uninstall(script, tmp_path)
        assert result.returncode == 1
        assert run_key_exists("ExperimentPlannerTest")
    assert (tmp_path / "desk" / "實驗規劃助手.lnk").exists()
    assert (tmp_path / "start" / "實驗規劃助手.lnk").exists()
    assert (tmp_path / "start" / "解除安裝實驗規劃助手.lnk").exists()
