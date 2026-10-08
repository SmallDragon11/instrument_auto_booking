import zipfile
from pathlib import Path

import pytest


def test_project_version_and_windows_version_tuple(build_exe):
    assert build_exe.project_version() == "0.1.0"
    assert build_exe.version_tuple("0.1.0") == (0, 1, 0, 0)
    assert build_exe.version_tuple("1.2.3.4") == (1, 2, 3, 4)
    with pytest.raises(ValueError):
        build_exe.version_tuple("1.2.3.4.5")


def test_version_file_shows_chinese_name_and_numbers(build_exe):
    text = build_exe.version_file_text("0.1.0")
    assert "filevers=(0, 1, 0, 0)" in text
    assert "StringStruct('FileDescription', '實驗規劃助手')" in text
    assert "StringStruct('OriginalFilename', 'ExperimentPlanner.exe')" in text
    compile(text, "version_info", "eval")  # PyInstaller 以 Python 運算式讀取


def test_pyinstaller_args_build_windowed_onedir_named_experiment_planner(build_exe, tmp_path):
    args = build_exe.pyinstaller_args(tmp_path, tmp_path / "build", tmp_path / "dist", tmp_path / "a.ico",
                                      tmp_path / "v.txt")
    assert "--windowed" in args and "--onefile" not in args
    assert args[args.index("--name") + 1] == "ExperimentPlanner"
    assert args[args.index("--paths") + 1] == str(tmp_path / "src")
    assert args[args.index("--specpath") + 1] == str(tmp_path / "build")  # .spec 不放在專案根目錄
    assert args[-1] == str(tmp_path / "tools" / "launch.py")


def test_write_icon_produces_an_ico_file(build_exe, tmp_path, qapp):
    path = tmp_path / "icons" / "app.ico"
    build_exe.write_icon(path)
    assert path.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_package_zip_contains_app_installer_and_guide(build_exe, tmp_path):
    app = tmp_path / "dist" / "ExperimentPlanner"
    (app / "_internal").mkdir(parents=True)
    (app / "ExperimentPlanner.exe").write_bytes(b"MZ")
    (app / "_internal" / "lib.dll").write_bytes(b"x")
    names = build_exe.package_zip(app, build_exe.ROOT, tmp_path / "out.zip", "0.1.0")
    assert sorted(names) == [
        "ExperimentPlanner-0.1.0/ExperimentPlanner/ExperimentPlanner.exe",
        "ExperimentPlanner-0.1.0/ExperimentPlanner/_internal/lib.dll",
        "ExperimentPlanner-0.1.0/install.ps1",
        "ExperimentPlanner-0.1.0/使用說明.txt",
        "ExperimentPlanner-0.1.0/安裝.cmd",
    ]
    with zipfile.ZipFile(tmp_path / "out.zip") as z:
        assert z.read("ExperimentPlanner-0.1.0/使用說明.txt") == (build_exe.ROOT / "docs" / "使用說明.md").read_bytes()


def test_clean_previous_outputs_removes_old_zip_and_self_check(build_exe, tmp_path):
    build_dir, dist_dir = tmp_path / "build", tmp_path / "dist"
    build_dir.mkdir()
    dist_dir.mkdir()
    (dist_dir / "ExperimentPlanner-0.1.0.zip").write_bytes(b"old")
    (dist_dir / "ExperimentPlanner-0.0.9.zip").write_bytes(b"other version")
    (build_dir / "self-check.txt").write_text("正常", encoding="utf-8")
    build_exe.clean_previous_outputs(build_dir, dist_dir, "0.1.0")
    assert not (dist_dir / "ExperimentPlanner-0.1.0.zip").exists()
    assert not (build_dir / "self-check.txt").exists()
    assert (dist_dir / "ExperimentPlanner-0.0.9.zip").exists()
    build_exe.clean_previous_outputs(build_dir, dist_dir, "0.1.0")  # 檔案不存在時不報錯
