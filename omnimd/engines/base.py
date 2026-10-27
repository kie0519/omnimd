"""解析引擎抽象契约

引擎只做一件事：把文件转成正文 Markdown。图片的后处理
（过滤/去重/上传/图片任务/渲染）统一由 converter 编排，
引擎不感知 VLM、存储与缓存。

引擎输出的 Markdown 中，图片允许两种形态：
1. 占位符 ``@@IMG:{sha256}@@``——引擎已通过 ``store.register`` 注册
2. data URI——由 converter 的注册步骤接管

引擎不落盘任何中间产物。支持格式清单在引擎注册表声明一处；
引擎的第三方依赖在构造时导入，依赖加载成本跟实际使用走。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..images import ImageAssetStore


class ParseEngine(ABC):
    """文件 → 正文 Markdown 的解析引擎"""

    @abstractmethod
    def convert(self, file_path: Path, store: "ImageAssetStore") -> str:
        """转换单个文件，返回正文 Markdown（图片形态见模块 docstring）"""
        raise NotImplementedError
