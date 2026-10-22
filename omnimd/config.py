"""配置管理

从环境变量或 .env 文件加载全部配置并构造不可变 Config。校验分两级：
- 加载即校验运行参数（取值域、数值类型、产出组成）；
- 凭据按消费方惰性校验（首次转写时查 VLM 凭据三项，首次上传时查存储配置）——纯解析无需凭据即可构造。
"""
import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv
from platformdirs import user_cache_dir

from .exceptions import ConfigError
from .engines import DOC_ENGINES
from .vlm.thinking import THINKING_EXTRA_BODY

# 显式列出两处（当前目录与包根），不依赖 load_dotenv 缺省搜索
load_dotenv(Path.cwd() / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def default_cache_dir() -> Path:
    """缓存目录缺省值：各平台用户缓存目录。Linux ~/.cache/omnimd、macOS ~/Library/Caches/omnimd、Windows %LOCALAPPDATA%/omnimd/Cache。"""
    return Path(user_cache_dir("omnimd", appauthor=False))


_OUTPUT_VALUES = ("body", "url", "base64", "ocr", "alt")


def parse_output(raw: str | None) -> str:
    """校验并归一化产出组成（OUTPUT 环境变量与 output 参数共用）。

    逗号分隔多选 {body, url, base64, ocr, alt}：顺序不敏感、自动去重、容忍空白；未设置时取 body（纯解析）。
    body 是必选锚点（省略时自动包含）；url 与 base64 互斥；ocr/alt 依赖 url 或 base64。
    返回按 body,url,base64,ocr,alt 固定顺序的 CSV，非法取值直接报 ConfigError。
    """
    raw = (raw or "").strip()
    if not raw:
        return "body"
    parts: list[str] = []
    for piece in raw.split(","):
        part = piece.strip()
        if not part:
            raise ConfigError("OUTPUT 含空取值（连续逗号或纯空白）")
        if part not in _OUTPUT_VALUES:
            raise ConfigError(
                f"不支持的 OUTPUT 取值：{part}"
                f"（可选：{', '.join(_OUTPUT_VALUES)}，逗号分隔多选）"
            )
        if part not in parts:
            parts.append(part)
    if "body" not in parts:
        parts.insert(0, "body")
    if "url" in parts and "base64" in parts:
        raise ConfigError(
            "OUTPUT 的 url 与 base64 互斥（图片只能有一种呈现方式），"
            "请只选其一"
        )
    if ("ocr" in parts or "alt" in parts) and "url" not in parts and "base64" not in parts:
        raise ConfigError(
            "OUTPUT 的 ocr/alt 依赖 url 或 base64（图片内容需有呈现载体），"
            "请同时选择其一（如 OUTPUT=body,base64,ocr）"
        )
    parts.sort(key=_OUTPUT_VALUES.index)
    return ",".join(parts)


@dataclass(frozen=True)
class Config:
    """全局配置，immutable，由 load() 构造并校验"""

    # ----- VLM 视觉模型 -----
    vlm_api_key: str
    vlm_base_url: str
    vlm_model: str

    # ----- 文件存储 -----
    storage_provider: str
    s3_endpoint_url: str         # S3 兼容（云厂商与自托管 MinIO 通用）
    s3_region: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_key_prefix: str           # 对象 key 前缀，用于桶内目录隔离，可为空

    # ----- 运行参数 -----
    vlm_max_tokens: int = 8192     # 单图转写输出上限
    vlm_concurrency: int = 8       # 转写并发数
    # 思考控制方式
    vlm_thinking: str = "none"
    cache_dir: Path = field(default_factory=default_cache_dir)
    min_image_dimension: int = 60  # 短边低于该像素数视为装饰性图片，直接丢弃
    # 产出组成：body=正文（必选锚点）；url=图片公开链接；base64=图片内嵌（与 url 互斥）；ocr=转写文本；alt=描述文本（ocr/alt 依赖 url 或 base64）
    output: str = "body"
    # 非 PDF 格式的解析引擎
    doc_engine: str = "markitdown"

    def validate(self) -> None:
        """构造期校验运行参数：取值域、数值类型。"""
        parse_output(self.output)
        if self.vlm_max_tokens < 1:
            raise ConfigError(
                f"VLM_MAX_TOKENS 需要正整数，当前值: {self.vlm_max_tokens}"
            )
        if self.storage_provider:
            from .storage import _PROVIDERS
            if self.storage_provider not in _PROVIDERS:
                raise ConfigError(
                    f"不支持的 STORAGE_PROVIDER: {self.storage_provider}"
                    f"（可选：{', '.join(sorted(_PROVIDERS))}）"
                )
        if self.vlm_thinking not in THINKING_EXTRA_BODY:
            raise ConfigError(
                f"不支持的 VLM_THINKING: {self.vlm_thinking}"
                f"（可选：{', '.join(THINKING_EXTRA_BODY)}）"
            )
        if self.vlm_concurrency < 1:
            raise ConfigError(
                f"VLM_CONCURRENCY 需要正整数，当前值: {self.vlm_concurrency}"
            )
        if self.doc_engine not in DOC_ENGINES:
            raise ConfigError(
                f"不支持的 DOC_ENGINE: {self.doc_engine}"
                f"（可选：{', '.join(sorted(DOC_ENGINES))}）"
            )

    def validate_vlm(self) -> None:
        """校验 VLM 凭据三项是否齐全、base_url 是否完整 URL。"""
        missing = [
            name for name, val in [
                ("VLM_API_KEY", self.vlm_api_key),
                ("VLM_BASE_URL", self.vlm_base_url),
                ("VLM_MODEL", self.vlm_model),
            ] if not val
        ]
        if missing:
            raise ConfigError(
                f"本次转换需要视觉模型（产出含 ocr/alt 部件），缺少凭据: "
                f"{', '.join(missing)}。请在 .env 中配置凭据三项"
                "（VLM_API_KEY / VLM_BASE_URL / VLM_MODEL），"
                "或仅产出正文/图片部件（output 不含 ocr、alt）。"
            )
        from urllib.parse import urlparse
        parsed = urlparse(self.vlm_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(
                f"VLM_BASE_URL 需要完整 URL（含协议头，如 https://api.example.com/v1），"
                f"当前值: {self.vlm_base_url!r}"
            )


def load(concurrency: int | None = None, cache_dir: str | None = None,
         output: str | None = None, doc_engine: str | None = None,
         validate: bool = True) -> Config:
    """构造并校验配置。

    concurrency / cache_dir / output / doc_engine 是运行参数，允许调用方覆盖。
    validate=False 跳过校验。
    """
    def env(name: str) -> str:
        return os.getenv(name, "").strip()

    def env_int(name: str, default: int) -> int:
        """整型变量解析：非数字值报 ConfigError。"""
        raw = env(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(
                f"{name} 需要整数，当前值: {raw!r}。"
                f"请修正 .env / 环境变量中的 {name}。"
            )

    config = Config(
        vlm_api_key=env("VLM_API_KEY"),
        vlm_base_url=env("VLM_BASE_URL"),
        vlm_model=env("VLM_MODEL"),
        vlm_max_tokens=env_int("VLM_MAX_TOKENS", 8192),
        vlm_concurrency=(concurrency if concurrency is not None
                        else env_int("VLM_CONCURRENCY", 8)),
        vlm_thinking=env("VLM_THINKING") or "none",
        storage_provider=env("STORAGE_PROVIDER"),
        s3_endpoint_url=env("S3_ENDPOINT_URL"),
        s3_region=env("S3_REGION"),
        s3_bucket=env("S3_BUCKET"),
        s3_access_key_id=env("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=env("S3_SECRET_ACCESS_KEY"),
        s3_key_prefix=env("S3_KEY_PREFIX"),
        output=parse_output(output or env("OUTPUT")),
        doc_engine=doc_engine or env("DOC_ENGINE") or "markitdown",
        cache_dir=Path(cache_dir) if cache_dir
        else Path(env("CACHE_DIR")) if env("CACHE_DIR")
        else default_cache_dir(),
    )
    if validate:
        config.validate()
    return config
