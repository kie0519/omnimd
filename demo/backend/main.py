"""应用入口：路由、静态挂载、启动钩子。

路由函数只做 HTTP 编排（校验、状态码、响应模型）；
SQL 与状态机在 tasks.py，字段语义注释在 models.py。
"""

import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from omnimd.engines import IMAGE_EXTENSIONS, supported_extensions
from omnimd.exceptions import OmniMDError

import tasks
from models import TaskCreated, TaskDetail, TaskSummary

# 上传上限（字节）：demo 边界，非库限制（前端另持同值，改值需两侧同步）
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# 自定义提示词长度上限：demo 边界，非库限制（前端编辑区计数同值）
MAX_PROMPT_CHARS = 10000

_SUPPORTED = frozenset(supported_extensions())
_IMAGE_ONLY = frozenset(IMAGE_EXTENSIONS)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DB_PATH = os.environ.get("DEMO_DB") or str(_DATA_DIR / "demo.db")

# 前端构建产物；存在时挂载（生产模式单进程完整演示）
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn 只配置自身 logger——补 root handler 使应用与 omnimd 的 INFO 日志
    # （缓存目录落点、缓存停用警告等）能进 backend.log；已配置时为 no-op
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:%(name)s:%(message)s")
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tasks.init_db(_DB_PATH)
    tasks.cleanup_zombies()
    tasks.cleanup_stale_temp_files()
    tasks.prewarm()
    # 缓存落点可见性：启动日志打印生效的缓存目录（配置问题不阻断启动，转换时才报）
    try:
        from omnimd.config import load as _load_omnimd_config
        logging.getLogger(__name__).info(
            "omnimd 缓存目录 → %s",
            _load_omnimd_config().cache_dir.resolve())
    except Exception as e:
        logging.getLogger(__name__).warning(
            "omnimd 配置读取失败（首次转换时将报错）：%s", e)
    yield


# API 子应用：/api 命名空间整体交给它（app.mount("/api", ...)）
# ——命名空间内未匹配的路径与方法由它自己返回 404 JSON。
# API 文档随子应用在 /api/docs
api_app = FastAPI(title="omnimd demo API")


def _summary(row) -> TaskSummary:
    return TaskSummary(
        task_id=row["id"], filename=row["filename"], status=row["status"],
        options=row["options"],
        image_count=row["image_count"], char_count=row["char_count"],
        elapsed_ms=row["elapsed_ms"],
        cache_hit=bool(row["cache_hit"]) if row["cache_hit"] is not None else None,
        created_at=row["created_at"],
    )


@api_app.get("/health")
def health() -> dict:
    # 纯探活：给部署检查与前端离线检测反复调用，不携带业务数据
    return {"status": "ok"}


@api_app.get("/formats")
def formats() -> dict:
    # 能力数据：支持扩展名清单
    return {"formats": sorted(_SUPPORTED)}


@api_app.post("/tasks", status_code=202)
async def create_task(
    file: UploadFile,
    images: str | None = Form(None),
    transcribe: bool = Form(False),
    describe: bool = Form(False),
    prompt: str = Form(""),
    system: str = Form(""),
) -> TaskCreated:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED:
        raise HTTPException(
            400,
            f"不支持的文件格式：{suffix or '（无扩展名）'}"
            f"（可选：{', '.join(sorted(_SUPPORTED))}）",
        )
    if images is not None and images not in ("link", "embed"):
        raise HTTPException(400, f"不支持的 images 取值：{images}（可选：link、embed）")
    if describe and images is None:
        raise HTTPException(
            400, "describe 填在图片的文字说明位，要先选图片保留方式——images 传 link 或 embed",
        )
    prompt = (prompt or "").strip()
    system = (system or "").strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            400, f"自定义提示词太长（≤ {MAX_PROMPT_CHARS} 字）。请精简后重试。"
        )
    if len(system) > MAX_PROMPT_CHARS:
        raise HTTPException(
            400, f"系统提示词太长（≤ {MAX_PROMPT_CHARS} 字）。请精简后重试。"
        )
    if tasks.count_active() >= tasks.MAX_CONCURRENT:
        raise HTTPException(
            429,
            f"同时进行的转换最多 {tasks.MAX_CONCURRENT} 个，现在已满。"
            f"请等其中一个完成后再试。",
        )
    # 流式分块读取：先按 Content-Length 预检，再边读边校验——超大上传在读满上限时即刻终止
    declared = file.size or 0
    if declared > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。请压缩或拆分后重试。"
        )
    # 边读边写盘：不把请求体聚合进内存，内存占用与上传大小无关。
    # 读循环异常路径也清理临时文件（客户端中途断开时 read 抛错）
    tmp_path = Path(tempfile.gettempdir()) / f"omnimd-demo-{uuid.uuid4().hex}{suffix}"
    received = 0
    oversize = False
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    oversize = True
                    break
                out.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if oversize:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。请压缩或拆分后重试。"
        )
    if not received:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, "文件为空，无法转换。请检查文件内容后重新上传。")

    try:
        task_id = tasks.create_task(
            filename, received, tmp_path,
            images=images, transcribe=transcribe, describe=describe,
            prompt=prompt, system=system,
        )
    except tasks.ConcurrencyLimitError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            429,
            f"同时进行的转换最多 {tasks.MAX_CONCURRENT} 个，现在已满。"
            f"请等其中一个完成后再试。",
        )
    return TaskCreated(task_id=task_id, filename=filename)


