"""VLM 抽象契约：请求进、结果出

协议层不认识任务——任务知识与请求组装都在编排层（converter），本层只认
提示词字段与输出上限；任何视觉调用都是同一契约的一次 run。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VisionRequest:
    """一次视觉模型调用的请求"""

    system: str        # 系统提示词（编排层已解析缺省——这里总是生效值）
    user: str          # 用户提示词（任务本体）
    max_tokens: int    # 输出上限（编排层已解析缺省——这里总是生效值）


@dataclass(frozen=True)
class Usage:
    """单次调用的 token 用量（成本可见性：CLI 汇总与库面成本核算取这里）"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class CompletionResult:
    """一次视觉模型调用的结果：文本 + 用量（端点未返回用量时 usage 为 None）"""

    text: str
    usage: Usage | None = None


class VlmClient(ABC):
    """视觉模型客户端的最小契约：run 单方法 + run_stream 流式变体。

    image_ref 是公开 URL 或 base64 data URI，两种形态等价透传。
    """

    @abstractmethod
    def run(self, request: VisionRequest, image_ref: str) -> CompletionResult:
        """执行一次视觉调用，返回文本与用量。失败抛异常。"""
        raise NotImplementedError

    def run_stream(self, request: VisionRequest, image_ref: str, on_usage=None):
        """流式变体：逐段产出文本增量（generator）；usage 经 on_usage 回调交付。

        非抽象（缺省不可用）——消费方按需探测使用；返回的增量拼接后与
        run 的 text 语义一致。端点未返回用量时 on_usage 不被调用，
        消费方按无用量处理。
        """
        raise NotImplementedError(f"{type(self).__name__} 不支持流式调用")
