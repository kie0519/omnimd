"""文件存储抽象层

统一契约：upload_image(image_data, filename) -> 公开 URL。
VLM 通过该 URL 拉取图片——存储与转写（视觉模型）两层完全解耦。

STORAGE_PROVIDER 的合法取值与各后端的必填变量都在本层校验：
未知取值或缺必填变量时构造即报错，报错信息列出全部可选项。
"""
from ..config import Config
from ..exceptions import ConfigError
from .base import StorageUploader
from .s3 import S3Uploader

# STORAGE_PROVIDER 取值 → 实现类
_PROVIDERS: dict[str, type[StorageUploader]] = {"s3": S3Uploader}


def create_uploader(config: Config) -> StorageUploader:
    """按 STORAGE_PROVIDER 构造存储后端，未支持的选择或缺配置直接报错"""
    cls = _PROVIDERS.get(config.storage_provider)
    if cls is None:
        raise ConfigError(
            f"不支持的 STORAGE_PROVIDER: {config.storage_provider}"
            f"（可选：{', '.join(sorted(_PROVIDERS))}）"
        )
    missing = [
        name for name in cls.REQUIRED_ENV_VARS
        if not getattr(config, name.lower())
    ]
    if missing:
        raise ConfigError(
            f"STORAGE_PROVIDER={config.storage_provider} 需要环境变量: "
            f"{', '.join(missing)}，请在 .env 中配置。"
        )
    return cls(config)
