"""tools/ 不是套件：測試以檔案路徑載入；圖示測試在背景執行 Qt。"""
import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TOOLS = Path(__file__).resolve().parents[2] / "tools"


@pytest.fixture(scope="session")
def build_exe():
    spec = importlib.util.spec_from_file_location("build_exe", TOOLS / "build_exe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
