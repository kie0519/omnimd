"""接口契约的类型定义：Pydantic 响应模型与字段语义。"""

from pydantic import BaseModel


class TaskCreated(BaseModel):
    """POST /api/tasks 202 响应：任务已受理"""

    task_id: str
    filename: str
    status: str = "pending"


class TaskSummary(BaseModel):
    """列表项（GET /api/tasks）。进行中记录的统计字段为 null。"""

    task_id: str
    filename: str
    status: str                  # pending / running / done / failed
    output: str                  # 产出组成（body,url,base64,ocr,alt 规范序 CSV，上传时选定）
    image_count: int | None = None    # done 时统计：产出中的图片块数
    char_count: int | None = None     # done 时统计：产出字符数
    elapsed_ms: int | None = None     # done/failed 时统计：转换耗时（failed 记录失败前耗时）
    cache_hit: bool | None = None     # done 时判定：耗时 < 2s 且有图片，图片文件输入且产出不含转写/描述的除外（口径见 tasks.py）
    created_at: str               # ISO 8601 UTC（T 分隔、Z 后缀）


class TaskDetail(TaskSummary):
    """任务详情（GET /api/tasks/{id}）。

    markdown 仅 done 返回；error 仅 failed 返回（omnimd 原始错误信息，
    透传不改写）。
    """

    size_bytes: int
    markdown: str | None = None
    error: str | None = None
