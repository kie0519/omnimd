"""解析引擎选择层

路由规则（按优先级）：
- 图片   → ImageEngine：一张图片就是一个「只有资产、没有正文」的文件
- pdf    → pymupdf4llm：唯一能保留全部内嵌图片的 PDF 引擎
- 其他   → DOC_ENGINE 选定引擎（缺省 markitdown）；所选引擎不支持的格式
  回退另一引擎，支持的格式取并集

图片与 PDF 的路由不可配置（语义唯一）；引擎模块在首次解析时才导入：
markitdown 的模块导入会加载全部格式转换器（pandas、onnxruntime 等），
远重于其他引擎，按需加载使一次运行只加载实际用到的引擎。
"""
from dataclasses import dataclass
from importlib import import_module

from ..exceptions import ConfigError, UnsupportedFormatError


@dataclass(frozen=True)
class EngineSpec:
    """引擎注册项：模块定位符 + 支持格式清单"""

    module: str
    cls: str
    extensions: frozenset[str]

    def load(self) -> type:
        """导入引擎模块并返回引擎类"""
        return getattr(import_module(self.module), self.cls)


# 图片输入扩展名（固定路由自用；demo 等下游消费同一份，不另抄清单）
IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".tif", ".tiff", ".svg", ".ico",
})

# 固定路由引擎：语义唯一，不受 DOC_ENGINE 影响
_IMAGE_ENGINE = EngineSpec(
    module="omnimd.engines.image_engine",
    cls="ImageEngine",
    extensions=IMAGE_EXTENSIONS,
)
_PDF_ENGINE = EngineSpec(
    module="omnimd.engines.pymupdf_engine",
    cls="PyMuPdfEngine",
    extensions=frozenset({".pdf"}),
)

# DOC_ENGINE 取值 → 引擎注册项
DOC_ENGINES: dict[str, EngineSpec] = {
    "markitdown": EngineSpec(
        module="omnimd.engines.markitdown_engine",
        cls="MarkitdownEngine",
        extensions=frozenset({
            ".docx", ".pptx", ".xlsx", ".xls", ".html", ".htm",
            ".csv", ".json", ".xml", ".zip", ".txt", ".md", ".rst",
            ".epub", ".ipynb",
        }),
    ),
    "anydoc": EngineSpec(
        module="omnimd.engines.anydoc_engine",
        cls="AnydocEngine",
        extensions=frozenset({
            ".doc", ".docx", ".docm", ".odt", ".ods", ".odp",
            ".ppt", ".pptx", ".pptm", ".ppsx", ".ppsm",
            ".rtf", ".epub", ".xlsx", ".xlsm", ".xlsb", ".csv",
        }),
    ),
}


def supported_extensions() -> list[str]:
    """全部可转换扩展名，按名排序（固定路由与 DOC_ENGINE 两引擎支持格式的并集）"""
    suffixes = _IMAGE_ENGINE.extensions | _PDF_ENGINE.extensions
    for spec in DOC_ENGINES.values():
        suffixes |= spec.extensions
    return sorted(suffixes)


def resolve_engine(suffix: str, doc_engine: str) -> type:
    """按扩展名解析引擎类（此时才导入引擎模块）；
    不支持的取值/格式抛异常并列出全部可选项"""
    if doc_engine not in DOC_ENGINES:
        raise ConfigError(
            f"不支持的 DOC_ENGINE: {doc_engine}"
            f"（可选：{', '.join(sorted(DOC_ENGINES))}）"
        )
    suffix = suffix.lower()
    if suffix in _IMAGE_ENGINE.extensions:
        return _IMAGE_ENGINE.load()
    if suffix in _PDF_ENGINE.extensions:
        return _PDF_ENGINE.load()
    spec = DOC_ENGINES[doc_engine]
    if suffix in spec.extensions:
        return spec.load()
    # 所选引擎不支持该格式时回退其它支持它的引擎，支持的格式取并集
    for other in DOC_ENGINES.values():
        if other is not spec and suffix in other.extensions:
            return other.load()
    raise UnsupportedFormatError(
        f"不支持的文件格式：{suffix}（可选：{', '.join(supported_extensions())}）"
    )
