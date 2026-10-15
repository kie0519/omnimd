"""应用入口：路由、静态挂载、启动钩子。

路由函数只做 HTTP 编排（校验、状态码、响应模型）；
SQL 与状态机在 tasks.py，字段语义注释在 models.py。
"""

import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile
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


app = FastAPI(title="omnimd demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",               # 开发模式 Vite；生产同源无需 CORS
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 全部收拢在本 router，/api 前缀由 include_router 一处给定；
# 前端挂载位于文件末尾 include_router 之后——路由按注册顺序匹配，
# catch-all 挂载必须晚于全部 API 注册
api = APIRouter()


def _summary(row) -> TaskSummary:
    return TaskSummary(
        task_id=row["id"], filename=row["filename"], status=row["status"],
        output=row["output"],
        image_count=row["image_count"], char_count=row["char_count"],
        elapsed_ms=row["elapsed_ms"],
        cache_hit=bool(row["cache_hit"]) if row["cache_hit"] is not None else None,
        created_at=row["created_at"],
    )


@api.get("/health")
def health() -> dict:
    # 纯探活：给部署检查与前端离线检测反复调用，不携带业务数据
    return {"status": "ok"}


@api.get("/formats")
def formats() -> dict:
    # 能力数据：支持扩展名清单，前端上传预检与格式提示行共用（改格式不改前端）
    return {"formats": sorted(_SUPPORTED)}


@api.post("/tasks", status_code=202)
async def create_task(file: UploadFile, output: str = Form("url,text")) -> TaskCreated:
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
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。"
        )
    # 边读边写盘：不把请求体聚合进内存，内存占用与上传大小无关
    tmp_path = Path(tempfile.gettempdir()) / f"omnimd-demo-{uuid.uuid4().hex}{suffix}"
    received = 0
    oversize = False
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
    if oversize:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            400, f"文件超出大小上限（≤ {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。"
        )
    if not received:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, "文件为空，无法转换。")

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


@api.get("/tasks")
def list_tasks(q: str = "", limit: int = 20, offset: int = 0) -> dict:
    limit = max(1, min(limit, 100))
    rows, total = tasks.list_tasks(q=q, limit=limit, offset=offset)
    return {"items": [_summary(r) for r in rows], "total": total}


@api.get("/tasks/{task_id}")
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


@api.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str):
    if not tasks.delete_task(task_id):
        raise HTTPException(404, "任务不存在。")


app.include_router(api, prefix="/api")


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
