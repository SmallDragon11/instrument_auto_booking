"""以暫存資料夾實際執行 install.ps1（不碰真正的安裝位置、桌面與開始功能表，也不啟動程式）。"""
import ctypes
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def make_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "ExperimentPlanner" / "_internal").mkdir(parents=True)
    (pkg / "ExperimentPlanner" / "ExperimentPlanner.exe").write_bytes(b"MZ fake")
    (pkg / "ExperimentPlanner" / "_internal" / "lib.dll").write_bytes(b"lib")
    shutil.copy(TOOLS / "install.ps1", pkg / "install.ps1")
    return pkg


def run_install(pkg: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(pkg / "install.ps1"),
         "-InstallDir", str(tmp_path / "inst"), "-DesktopDir", str(tmp_path / "desk"),
         "-StartMenuDir", str(tmp_path / "start"), "-NoLaunch", "-Quiet"],
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
    with exclusive_lock(tmp_path / "inst" / "_internal" / "lib.dll"):
        # 輸出寫進檔案而不是管線：逾時時不會因殘留的 robocopy 子程序握著管線而卡住測試。
        with out_file.open("wb") as out:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(pkg / "install.ps1"),
                 "-InstallDir", str(tmp_path / "inst"), "-DesktopDir", str(tmp_path / "desk"),
                 "-StartMenuDir", str(tmp_path / "start"), "-NoLaunch", "-Quiet"],
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
