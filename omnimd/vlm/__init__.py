"""VLM 视觉模型抽象层

统一契约：ocr(image_ref) / describe(image_ref)——image_ref 是公开 URL
或 base64 data URI（按 VLM_IMAGE_INPUT 而定）；返回该视觉任务的文本产物。

本层只实现一种协议：OpenAI Chat Completions + image_url，任意 OpenAI
兼容端点（OpenAI / DeepSeek / GLM / 本地 Ollama / vLLM / 聚合中转站）
由凭据三项（KEY / URL / MODEL）直接接入，无厂商概念。思考控制方式由
VLM_THINKING 配置，参数表定义在 thinking.py（config 层校验从它导入）。

依赖方向约束：本包 __init__ 不导入骨架类——骨架依赖 config，而
config 校验取值表时要导入本包，先导入骨架会循环。骨架放工厂函数里
导入。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .thinking import THINKING_EXTRA_BODY

if TYPE_CHECKING:
    from ..config import Config
    from .base import VlmClient

__all__ = ["THINKING_EXTRA_BODY", "create_vlm"]


def create_vlm(config: Config) -> VlmClient:
    """构造视觉模型客户端（VLM_THINKING 合法性由 config 校验保证）"""
    from .openai_compat import OpenAICompatVlm
    return OpenAICompatVlm(config)
