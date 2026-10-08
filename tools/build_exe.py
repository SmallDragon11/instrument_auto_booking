"""建置可交付的 zip：圖示 → PyInstaller（onedir、無主控台）→ 自我檢查 → 加上安裝腳本與使用說明 → 壓縮。

用法（專案根目錄）：.venv/Scripts/python tools/build_exe.py
產物：dist/ExperimentPlanner-<版本>.zip（暫存檔在 build/、dist/，皆不進 git）。
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # 未以 pip install -e 安裝時也能匯入 instrument_booking（產生圖示用）
APP_NAME = "ExperimentPlanner"
DISPLAY_NAME = "實驗規劃助手"
INSTALL_FILES = ("install.ps1", "安裝.cmd", "uninstall.ps1", "解除安裝.cmd")
GUIDE_SOURCE = Path("docs") / "使用說明.md"
GUIDE_NAME = "使用說明.txt"


def project_version(root: Path = ROOT) -> str:
    with open(root / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """「0.1.0」→ (0, 1, 0, 0)：Windows 版本資訊固定四段。"""
    parts = [int(x) for x in version.split(".")]
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"版本號格式錯誤：{version}")
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def version_file_text(version: str) -> str:
    """PyInstaller --version-file 的內容：工作管理員等處顯示「實驗規劃助手」。"""
    numbers = version_tuple(version)
    strings = [("FileDescription", DISPLAY_NAME), ("ProductName", DISPLAY_NAME), ("FileVersion", version),
               ("ProductVersion", version), ("InternalName", APP_NAME), ("OriginalFilename", f"{APP_NAME}.exe")]
    table = ", ".join(f"StringStruct('{k}', '{v}')" for k, v in strings)
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,\n"
        "                    fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        f"  kids=[StringFileInfo([StringTable('040404B0', [{table}])]),\n"
        "        VarFileInfo([VarStruct('Translation', [0x0404, 1200])])]\n"
        ")\n"
    )


def pyinstaller_args(root: Path, build_dir: Path, dist_dir: Path, icon: Path, version_file: Path) -> list[str]:
    return ["--noconfirm", "--clean", "--windowed", "--name", APP_NAME, "--icon", str(icon),
            "--version-file", str(version_file), "--paths", str(root / "src"),
            "--distpath", str(dist_dir), "--workpath", str(build_dir / "work"), "--specpath", str(build_dir),
            str(root / "tools" / "launch.py")]


def write_icon(path: Path) -> None:
    """以 App 圖示（程式繪製）輸出 256×256 的 .ico；需要 Qt。"""
    from PySide6.QtGui import QGuiApplication

    from instrument_booking.app.icon import app_icon

    _app = QGuiApplication.instance() or QGuiApplication([])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not app_icon().pixmap(256, 256).save(str(path), "ICO"):
        raise RuntimeError(f"無法寫入圖示：{path}")


def package_zip(app_dir: Path, root: Path, out_zip: Path, version: str) -> list[str]:
    """把程式資料夾、安裝腳本與使用說明壓縮成 zip；回傳 zip 內的檔名。"""
    top = f"{APP_NAME}-{version}"
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, f"{top}/{APP_NAME}/{f.relative_to(app_dir).as_posix()}")
        for name in INSTALL_FILES:
            z.write(root / "tools" / name, f"{top}/{name}")
        z.write(root / GUIDE_SOURCE, f"{top}/{GUIDE_NAME}")
        return z.namelist()


def clean_previous_outputs(build_dir: Path, dist_dir: Path, version: str) -> None:
    """建置前刪掉上次的 zip 與自我檢查結果，避免這次失敗時殘留舊檔被誤認為成功。"""
    (dist_dir / f"{APP_NAME}-{version}.zip").unlink(missing_ok=True)
    (build_dir / "self-check.txt").unlink(missing_ok=True)


def main() -> int:
    version = project_version()
    build_dir, dist_dir = ROOT / "build", ROOT / "dist"
    clean_previous_outputs(build_dir, dist_dir, version)
    icon, version_file = build_dir / f"{APP_NAME}.ico", build_dir / "version_info.txt"
    write_icon(icon)
    version_file.write_text(version_file_text(version), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller",
                    *pyinstaller_args(ROOT, build_dir, dist_dir, icon, version_file)], check=True)
    app_dir = dist_dir / APP_NAME
    result = build_dir / "self-check.txt"
    check = subprocess.run([str(app_dir / f"{APP_NAME}.exe"), "--self-check", str(result)], timeout=120)
    print("自我檢查：", result.read_text(encoding="utf-8").strip() if result.exists() else "（沒有結果）")
    if check.returncode != 0:
        print("自我檢查失敗，不產生 zip")
        return 1
    out_zip = dist_dir / f"{APP_NAME}-{version}.zip"
    names = package_zip(app_dir, ROOT, out_zip, version)
    print(f"完成：{out_zip}（{len(names)} 個檔案，{out_zip.stat().st_size / 1e6:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
