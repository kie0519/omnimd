"""demo 测试专属夹具：后端路径注入、测试专用 SQLite、stub 转换器与 client。

demo 后端是扁平目录（非包），按路径导入；DEMO_DB 指向测试专用临时文件，
须在导入 main 前就位——conftest 先于测试收集执行，正好承载这个时序。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "demo" / "backend"
_TEST_DIR = tempfile.mkdtemp(prefix="omnimd-demo-test-")
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)   # 退出即清
os.environ["DEMO_DB"] = os.path.join(_TEST_DIR, "test.db")
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient

from omnimd.converter import ConversionResult


class StubConverter:
    """固定产出的假转换器：fail=True 时抛错，验证失败透传"""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def convert(self, path, output=None):
        if self.fail:
            raise RuntimeError("引擎故障（stub）")
        return ConversionResult(
            markdown=f"# stub\n\noutput={output}\n",
            images=[],
            image_count=1, char_count=20, elapsed_ms=7,
        )


@pytest.fixture()
def client(monkeypatch):
    import tasks
    monkeypatch.setattr(tasks, "get_converter", lambda: StubConverter())
    import main
    with TestClient(main.app) as c:
        yield c
