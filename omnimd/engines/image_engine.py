"""图片直转引擎：单张图片文件作为输入

一张图片就是一个「只有资产、没有正文」的文件：注册进图片管线（过滤/去重/上传/转写），渲染与文档内嵌图片同一管线，按产出组成生成图片块。

小图（短边低于配置阈值 min_image_dimension 的图标/分隔线）同样走装饰图过滤：注册返回 None 时产出为空文件。
"""
import logging
from pathlib import Path

from .base import ParseEngine

logger = logging.getLogger(__name__)

class ImageEngine(ParseEngine):
    def convert(self, file_path: Path, store) -> str:
        data = file_path.read_bytes()
        asset = store.register(str(file_path), data)
        if asset is None:
            logger.debug("跳过装饰性小图: %s", file_path.name)
            return ""
        return f"@@IMG:{asset.sha256}@@"
