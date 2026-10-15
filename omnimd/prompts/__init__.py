"""提示词包：每个视觉任务一个模块

- ocr.py：图片完整转写（text 部件的正文级产物）
- alt.py：图片 alt 文本（alt 部件的一句话描述）

各模块导出 <任务>_SYSTEM_PROMPT / <任务>_USER_PROMPT 一对常量，
系统提示词在前、用户提示词在后。新增视觉任务 = 新增一个模块，
消费方（vlm 层）按任务名导入——不集中注册，避免单文件膨胀。
"""
from .alt import ALT_SYSTEM_PROMPT, ALT_USER_PROMPT
from .ocr import OCR_SYSTEM_PROMPT, OCR_USER_PROMPT

__all__ = [
    "OCR_SYSTEM_PROMPT", "OCR_USER_PROMPT",
    "ALT_SYSTEM_PROMPT", "ALT_USER_PROMPT",
]
