"""omnimd

任意文件转 Markdown：把各类文件转换为「正文 + 图片链接 + 图片转写文本」
三合一的 Markdown，产出的文本可直接作为 coding agent 上下文、RAG 知识库语料。

命令行用法见 README；作为 Python 库使用（与大模型 SDK 同构——先构造
客户端，后转换）：

    from omnimd import OmniMD, VlmConfig, S3Config

    md = OmniMD()                        # 读环境变量 / .env（缺省构造方式）
    result = md.convert("报告.pdf")       # → ConversionResult（.markdown 为产出全文）

    md = OmniMD(                          # 或显式传连接配置
        vlm=VlmConfig(api_key="...", base_url="...", model="..."),
        storage=S3Config(endpoint="...", region="...", bucket="...",
                         access_key_id="...", secret_access_key="..."),
    )
    md.convert("手册.pdf", output="url")  # output = 本次产出组成（url/text/alt 多选）
    md.convert_many(["a.pdf", "dir/"])    # 批量：{完整路径: ConversionResult}

可预期错误（配置、格式等）抛 OmniMDError 子类，上游依赖的原始异常透传不改写。
完整接口说明见 docs/library.md。
"""
from .exceptions import ConfigError, OmniMDError, UnsupportedFormatError
from .library import (
    ConversionResult,
    ImageInfo,
    OmniMD,
    S3Config,
    VlmConfig,
)

__version__ = "1.0.1"

__all__ = [
    "OmniMD", "VlmConfig", "S3Config",
    "ConversionResult", "ImageInfo",
    "ConfigError", "UnsupportedFormatError", "OmniMDError",
    "__version__",
]
