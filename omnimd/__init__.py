"""omnimd

任意文件转 Markdown：正文逐字保留，图片可保留为链接或内嵌（--images link /
embed），可转写、描述或按自定义提示词分析（--transcribe / --describe /
--prompt），产出的文本可直接作为 coding agent 上下文、RAG 知识库语料。

命令行用法见 README；作为 Python 库使用：

    from omnimd import OmniMD, VlmConfig, S3Config, TaskPrompt

    md = OmniMD()                        # 读环境变量 / .env（缺省构造方式）
    result = md.convert("报告.pdf")       # 不带图片参数：文档只提取正文

    md = OmniMD(                          # 或显式传连接配置
        vlm=VlmConfig(api_key="...", base_url="...", model="..."),
        storage=S3Config(endpoint="...", region="...", bucket="...",
                         access_key_id="...", secret_access_key="..."),
    )
    md.convert("手册.pdf", images="link", transcribe=True)   # 正文 + 链接 + 转写
    md.convert("截图.png")                                   # 图片文件 = 完整转写

    walk = TaskPrompt(name="walk", prompt="完整走查这个界面的布局、组件与配色")
    md2 = OmniMD(tasks=[walk])
    md2.convert("设计稿.png", prompt=[walk, "重点看配色"])     # 任务可叠加
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
    TaskPrompt,
    VlmConfig,
)

__version__ = "3.0.0"

__all__ = [
    "OmniMD", "VlmConfig", "S3Config", "TaskPrompt",
    "ConversionResult", "ImageInfo",
    "ConfigError", "UnsupportedFormatError", "OmniMDError",
    "__version__",
]
