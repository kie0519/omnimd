"""markitdown 引擎：非 PDF 格式的缺省解析引擎

支持的格式最多（xlsx/html/csv/json/xml/zip/ipynb/epub/...），
keep_data_uris=True 保留内嵌图片为 data URI（图片管线的输入形态）。

markitdown 内部对每个文件可能尝试多个转换器，异常分三类：
UnsupportedFormatException / FileConversionException / MissingDependencyException，
此处不吞异常，原样上抛，整批语义（单文件失败不中断）由调用方决定。
"""
from pathlib import Path

from .base import ParseEngine


class MarkitdownEngine(ParseEngine):
    def __init__(self):
        from markitdown import MarkItDown
        # 插件保持关闭：entry_points 全量加载会引入不受控的转换行为
        self._md = MarkItDown(enable_plugins=False)

    def convert(self, file_path: Path, store) -> str:
        result = self._md.convert(str(file_path), keep_data_uris=True)
        return result.markdown
