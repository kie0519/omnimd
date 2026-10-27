"""配置管理

从环境变量或 .env 文件加载全部配置并构造不可变 Config。校验分两级：
- 加载即校验运行参数（取值域、数值类型）；
- 凭据按消费方惰性校验（首次调用视觉模型时查 VLM 凭据三项，首次上传时查存储配置）——纯解析无需凭据即可构造。

.env 只承载环境与引擎（凭据、模型、思考方式、超时、并发、缓存）——图片处理选项
不进配置面：不带图片参数时行为由输入类型决定，显式选项由命令参数决定——不带
图片参数的命令在任何环境跑出同一产出。
"""
import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import dotenv_values, load_dotenv
from platformdirs import user_cache_dir

from .exceptions import ConfigError
from .engines import DOC_ENGINES
from .vlm.thinking import THINKING_EXTRA_BODY


def _env_files() -> tuple[Path, ...]:
    """配置文件查找链：工作目录 .env 优先，包根 .env 兜底。"""
    return (
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    )


def default_cache_dir() -> Path:
    """缓存目录缺省值：各平台用户缓存目录。Linux ~/.cache/omnimd、macOS ~/Library/Caches/omnimd、Windows %LOCALAPPDATA%/omnimd/Cache。"""
    return Path(user_cache_dir("omnimd", appauthor=False))


def _env_provides(env_file: Path, raw: str) -> bool:
    """该 .env 是否提供了这个 CACHE_DIR 值（锚点与来源指路共用的判定）。"""
    return str(dotenv_values(env_file).get("CACHE_DIR") or "").strip() == raw


def _resolve_cache_dir(raw: str, injected: str | None,
                       files: tuple[Path, ...]) -> Path:
    """CACHE_DIR 解析：绝对路径原样；相对值锚定提供该值的配置级——
    进程注入锚工作目录，.env 提供锚该文件所在目录（打开 .env 即可预测缓存落点）。
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    if injected is not None and injected.strip() == raw:
        return Path.cwd() / path
    for env_file in files:
        if _env_provides(env_file, raw):
            return env_file.parent / path
    return Path.cwd() / path


def _cache_origin(explicit, raw: str, injected: str | None,
                  files: tuple[Path, ...]) -> str:
    """CACHE_DIR 值的来源描述（报错时指路去哪改）：显式参数 / 进程环境变量 / .env 文件路径。"""
    if explicit:
        return "显式 cache_dir 参数"
    if raw and injected is not None and injected.strip() == raw:
        return "进程环境变量 CACHE_DIR"
    for env_file in files:
        if _env_provides(env_file, raw):
            return str(env_file)
    return ""


@dataclass(frozen=True)
class Config:
    """全局配置，immutable，由 load() 构造并校验"""

    # ----- VLM 视觉模型 -----
    vlm_api_key: str
    vlm_base_url: str
    vlm_model: str

    # ----- 文件存储（S3 兼容；endpoint 留空 = 不使用存储）-----
    s3_endpoint_url: str         # S3 兼容（云厂商与自托管 MinIO 通用）
    s3_region: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_key_prefix: str           # 对象 key 前缀，用于桶内目录隔离，可为空

    # ----- 运行参数 -----
    vlm_max_tokens: int = 8192     # 单图任务输出上限
    vlm_concurrency: int = 8       # 图片任务并发数
    vlm_timeout: float = 300.0     # 单次调用超时（秒）——SDK 缺省 600s 过长，挂起时无反馈
    # 思考控制方式
    vlm_thinking: str = "none"
    cache_dir: Path = field(default_factory=default_cache_dir)
    min_image_dimension: int = 60  # 短边低于该像素数视为装饰性图片，直接丢弃
    # 非 PDF 格式的解析引擎
    doc_engine: str = "markitdown"

    def validate(self) -> None:
        """构造期校验运行参数：取值域、数值类型。"""
        if self.vlm_max_tokens < 1:
            raise ConfigError(
                f"VLM_MAX_TOKENS 需要正整数，当前值: {self.vlm_max_tokens}"
            )
        if self.vlm_timeout <= 0:
            raise ConfigError(
                f"VLM_TIMEOUT 需要正数（秒），当前值: {self.vlm_timeout}"
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
                f"本次转换需要视觉模型（图片任务），缺少凭据: "
                f"{', '.join(missing)}。请在 .env 中配置凭据三项"
                "（VLM_API_KEY / VLM_BASE_URL / VLM_MODEL）。"
            )
        from urllib.parse import urlparse
        parsed = urlparse(self.vlm_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(
                f"VLM_BASE_URL 需要完整 URL（含协议头，如 https://api.example.com/v1），"
                f"当前值: {self.vlm_base_url!r}"
            )


def load(concurrency: int | None = None, cache_dir: str | None = None,
         doc_engine: str | None = None, validate: bool = True) -> Config:
    """构造并校验配置。

    concurrency / cache_dir / doc_engine 是运行参数，允许调用方覆盖。
    validate=False 跳过 Config.validate() 的运行参数校验
    （CACHE_DIR 指向已存在文件的静态检查不受此开关控制）。
    """
    # .env 按查找链合并（进程环境变量优先，两文件间前文优先）；
    # 读取时机即 load() 调用时点——import 本模块不产生文件读取副作用
    injected_cache = os.environ.get("CACHE_DIR")
    for env_file in _env_files():
        load_dotenv(env_file)

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

    def env_float(name: str, default: float) -> float:
        """浮点变量解析：非数字值报 ConfigError。"""
        raw = env(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(
                f"{name} 需要数字（秒），当前值: {raw!r}。"
                f"请修正 .env / 环境变量中的 {name}。"
            )

    # 缓存目录解析：显式参数调用方自决；否则溯源锚定（相对值落在提供值的 .env 旁）
    if cache_dir:
        resolved_cache = Path(cache_dir)
    else:
        raw_cache = env("CACHE_DIR")
        resolved_cache = (
            _resolve_cache_dir(raw_cache, injected_cache, _env_files())
            if raw_cache else default_cache_dir()
        )
    if resolved_cache.exists() and not resolved_cache.is_dir():
        origin = _cache_origin(cache_dir, env("CACHE_DIR"),
                               injected_cache, _env_files())
        raise ConfigError(
            f"缓存目录指向的不是目录: {resolved_cache}"
            + (f"（值来自 {origin}）" if origin else "")
            + "。CACHE_DIR 需指向目录位置（不存在时在首次写入时创建）。"
        )

    config = Config(
        vlm_api_key=env("VLM_API_KEY"),
        vlm_base_url=env("VLM_BASE_URL"),
        vlm_model=env("VLM_MODEL"),
        vlm_max_tokens=env_int("VLM_MAX_TOKENS", 8192),
        vlm_concurrency=(concurrency if concurrency is not None
                        else env_int("VLM_CONCURRENCY", 8)),
        vlm_timeout=env_float("VLM_TIMEOUT", 300.0),
        vlm_thinking=env("VLM_THINKING") or "none",
        s3_endpoint_url=env("S3_ENDPOINT_URL"),
        s3_region=env("S3_REGION"),
        s3_bucket=env("S3_BUCKET"),
        s3_access_key_id=env("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=env("S3_SECRET_ACCESS_KEY"),
        s3_key_prefix=env("S3_KEY_PREFIX"),
        doc_engine=doc_engine or env("DOC_ENGINE") or "markitdown",
        cache_dir=resolved_cache,
    )
    if validate:
        config.validate()
    return config
