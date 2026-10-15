"""OpenAI 兼容 VLM 公共骨架

VLM 层只实现一种协议：OpenAI Chat Completions + image_url 内容块
（公开 URL 与 base64 data URI 两种标准形式，传哪种由 converter 按
VLM_IMAGE_INPUT 决定，本层不做选择）。思考控制方式由 VLM_THINKING
配置选择（见 thinking.py）。

本层是瘦客户端：调用并返回文本内容，API 异常透传不改写；内容为空
的处置不在本层——与上传失败同走图片失败渲染。
"""
from openai import OpenAI

from ..config import Config
from ..prompts import (
    ALT_SYSTEM_PROMPT,
    ALT_USER_PROMPT,
    OCR_SYSTEM_PROMPT,
    OCR_USER_PROMPT,
)
from .base import VlmClient
from .thinking import THINKING_EXTRA_BODY

# alt 任务的输出上限：一句话描述；转写任务跟配置走（VLM_MAX_TOKENS）
_ALT_MAX_TOKENS = 512


class OpenAICompatVlm(VlmClient):
    """OpenAI 兼容 chat/completions 协议的视觉模型实现。"""

    def __init__(self, config: Config):
        self.config = config
        self.client = OpenAI(api_key=config.vlm_api_key, base_url=config.vlm_base_url)
        self._thinking_extra_body = THINKING_EXTRA_BODY[config.vlm_thinking]

    def ocr(self, image_ref: str) -> str:
        """转写单张图片，返回 Markdown 文本。"""
        return self._complete(
            system=OCR_SYSTEM_PROMPT, user=OCR_USER_PROMPT,
            image_ref=image_ref, max_tokens=self.config.vlm_max_tokens,
        )

    def describe(self, image_ref: str) -> str:
        """生成图片 alt 文本，一句话。"""
        return self._complete(
            system=ALT_SYSTEM_PROMPT, user=ALT_USER_PROMPT,
            image_ref=image_ref, max_tokens=_ALT_MAX_TOKENS,
        )

    def _complete(
        self, system: str, user: str, image_ref: str, max_tokens: int,
    ) -> str:
        """单次视觉任务，返回文本内容（空白归一为空串）。

        image_ref 是公开 URL 或 base64 data URI；API 异常原样上抛。
        """
        response = self.client.chat.completions.create(
            model=self.config.vlm_model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_ref, "detail": "high"},
                        },
                        {"type": "text", "text": user},
                    ],
                },
            ],
            max_tokens=max_tokens,
            extra_body=self._thinking_extra_body,
        )
        # choices 可能为空（内容过滤、部分网关的错误语义）——按空内容处置，
        # 与 content 为 None 同走图片失败渲染
        content = response.choices[0].message.content if response.choices else None
        return (content or "").strip()
