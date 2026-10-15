"""demo API 契约回归：端点行为、output 校验、任务状态机。

转换器以 stub 替换——契约测试不依赖真实引擎、存储与模型；
断言的契约口径见 demo/README.md §接口行为。
"""
import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

# demo 后端是扁平目录（非包），按路径导入；
# DEMO_DB 指向测试专用临时文件，须在导入 main 前就位
_BACKEND = Path(__file__).resolve().parents[1] / "demo" / "backend"
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


def _wait_terminal(client, task_id: str, timeout: float = 5.0) -> dict:
    """轮询任务详情直至终态（stub 转换毫秒级完成）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/tasks/{task_id}").json()
        if detail["status"] in ("done", "failed"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内到达终态")


def _upload(client, name="a.txt", content=b"hello", output="url,text"):
    return client.post(
        "/api/tasks",
        files={"file": (name, content, "text/plain")},
        data={"output": output},
    )


# ---------- 探活与能力数据分离 ----------

def test_health_is_liveness_only(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_formats_endpoint(client):
    formats = client.get("/api/formats").json()["formats"]
    assert ".pdf" in formats
    assert formats == sorted(formats)


# ---------- 上传校验 ----------

def test_rejects_unsupported_format(client):
    resp = _upload(client, name="b.xyz", content=b"x")
    assert resp.status_code == 400
    assert ".pdf" in resp.json()["detail"]


def test_rejects_invalid_output(client):
    resp = _upload(client, output="all")
    assert resp.status_code == 400
    assert "url, text" in resp.json()["detail"]


def test_rejects_empty_file(client):
    resp = _upload(client, content=b"")
    assert resp.status_code == 400


# ---------- 任务生命周期 ----------

def test_task_lifecycle(client):
    resp = _upload(client, output="text")
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["output"] == "text"
    assert "output=text" in detail["markdown"]
    assert detail["image_count"] == 1 and detail["char_count"] == 20
    assert detail["cache_hit"] is True           # 口径：耗时 < 2s 且有图片（stub 7ms）

    items = client.get("/api/tasks", params={"q": "a.txt"}).json()["items"]
    assert any(t["task_id"] == task_id for t in items)
    assert all("markdown" not in t for t in items)   # 列表不带 markdown（性能纪律）

    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_failed_task_transparent_error(client, monkeypatch):
    import tasks
    monkeypatch.setattr(tasks, "get_converter", lambda: StubConverter(fail=True))
    task_id = _upload(client, output="url").json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "failed"
    assert "引擎故障" in detail["error"]          # 原始异常透传不改写
    client.delete(f"/api/tasks/{task_id}")        # 测试间清理


def test_delete_missing_returns_404(client):
    """删除不存在的任务 → 404"""
    assert client.delete("/api/tasks/" + "0" * 32).status_code == 404


def test_concurrency_limit_rejects_with_429(client, monkeypatch):
    """并发占满（running+pending ≥ 3）→ 新上传 429"""
    import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "count_active", lambda: tasks_mod.MAX_CONCURRENT)
    resp = _upload(client)
    assert resp.status_code == 429
    assert "并发任务已满" in resp.json()["detail"]
