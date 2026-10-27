"""demo API 契约回归：端点行为、选项校验、任务状态机。

转换器与夹具见同目录 conftest.py（stub 替换——不依赖真实引擎、存储与模型）。
"""
import time
from pathlib import Path

from tests.demo.conftest import StubConverter


def _wait_terminal(client, task_id: str, timeout: float = 5.0) -> dict:
    """轮询任务详情直至终态（stub 转换毫秒级完成）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/tasks/{task_id}").json()
        if detail["status"] in ("done", "failed"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内到达终态")


def _upload(client, name="a.txt", content=b"hello", **options):
    data = {"transcribe": str(options.get("transcribe", False)).lower(),
            "describe": str(options.get("describe", False)).lower()}
    if options.get("images"):
        data["images"] = options["images"]
    if options.get("prompt"):
        data["prompt"] = options["prompt"]
    if options.get("system"):
        data["system"] = options["system"]
    return client.post(
        "/api/tasks",
        files={"file": (name, content, "text/plain")},
        data=data,
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


def test_rejects_invalid_images(client):
    """images 取值域校验：link / embed 之外 400"""
    resp = _upload(client, images="url")
    assert resp.status_code == 400
    assert "link" in resp.json()["detail"] and "embed" in resp.json()["detail"]


def test_rejects_describe_without_images(client):
    """describe 依赖呈现：无 images 时 400 并给指引"""
    resp = _upload(client, describe=True)
    assert resp.status_code == 400
    assert "link" in resp.json()["detail"] and "embed" in resp.json()["detail"]


def test_rejects_empty_file(client):
    resp = _upload(client, content=b"")
    assert resp.status_code == 400


# ---------- 任务生命周期 ----------

def test_task_lifecycle(client):
    resp = _upload(client, images="embed", transcribe=True)
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["options"] == "embed+transcribe"
    assert "options=embed+transcribe" in detail["markdown"]
    assert detail["image_count"] == 1 and detail["char_count"] == 20
    assert detail["cache_hit"] is True           # cache_hit 判定：耗时 < 2s、有图片、且非「图片输入+无显式任务」（stub 7ms 满足）

    items = client.get("/api/tasks", params={"q": "a.txt"}).json()["items"]
    assert any(t["task_id"] == task_id for t in items)
    assert all("markdown" not in t for t in items)   # 列表不返回 markdown（大字段不进列表查询）

    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_default_upload_records_drop(client):
    """不传任何选项：options 记为 drop（缺省由输入类型决定）"""
    task_id = _upload(client).json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["options"] == "drop"
    client.delete(f"/api/tasks/{task_id}")


def test_describe_upload_records_options(client):
    """describe + images 的成功路径：options 规范串含 describe（400 拒绝分支另有用例）"""
    task_id = _upload(client, images="embed", describe=True).json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["options"] == "embed+describe"
    client.delete(f"/api/tasks/{task_id}")


def test_prompt_upload_records_options_and_text(client):
    """自定义提示词：options 规范串加 prompt 标记，详情回显提示词原文"""
    task_id = _upload(client, prompt="重点看配色").json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["options"] == "drop+prompt"
    assert detail["prompt"] == "重点看配色"
    client.delete(f"/api/tasks/{task_id}")


def test_rejects_overlong_prompt(client):
    """自定义提示词超长：400，报错带上限（10000 字）"""
    resp = _upload(client, prompt="长" * 10001)
    assert resp.status_code == 400
    assert "提示词太长" in resp.json()["detail"]


def test_upload_with_system_roundtrip(client):
    """带 system 的自定义任务：落库回显、以 TaskPrompt 传递（stub 回显 options 含 prompt）"""
    task_id = _upload(
        client, prompt="重点看配色",
        system="You are a senior color analyst.",
    ).json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["prompt"] == "重点看配色"
    assert detail["system"] == "You are a senior color analyst."
    client.delete(f"/api/tasks/{task_id}")


def test_rejects_overlong_system(client):
    """system 超长：400，报错带上限"""
    resp = _upload(client, prompt="重点看配色", system="长" * 10001)
    assert resp.status_code == 400
    assert "系统提示词太长" in resp.json()["detail"]


def test_prompt_run_accepts_system(client, fake_stream):
    """流式生成接受 system：正常流式（与上传共享派生名规则，缓存互认）"""
    resp = client.post(
        "/api/prompt/run",
        files={"file": ("photo.png", b"png-bytes", "image/png")},
        data={"prompt": "重点看配色", "system": "You are a senior color analyst."},
    )
    assert resp.status_code == 200
    assert '"done": true' in resp.text


def test_prompt_run_rejects_overlong_system(client):
    resp = client.post(
        "/api/prompt/run",
        files={"file": ("photo.png", b"png-bytes", "image/png")},
        data={"prompt": "重点看配色", "system": "长" * 10001},
    )
    assert resp.status_code == 400
    assert "系统提示词太长" in resp.json()["detail"]


def test_image_default_upload_not_counted_as_cache_hit(client):
    """cache_hit 口径：图片输入 + 无显式任务（缺省注入转写）亚秒完成不计命中——
    单图秒级是注册管线的常态而非缓存证据"""
    task_id = _upload(client, name="photo.png").json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "done"
    assert detail["cache_hit"] is False
    client.delete(f"/api/tasks/{task_id}")


def test_failed_task_transparent_error(client, monkeypatch):
    import tasks
    monkeypatch.setattr(tasks, "get_converter", lambda: StubConverter(fail=True))
    task_id = _upload(client, images="link").json()["task_id"]
    detail = _wait_terminal(client, task_id)
    assert detail["status"] == "failed"
    assert "引擎故障" in detail["error"]          # 原始异常透传不改写
    client.delete(f"/api/tasks/{task_id}")        # 测试间清理


def test_delete_missing_returns_404(client):
    """删除不存在的任务 → 404"""
    assert client.delete("/api/tasks/" + "0" * 32).status_code == 404


def test_unknown_api_path_404_all_methods(client):
    """未知 /api 路径对全部方法返回 404 JSON——API 命名空间由子应用自负其责，不落入 SPA 静态回退（GET 拿 200 HTML、DELETE 拿 405 都错）"""
    for method in ("get", "delete", "put", "post"):
        resp = getattr(client, method)("/api/no-such-endpoint")
        assert resp.status_code == 404, method
        assert resp.headers["content-type"].startswith("application/json")


def test_concurrency_limit_rejects_with_429(client, monkeypatch):
    """并发占满（running+pending ≥ 3）→ 新上传 429"""
    import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "count_active", lambda: tasks_mod.MAX_CONCURRENT)
    resp = _upload(client)
    assert resp.status_code == 429
    assert "现在已满" in resp.json()["detail"]

# ---------- 提示词工作台：POST /api/prompt/run ----------

def _run_prompt(client, name="photo.png", prompt="重点看配色"):
    return client.post(
        "/api/prompt/run",
        files={"file": (name, b"png-bytes", "image/png")},
        data={"prompt": prompt},
    )


def test_prompt_run_streams_deltas_and_done(client, fake_stream):
    """工作台生成：SSE 流式——delta 帧逐段输出、done 帧收尾（全文 + 耗时），不创建任务"""
    resp = _run_prompt(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert 'data: {"delta": "# 走查"}' in body          # delta 帧逐段
    assert 'data: {"delta": "布局结构…"}' in body
    assert '"done": true' in body                        # done 帧收尾
    assert '"markdown": "# 走查报告' in body             # 全文（空白归一）
    assert '"elapsed_ms":' in body
    assert '"usage"' in body and '"total_tokens": 2100' in body   # usage 随 done 帧交付
    total = client.get("/api/tasks").json()["total"]
    assert total == 0                                    # 生成不创建任务、不进历史


def test_prompt_run_missing_credentials_400(client, monkeypatch):
    """凭据缺失：发流之前同步 400（报错列出缺失项——不是流内 error 帧）"""
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    resp = _run_prompt(client)
    assert resp.status_code == 400
    assert "VLM_API_KEY" in resp.json()["detail"]


def test_prompt_run_rejects_non_image(client):
    """生成只收图片：文档扩展名 400，报错附图片格式清单"""
    resp = _run_prompt(client, name="doc.pdf")
    assert resp.status_code == 400
    assert ".png" in resp.json()["detail"]


def test_prompt_run_rejects_empty_prompt(client):
    resp = _run_prompt(client, prompt="   ")
    assert resp.status_code == 400
    assert "提示词" in resp.json()["detail"]


def test_prompt_run_rejects_overlong_prompt(client):
    resp = _run_prompt(client, prompt="长" * 10001)
    assert resp.status_code == 400
    assert "提示词太长" in resp.json()["detail"]


def test_prompt_run_stream_error_frame(client, monkeypatch):
    """生成中模型故障：流内 error 帧透传原始信息（右栏如实展示）"""

    class BoomVlm:
        def run_stream(self, request, image_ref, on_usage=None):
            raise RuntimeError("引擎故障（stream stub）")
            yield  # pragma: no cover —— 生成器语法要求

    import omnimd.vlm
    monkeypatch.setattr(omnimd.vlm, "create_vlm", lambda config: BoomVlm())
    resp = _run_prompt(client)
    assert resp.status_code == 200                      # 流已开，错误以帧交付
    assert "引擎故障" in resp.text
    assert '"error"' in resp.text


def test_prompt_templates_endpoint(client):
    """模板卡片十枚齐备（内置 2 + 场景 8）三件套完整：prompt + system（任务文件 frontmatter）+
    通用骨架原文 general_system（工作台「以骨架为起点」的数据源）"""
    from omnimd.prompts import GENERAL_SYSTEM_PROMPT, build_registry, parse_task_file
    body = client.get("/api/prompt/templates").json()
    labels = [t["label"] for t in body["templates"]]
    assert labels == ["完整转写", "一句话描述", "界面走查", "图表解读",
                      "代码提取", "报错诊断", "设计转码", "设计转提示词",
                      "设计规格", "界面长描述"]
    assert body["general_system"] == GENERAL_SYSTEM_PROMPT
    by_name = {t["name"]: t for t in body["templates"]}
    registry = build_registry()
    assert by_name["transcribe"]["prompt"] == registry["transcribe"].prompt   # 内置：一处定义
    assert by_name["describe"]["prompt"] == registry["describe"].prompt
    demo_root = Path(__file__).resolve().parents[2] / "demo"                 # 场景：任务文件同构
    for name in ("ui-audit", "chart-insight", "code-extract", "error-diagnosis",
                 "design-to-code", "ui-to-prompt", "design-spec", "ui-describe"):
        spec = parse_task_file(demo_root / "prompts" / f"{name}.md")
        assert by_name[name]["prompt"] == spec.prompt
        assert by_name[name]["system"] == spec.system                         # 三件套：system 随卡下发
    assert by_name["ui-audit"]["system"].startswith("You are a senior")
    # 卡片文案一处定义（PRD §五）：行为语言，无术语括注泄漏
    assert by_name["ui-audit"]["blurb"] == "逐项检查界面，给出按轻重排列的问题清单"
    assert by_name["design-spec"]["blurb"] == "整理出界面的颜色、字体、间距规格清单"
    for tpl in body["templates"]:
        assert "(" not in tpl["blurb"] and "+" not in tpl["blurb"]   # 无术语括注、无符号拼接
    assert "启发式核对" in by_name["ui-audit"]["prompt"]                     # 母本七节形态
    assert "逐字保真" in by_name["code-extract"]["prompt"]
    assert "DTCG" in by_name["design-spec"]["prompt"]
    assert "心理图景" in by_name["ui-describe"]["prompt"]
    assert "推断自白" in by_name["design-to-code"]["prompt"]
    assert "语义化转译" in by_name["ui-to-prompt"]["prompt"]


