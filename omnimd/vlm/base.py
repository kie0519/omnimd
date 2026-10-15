"""VLM 抽象基类：视觉模型的最小契约"""
from abc import ABC, abstractmethod


class VlmClient(ABC):
    """以图片引用传入（公开 URL 或 base64 data URI，按 VLM_IMAGE_INPUT 而定），
    返回该视觉任务的文本产物"""

    @abstractmethod
    def ocr(self, image_ref: str) -> str:
        """转写单张图片，返回 Markdown 文本。失败抛异常。"""
        raise NotImplementedError

    @abstractmethod
    def describe(self, image_ref: str) -> str:
        """生成图片 alt 文本，一句话。失败抛异常。"""
        raise NotImplementedError