_DEMO_ROOT = Path(__file__).resolve().parent.parent

# 模板卡片的展示文案（UI 元数据——提示词本体一处定义：内置母本在库注册表，
# 场景任务文件在 demo/prompts/，与 CLI --prompt <任务名> 同一发现机制）
# 卡片文案一处定义于产品层文档（模板卡片节）——展示层从此取值，不另行措辞
_TEMPLATE_META = {
    "transcribe": ("完整转写", "图片里的全部信息变成文字，可搜索可引用"),
    "describe": ("一句话描述", "一句话说清这张图是什么"),
    "ui-audit": ("界面走查", "逐项检查界面，给出按轻重排列的问题清单"),
    "chart-insight": ("图表解读", "读出数据、趋势和异常，告诉你该注意什么"),
    "code-extract": ("代码提取", "截图里的代码原样变成文字，复制就能用"),
    "error-diagnosis": ("报错诊断", "看懂报错的原因，给出排查方向"),
    "design-to-code": ("设计转码", "界面截图变成网页代码，复制即可运行"),
    "ui-to-prompt": ("设计转提示词", "把界面变成一段提示词，交给 AI 复现它"),
    "design-spec": ("设计规格", "整理出界面的颜色、字体、间距规格清单"),
    "ui-describe": ("界面长描述", "为看不见图的人写一段完整的描述"),
}


@api_app.get("/prompt/templates")
def prompt_templates() -> dict:
    """工作台的四枚模板卡片：提示词本体一处定义——内置母本（registry）
    + demo/prompts/ 任务文件（工作目录发现，与 CLI --prompt <任务名> 同一
    机制）。点开卡片即见全文：「内置任务就是一段提示词」被亲眼看见。
    """
    from omnimd.prompts import GENERAL_SYSTEM_PROMPT, build_registry
    registry = build_registry(workdir=_DEMO_ROOT)
    return {
        "general_system": GENERAL_SYSTEM_PROMPT,
        "templates": [
            {
                "name": name,
                "label": label,
                "blurb": blurb,
                "prompt": registry[name].prompt,
                "system": registry[name].system,
            }
            for name, (label, blurb) in _TEMPLATE_META.items()
        ]
    }


