"""pymupdf4llm 引擎：PDF 专线

正文、表格、标题层级一次全出。embed_images=True 让内嵌图片直接以
data URI 内嵌在 Markdown 里——不产生本地文件路径，源文件名的
特殊字符（全角括号等）不影响产出。这是图片保真的唯一 PDF 路线
（候选引擎的 PDF 转换都丢全部内嵌图片）。

layout 模式（pymupdf.layout 子进程管线）不可用，两个原因：
- Windows 上子进程输出 GBK 编码导致解码失败
- 扫描版 PDF（整页图片、无文字层）在该模式下产出为空

全局固定 legacy 管线（pymupdf_rag）。
"""
import re

from .base import ParseEngine

# pymupdf4llm 自带的 picture-text 冗余标记整体移除；
# 图片内容由渲染阶段按产出组成回填
_PICTURE_TEXT_PATTERN = re.compile(
    r"\*\*----- Start of picture text -----<br>\|?.*?"
    r"\*\*----- End of picture text -----<br>",
    flags=re.S,
)


class PyMuPdfEngine(ParseEngine):
    def __init__(self):
        import pymupdf4llm
        pymupdf4llm.use_layout(False)
        self._pymupdf4llm = pymupdf4llm

    def convert(self, file_path, store) -> str:
        markdown = self._pymupdf4llm.to_markdown(
            str(file_path),
            embed_images=True,
            image_format="png",
            show_progress=False,
        )
        return _PICTURE_TEXT_PATTERN.sub("", markdown)
