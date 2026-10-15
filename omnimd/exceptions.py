"""异常体系：omnimd 自有错误的统一类型

调用方按粒度捕获：`except OmniMDError` 兜住 omnimd 的全部已知错误，
或精确捕获某个子类。上游依赖（解析引擎 / openai SDK / boto3）的原始
异常不在本体系内——透传不改写，调用方按需直接捕获。
"""


class OmniMDError(Exception):
    """omnimd 全部异常的基类"""


class ConfigError(OmniMDError):
    """配置与参数错误：必填项缺失、可选值或数值非法、配置对象类型不对。

    报错信息列出全部缺失项与可选值；存储按需必填的检查延迟到首次
    需要上传时。
    """


class UnsupportedFormatError(OmniMDError):
    """文件格式不在支持列表，报错信息列出全部可选格式。"""