@api_app.post("/prompt/run")
def run_prompt(file: UploadFile, prompt: str = Form(""), system: str = Form("")):
    """提示词工作台：流式生成——SSE 逐帧输出（delta 帧），done 帧收尾。

    左写右看的调试心智：输出逐字滚出而非整段等待。工作台动作而非正式
    转换——不进任务队列与历史、不占并发位；完成后落任务缓存（任务名由
    编译层规则从提示词派生——正式转换同一提示词直接命中，生成即预热）。
    图片以 base64 直传（无存储依赖，本地端点兼容）。def（线程池）执行。
    """
    import base64 as _b64
    import hashlib as _hl
    import json as _json
    import time as _time
    from fastapi.responses import StreamingResponse
    from omnimd.config import load
    from omnimd.images import ImageAssetStore
    from omnimd.prompts import TaskSpec, adhoc_task_name, effective_system
    from omnimd.vlm import create_vlm
    from omnimd.vlm.base import VisionRequest

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _IMAGE_ONLY:
        raise HTTPException(
            400, f"生成只支持图片文件（可选：{', '.join(sorted(_IMAGE_ONLY))}）"
        )
    prompt = (prompt or "").strip()
    system = (system or "").strip()
    if not prompt:
        raise HTTPException(400, "提示词不能为空。请先在左侧写下提示词。")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            400, f"提示词太长（≤ {MAX_PROMPT_CHARS} 字）。请精简后重试。"
        )
    if len(system) > MAX_PROMPT_CHARS:
        raise HTTPException(
            400, f"系统提示词太长（≤ {MAX_PROMPT_CHARS} 字）。请精简后重试。"
        )
    data = file.file.read()
    if not data:
        raise HTTPException(400, "文件为空，无法生成。请检查文件内容后重新选择。")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。请压缩后重试。"
        )
    try:
        config = load()
        config.validate_vlm()      # 凭据缺失在发流之前同步报出（400 而非流内 error）
    except OmniMDError as e:
        raise HTTPException(400, str(e))

    sha = _hl.sha256(data).hexdigest()
    # 即时任务：名从内容派生（一处定义），system 进 digest 天然隔离缓存
    spec = TaskSpec(name=adhoc_task_name(prompt), prompt=prompt, system=system)
    image_ref = f"data:image/png;base64,{_b64.b64encode(data).decode()}"
    request = VisionRequest(
        system=effective_system(spec),
        user=prompt,
        max_tokens=config.vlm_max_tokens,
    )

    def _frame(payload: dict) -> str:
        # SSE 帧：data: <json> + 空行分隔
        return f"data: {_json.dumps(payload, ensure_ascii=False)}" + "\n\n"

    def generate():
        started = _time.perf_counter()
        vlm = create_vlm(config)
        chunks: list[str] = []
        usage_holder: dict = {}

        def _capture(u) -> None:
            usage_holder.update(
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                total_tokens=u.total_tokens,
            )

        try:
            for delta in vlm.run_stream(request, image_ref, on_usage=_capture):
                chunks.append(delta)
                yield _frame({"delta": delta})
            text = "".join(chunks).strip()
            # 生成即预热：与正式转换共享任务缓存（同任务名 + 同图内容哈希）
            store = ImageAssetStore(config, config.cache_dir, None, tasks=[spec])
            store.save_cached_text(spec, sha, text)
            elapsed = int((_time.perf_counter() - started) * 1000)
            yield _frame({
                "done": True, "markdown": text, "elapsed_ms": elapsed,
                "usage": usage_holder or None,   # 端点未返回用量时为 null
            })
        except OmniMDError as e:
            yield _frame({"error": str(e)})
        except Exception as e:
            yield _frame({"error": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@api_app.get("/tasks")
def list_tasks(q: str = "", limit: int = 20, offset: int = 0) -> dict:
    limit = max(1, min(limit, 100))
    rows, total = tasks.list_tasks(q=q, limit=limit, offset=offset)
    return {"items": [_summary(r) for r in rows], "total": total}


@api_app.get("/tasks/{task_id}")
def get_task(task_id: str) -> TaskDetail:
    row = tasks.get_task(task_id)
    if row is None:
        raise HTTPException(404, "任务不存在。")
    detail = TaskDetail(
        task_id=row["id"], filename=row["filename"], status=row["status"],
        options=row["options"],
        image_count=row["image_count"], char_count=row["char_count"],
        elapsed_ms=row["elapsed_ms"],
        cache_hit=bool(row["cache_hit"]) if row["cache_hit"] is not None else None,
        created_at=row["created_at"], size_bytes=row["size_bytes"],
        markdown=row["markdown"], error=row["error"],
        prompt=row["prompt"], system=row["system"],
    )
    return detail


@api_app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str):
    if not tasks.delete_task(task_id):
        raise HTTPException(404, "任务不存在。")


# 主应用：只有两个挂载（/api 子应用 + / 静态），无自身路由——docs 关闭避免空文档页；API 文档见子应用的 /api/docs
app = FastAPI(title="omnimd demo", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# 前端构建产物由本服务托管（同源）。
# 跨源读写经 CORS_ORIGINS 收口：缺省放行本机回环（浏览器里从其他源调用 demo API 的常用形态），多个来源用逗号分隔；
# 设为 * 才全放开（明示选择宽面）
_origins = os.getenv("CORS_ORIGINS", "http://localhost:8300,http://127.0.0.1:8300")
if _origins.strip() == "*":
    _allow_origins: list[str] | str = "*"
else:
    _allow_origins = [o.strip() for o in _origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.mount("/api", api_app)


class SPAStaticFiles(StaticFiles):
    """未匹配路径回 index.html（SPA 深链支持）。

    文件未命中时 StaticFiles 以 HTTPException(404) 上抛（非返回 404 响应），回退逻辑须捕获异常而非检查响应码。
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


if _DIST.exists():
    # 挂整个 dist：html=True 命中 /（index.html）；/assets/* 与带 hash 的构建产物照常服务；favicon 等根级静态文件（Vite public/ 的落点）同源可达
    app.mount("/", SPAStaticFiles(directory=_DIST, html=True), name="frontend")
