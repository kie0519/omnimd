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

    def convert(self, path, *, images=None, transcribe=False, describe=False,
                prompt=None):
        if self.fail:
            raise RuntimeError("引擎故障（stub）")
        # prompt 形态：str（即时文本）或 [TaskPrompt]（带 system 的即时任务）——
        # 两者都构成 prompt 任务标记
        has_prompt = bool(prompt) and (
            isinstance(prompt, str) or len(prompt) > 0
        )
        options = "+".join(
            [images or "drop"]
            + (["transcribe"] if transcribe else [])
            + (["describe"] if describe else [])
            + (["prompt"] if has_prompt else [])
        )
        return ConversionResult(
            markdown=f"# stub\n\noptions={options}\n",
            images=[],
            image_count=1, char_count=20, elapsed_ms=7,
        )


@pytest.fixture()
def client(monkeypatch):
    import tasks
    monkeypatch.setattr(tasks, "get_converter", lambda: StubConverter())
    # 流式生成端点直连 vlm 层：哑凭据 + 临时缓存目录（不触网、不写真缓存）
    monkeypatch.setenv("VLM_API_KEY", "key")
    monkeypatch.setenv("VLM_BASE_URL", "https://vlm.example/v1")
    monkeypatch.setenv("VLM_MODEL", "vision-model")
    monkeypatch.setenv("CACHE_DIR", os.path.join(_TEST_DIR, "cache"))
    import main
    with TestClient(main.app) as c:
        yield c


class FakeStreamVlm:
    """流式假模型：逐段产出固定文本并回报用量（不触网）"""

    def run_stream(self, request, image_ref, on_usage=None):
        pieces = ["# 走查", "报告", chr(10), "布局结构…"]
        for piece in pieces:
            yield piece
        if on_usage is not None:
            from omnimd.vlm.base import Usage
            on_usage(Usage(prompt_tokens=980, completion_tokens=1120,
                           total_tokens=2100))


@pytest.fixture()
def fake_stream(monkeypatch):
    """把流式生成端点的 vlm 构造替换为假模型（命中 omnimd.vlm.create_vlm）"""
    import omnimd.vlm
    monkeypatch.setattr(omnimd.vlm, "create_vlm", lambda config: FakeStreamVlm())
