"""OpenAI 兼容 VLM 公共骨架

纯协议层：VisionRequest 进、CompletionResult 出——无任务知识（任务组装在编排层）。
思考控制方式由 VLM_THINKING 配置选择（见 thinking.py）。

四条工程立场：
- 超时显式化：VLM_TIMEOUT（缺省 300s）构造期注入——SDK 缺省 read=600s，
  端点挂起时用户无反馈地等十分钟；
- 重试不叠加：SDK 内置 2 次指数退避（429/5xx/连接错误），幂等调用够用；
  应用层不叠加重试——并发 × 双层重试 = 限流场景的重试风暴；
- 采样参数不发送：端点缺省值是端点作者对自家模型的调校，多端点工具
  替端点猜值是制造不可预测；
- 错误按状态码转译：状态码是协议语义（非文案猜测）——401/403 凭据问题、
  404 模型名或路径问题，转译为配置指引并附 request_id（端点报障凭据）；
  其余异常透传不改写（SDK 已重试过，原始证据最准）。

唯一按请求形态改写的分支：URL 传图被端点拒绝（400）——组合判定
（URL 形态 + 状态码），指引两条出路，附原始错误全文。
"""
from collections.abc import Iterator

from openai import APIStatusError, BadRequestError, OpenAI

from ..config import Config
from ..exceptions import ConfigError, OmniMDError
from .base import CompletionResult, Usage, VisionRequest, VlmClient
from .thinking import THINKING_EXTRA_BODY


class OpenAICompatVlm(VlmClient):
    """OpenAI 兼容 chat/completions 协议的视觉模型实现。"""

    def __init__(self, config: Config):
        self.config = config
        self.client = OpenAI(
            api_key=config.vlm_api_key,
            base_url=config.vlm_base_url,
            timeout=config.vlm_timeout,
        )
        self._thinking_extra_body = THINKING_EXTRA_BODY[config.vlm_thinking]

    def run(self, request: VisionRequest, image_ref: str) -> CompletionResult:
        """单次视觉调用：返回文本（空白归一）与用量。"""
        try:
            response = self.client.chat.completions.create(
                model=self.config.vlm_model,
                messages=[
                    {"role": "system", "content": request.system},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": image_ref, "detail": "high"},
                            },
                            {"type": "text", "text": request.user},
                        ],
                    },
                ],
                max_tokens=request.max_tokens,
                extra_body=self._thinking_extra_body,
            )
        except BadRequestError as exc:
            if image_ref.startswith("http"):
                raise OmniMDError(
                    "当前端点可能不支持 URL 传图（Ollama 等本地端点无服务端取图）。"
                    "两个选择：①改用内嵌呈现 --images embed（图片内嵌产出、"
                    "VLM 直传 base64、免存储）；②换支持 URL 传图的云端端点。"
                    f"原始错误：{exc}"
                ) from exc
            raise
        except APIStatusError as exc:
            translated = self._translate_status(exc)
            if translated is not None:
                raise translated from exc
            raise
        # choices 可能为空（内容过滤、部分网关的错误语义）——按空内容处置
        content = response.choices[0].message.content if response.choices else None
        usage = None
        if response.usage is not None:
            usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
        return CompletionResult(text=(content or "").strip(), usage=usage)

    def run_stream(self, request: VisionRequest, image_ref: str,
                   on_usage=None) -> Iterator[str]:
        """流式执行一次视觉调用，逐段产出文本增量；usage 经回调交付。

        与 run 同构的请求组装（含思考参数），外加 stream_options
        （include_usage——OpenAI 规范字段）：支持的端点在收尾帧返回用量
        （choices 为空、usage 在其上），不返回时 on_usage 不被调用。
        """
        base = dict(
            model=self.config.vlm_model,
            messages=[
                {"role": "system", "content": request.system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_ref, "detail": "high"},
                        },
                        {"type": "text", "text": request.user},
                    ],
                },
            ],
            max_tokens=request.max_tokens,
            extra_body=self._thinking_extra_body,
            stream=True,
            stream_options={"include_usage": True},
        )
        try:
            stream = self.client.chat.completions.create(**base)
        except BadRequestError as exc:
            if image_ref.startswith("http"):
                raise OmniMDError(
                    "当前端点可能不支持 URL 传图（Ollama 等本地端点无服务端取图）。"
                    "两个选择：①改用内嵌呈现 --images embed（图片内嵌产出、"
                    "VLM 直传 base64、免存储）；②换支持 URL 传图的云端端点。"
                    f"原始错误：{exc}"
                ) from exc
            raise
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None and on_usage is not None:
                on_usage(Usage(
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                ))
            if not chunk.choices:
                continue    # 收尾帧 choices 为空（usage 在其上）
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text

    @staticmethod
    def _translate_status(exc: APIStatusError) -> ConfigError | None:
        """配置类状态码转译为指引；其余返回 None（调用方透传原异常）。

        只按状态码语义判定，不做文案猜测；指引附 request_id（存在时）。
        """
        rid = getattr(exc, "request_id", None)
        rid_note = f"（request_id={rid}）" if rid else ""
        if exc.status_code in (401, 403):
            return ConfigError(
                f"VLM 端点拒绝凭据（HTTP {exc.status_code}）{rid_note}——"
                "请核对 .env 的 VLM_API_KEY 与 VLM_BASE_URL。"
                f"原始错误：{exc}"
            )
        if exc.status_code == 404:
            return ConfigError(
                f"VLM 端点未找到（HTTP 404）{rid_note}——"
                "请核对 VLM_MODEL（模型名）与 VLM_BASE_URL（路径常缺 /v1）。"
                f"原始错误：{exc}"
            )
        return None
