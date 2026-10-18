"""任务领域层：SQLite 访问、状态机、后台执行。

分层纪律：本模块不出现 HTTP 概念（请求/响应/状态码归 main.py）；
SQL 只出现在本模块。上传的临时文件在任务终态删除（finally 保证）。
"""

import logging
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from omnimd import OmniMD
from omnimd.engines import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

# 单进程内 running+pending 达到该数即拒绝新任务（转换占用 VLM 配额）
MAX_CONCURRENT = 3

# cache_hit 判定口径：双层缓存命中时同输入秒级完成；
# 要求有图片，排除「没有图片的纯文本文件本来就近乎瞬时」
_CACHE_HIT_MAX_MS = 2000

_db: sqlite3.Connection | None = None
_db_lock = threading.Lock()
# 转换器实例，进程级共享：产出组成是每次 convert 的参数，一个实例服务全部任务
_md: OmniMD | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    output        TEXT NOT NULL DEFAULT 'body',  -- 产出组成（规范序 CSV）
    markdown      TEXT,
    error         TEXT,
    image_count   INTEGER,
    char_count    INTEGER,
    elapsed_ms    INTEGER,
    cache_hit     INTEGER,
    created_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
"""


def init_db(db_path: str) -> None:
    """建库建表，表已存在时跳过（可重复调用），供应用启动钩子调用"""
    global _db
    # isolation_level=None：关闭隐式事务，写路径显式 BEGIN IMMEDIATE 短事务
    _db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    _db.row_factory = sqlite3.Row
    _db.executescript(_SCHEMA)


def _write(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """短事务写入：BEGIN IMMEDIATE 立即取写锁，同一连接跨线程全部访问串行在锁内。

    语句都是毫秒级，读写一律加锁换正确性，避免多线程并发复用
    同一 sqlite3.Connection 的交错事务问题。
    """
    with _db_lock:
        _db.execute("BEGIN IMMEDIATE")
        try:
            cur = _db.execute(sql, params)
            _db.commit()
        except BaseException:
            _db.rollback()
            raise
        return cur


def _read(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _db_lock:
        return _db.execute(sql, params).fetchall()


def cleanup_stale_temp_files() -> None:
    """清扫进程硬杀遗留的上传临时文件（omnimd-demo-* 前缀，超 1 小时）。

    正常路径的清理在 _run 的 finally；进程被 kill -9 / 断电时 finally
    不执行，临时文件由下一次启动补扫。
    """
    import time as _time
    tmp_dir = Path(tempfile.gettempdir())
    cutoff = _time.time() - 3600
    for stale in tmp_dir.glob("omnimd-demo-*"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_zombies() -> None:
    """把上次进程遗留的 running/pending 行置为失败。

    进程被杀不会留下永久转圈的任务；重启后这些记录点开即是明确错误。
    """
    _write(
        "UPDATE tasks SET status='failed', error=?, "
        "finished_at=replace(datetime('now'),' ','T') || 'Z' "
        "WHERE status IN ('pending','running')",
        ("服务重启导致任务中断，请重新上传该文件转换。",),
    )


_converter_lock = threading.Lock()


def get_converter() -> OmniMD:
    """OmniMD 实例（进程级缓存，构造一次跨请求复用）。

    产出组成是 convert 的每次参数——同一个实例服务全部任务，
    引擎与模型客户端在实例内共享；缓存为磁盘缓存，跨进程共享。构造加锁：预热线程
    与首个请求线程并发时不会重复构造。
    """
    global _md
    if _md is None:
        with _converter_lock:
            if _md is None:
                _md = OmniMD()
    return _md


def prewarm() -> None:
    """后台预热转换器：解析引擎的模块导入耗时较长（markitdown 全量
    转换器），启动期先吸收掉，首个任务的耗时统计才反映真实管线成本。

    预热转换一个临时文本文件——无图片即无上传与模型调用；
    失败只记日志，不影响服务启动。
    """
    def _warm() -> None:
        tmp = Path(tempfile.gettempdir()) / f"omnimd-demo-warmup-{uuid.uuid4().hex}.txt"
        try:
            tmp.write_text("warmup", encoding="utf-8")
            get_converter().convert(tmp)
        except Exception:
            logger.exception("转换器预热失败（首个任务将承担引擎导入成本）")
        finally:
            tmp.unlink(missing_ok=True)

    threading.Thread(target=_warm, daemon=True, name="omnimd-prewarm").start()


def count_active() -> int:
    rows = _read("SELECT COUNT(*) AS n FROM tasks WHERE status IN ('pending','running')")
    return rows[0]["n"]


class ConcurrencyLimitError(Exception):
    """并发任务已满（由 INSERT 的原子计数判定）"""


def create_task(filename: str, size: int, tmp_path: Path, output: str = "body") -> str:
    """登记已落盘的上传文件为 pending 任务并投递工作线程，返回任务 ID。

    调用方负责 output 取值校验与文件落盘。并发上限判定并入同一条
    INSERT（WHERE 子查询计数）——检查与插入原子，上传请求体读取
    时段的并发请求不会击穿上限。
    """
    task_id = uuid.uuid4().hex
    try:
        cur = _write(
            "INSERT INTO tasks (id, filename, size_bytes, status, output, created_at) "
            "SELECT ?, ?, ?, 'pending', ?, replace(datetime('now'),' ','T') || 'Z' "
            "WHERE (SELECT COUNT(*) FROM tasks WHERE status IN ('pending','running')) < ?",
            (task_id, filename, size, output, MAX_CONCURRENT),
        )
        if cur.rowcount == 0:
            raise ConcurrencyLimitError(f"并发任务已满（同时进行 ≤ {MAX_CONCURRENT}）")
    except BaseException:
        tmp_path.unlink(missing_ok=True)   # 落库失败不遗留临时文件
        raise
    threading.Thread(target=_run, args=(task_id, tmp_path, output), daemon=True).start()
    return task_id


def _run(task_id: str, path: Path, output: str) -> None:
    """工作线程：状态机推进与终态写入的唯一场所"""
    started = time.perf_counter()
    try:
        _write("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
        result = get_converter().convert(path, output=output)
        markdown = result.markdown
        image_count = result.image_count
        elapsed_ms = result.elapsed_ms
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            _write(
                "UPDATE tasks SET status='failed', error=?, elapsed_ms=?, "
                "finished_at=replace(datetime('now'),' ','T') || 'Z' WHERE id=?",
                (str(e), elapsed_ms, task_id),
            )
        except Exception:
            # 失败落库自身异常（库锁/磁盘满）：记日志而非让线程裸崩；
            # 残留的 running 状态由重启时的僵尸任务清理兜底
            logger.exception("任务失败状态写入异常: %s", task_id)
        return
    finally:
        path.unlink(missing_ok=True)   # 上传文件即用即删

    # 图片文件 + 无视觉任务产出：单图只走注册+上传，亚秒即完成属常态，
    # 不参与命中判定（否则首转也显示「缓存命中」）
    is_image_input = path.suffix.lower() in IMAGE_EXTENSIONS
    eligible = image_count > 0 and not (
        is_image_input and "ocr" not in output and "alt" not in output
    )
    cache_hit = 1 if (elapsed_ms < _CACHE_HIT_MAX_MS and eligible) else 0
    _write(
        "UPDATE tasks SET status='done', markdown=?, image_count=?, "
        "char_count=?, elapsed_ms=?, cache_hit=?, "
        "finished_at=replace(datetime('now'),' ','T') || 'Z' WHERE id=?",
        (markdown, image_count, result.char_count, elapsed_ms, cache_hit, task_id),
    )


def list_tasks(q: str = "", limit: int = 20, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
    """历史列表（搜索 + 分页），created_at 倒序。

    列表查询不取 markdown（大字段不进列表，避免拖慢列表查询）。
    返回 (rows, total)。
    """
    where = ""
    params: list = []
    if q:
        where = "WHERE lower(filename) LIKE ?"
        params.append(f"%{q.lower()}%")
    total = _read(f"SELECT COUNT(*) AS n FROM tasks {where}", tuple(params))[0]["n"]
    rows = _read(
        f"SELECT id, filename, status, output, image_count, char_count, "
        f"elapsed_ms, cache_hit, created_at FROM tasks {where} "
        f"ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return rows, total


def get_task(task_id: str) -> sqlite3.Row | None:
    rows = _read("SELECT * FROM tasks WHERE id=?", (task_id,))
    return rows[0] if rows else None


def delete_task(task_id: str) -> bool:
    """整行删除（含 Markdown 产出）。返回是否存在。"""
    cur = _write("DELETE FROM tasks WHERE id=?", (task_id,))
    return cur.rowcount > 0
