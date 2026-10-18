"""应用入口：路由、静态挂载、启动钩子。

路由函数只做 HTTP 编排（校验、状态码、响应模型）；
SQL 与状态机在 tasks.py，字段语义注释在 models.py。
"""

import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from omnimd.engines import supported_extensions
from omnimd.config import parse_output
from omnimd.exceptions import ConfigError

import tasks
from models import TaskCreated, TaskDetail, TaskSummary

# 上传上限（字节）：demo 边界，前后端双重校验
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_SUPPORTED = frozenset(supported_extensions())

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DB_PATH = os.environ.get("DEMO_DB") or str(_DATA_DIR / "demo.db")

# 前端构建产物；存在时挂载（生产模式单进程完整演示）
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tasks.init_db(_DB_PATH)
    tasks.cleanup_zombies()
    tasks.cleanup_stale_temp_files()
    tasks.prewarm()
    yield


# API 子应用：/api 命名空间整体交给它（app.mount("/api", ...)）——
# 命名空间内未匹配的路径与方法由它自己返回 404 JSON，与根上的
# SPA 静态兜底互不越界。API 文档随子应用在 /api/docs
api_app = FastAPI(title="omnimd demo API")


def _summary(row) -> TaskSummary:
    return TaskSummary(
        task_id=row["id"], filename=row["filename"], status=row["status"],
        output=row["output"],
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
    # 能力数据：支持扩展名清单，前端上传预检与格式提示行共用（改格式不改前端）
    return {"formats": sorted(_SUPPORTED)}


@api_app.post("/tasks", status_code=202)
async def create_task(file: UploadFile, output: str = Form("body")) -> TaskCreated:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED:
        raise HTTPException(
            400,
            f"不支持的文件格式：{suffix or '（无扩展名）'}"
            f"（可选：{', '.join(sorted(_SUPPORTED))}）",
        )
    try:
        output = parse_output(output)
    except ConfigError as e:
        raise HTTPException(400, str(e))
    if tasks.count_active() >= tasks.MAX_CONCURRENT:
        raise HTTPException(
            429,
            f"并发任务已满（同时进行 ≤ {tasks.MAX_CONCURRENT}）。"
            f"请等待当前任务完成后重试。",
        )
    # 流式分块读取：先按 Content-Length 预检，再边读边校验——超大
    # 上传在读满上限时即刻终止，不把整个请求体读进内存
    declared = file.size or 0
    if declared > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。请压缩或拆分后重试。"
        )
    # 边读边写盘：不把请求体聚合进内存，内存占用与上传大小无关。
    # 读循环包 finally：客户端中途断开（read 抛错）时也清理临时文件，
    # 不等重启清扫兜底
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
        task_id = tasks.create_task(filename, received, tmp_path, output)
    except tasks.ConcurrencyLimitError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            429,
            f"并发任务已满（同时进行 ≤ {tasks.MAX_CONCURRENT}）。"
            f"请等待当前任务完成后重试。",
        )
    return TaskCreated(task_id=task_id, filename=filename)


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
        output=row["output"],
        image_count=row["image_count"], char_count=row["char_count"],
        elapsed_ms=row["elapsed_ms"],
        cache_hit=bool(row["cache_hit"]) if row["cache_hit"] is not None else None,
        created_at=row["created_at"], size_bytes=row["size_bytes"],
        markdown=row["markdown"], error=row["error"],
    )
    return detail


@api_app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str):
    if not tasks.delete_task(task_id):
        raise HTTPException(404, "任务不存在。")


# 主应用：只有两个挂载（/api 子应用 + / 静态），无自身路由——
# docs 关闭避免空文档页；API 文档见子应用的 /api/docs
app = FastAPI(title="omnimd demo", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# 前端构建产物由本服务托管（同源）。跨源读写经 CORS_ORIGINS 收口：
# 缺省放行本机回环（浏览器里从其他源调用 demo API 的常用形态），
# 多个来源用逗号分隔；设为 * 才全放开（明示选择宽面）
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
    """未匹配路径回 index.html——前端路由的深链刷新不由静态 404 截断。

    文件未命中时 StaticFiles 以 HTTPException(404) 上抛（非返回 404
    响应），回退逻辑须捕获异常而非检查响应码。
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


if _DIST.exists():
    # 挂整个 dist：html=True 命中 /（index.html）；/assets/* 与带 hash 的
    # 构建产物照常服务；favicon 等根级静态文件（Vite public/ 的落点）同源可达
    app.mount("/", SPAStaticFiles(directory=_DIST, html=True), name="frontend")
