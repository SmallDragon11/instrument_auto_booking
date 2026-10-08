"""以暫存資料夾實際執行 install.ps1（不碰真正的安裝位置、桌面與開始功能表，也不啟動程式）。"""
import shutil
import subprocess
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
    assert b"install.ps1" in (TOOLS / "安裝.cmd").read_bytes()


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
