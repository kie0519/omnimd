"""VLM 视觉模型抽象层

统一契约：run(VisionRequest, image_ref) -> CompletionResult——image_ref 是公开 URL
或 base64 data URI，两种形态等价透传；请求组装与任务知识在编排层，本层只认协议。

本层只实现一种协议：OpenAI Chat Completions + image_url，
任意 OpenAI 兼容端点（OpenAI / DeepSeek / GLM / 本地 Ollama / vLLM / 聚合中转站）由凭据三项（KEY / URL / MODEL）直接接入，无厂商概念。
思考控制方式由 VLM_THINKING 配置，参数表定义在 thinking.py。

依赖方向约束：骨架类不在包级导入（与 config 循环），在工厂函数内导入。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .thinking import THINKING_EXTRA_BODY

if TYPE_CHECKING:
    from ..config import Config
    from .base import VlmClient

__all__ = ["THINKING_EXTRA_BODY", "create_vlm"]


def create_vlm(config: Config) -> VlmClient:
    """构造视觉模型客户端"""
    from .openai_compat import OpenAICompatVlm
    return OpenAICompatVlm(config)
