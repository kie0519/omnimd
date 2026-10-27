"""文件存储抽象层

统一契约：upload_image(image_data, filename) -> 公开 URL（VLM 端点据此拉取图片）。

与 VLM 层同一形态：单协议（S3 兼容）+ 凭据直接接入，无厂商概念——
S3_ENDPOINT_URL 留空 = 不使用存储；配置则五项齐全（缺一在构造时报全清单）。
接入第二协议时再引入后端选择变量。
"""
from ..config import Config
from ..exceptions import ConfigError
from .base import StorageUploader
from .s3 import S3Uploader

__all__ = ["StorageUploader", "create_uploader"]


def create_uploader(config: Config) -> StorageUploader:
    """构造 S3 兼容存储客户端；必填变量缺一时报全清单。"""
    missing = [
        name for name in S3Uploader.REQUIRED_ENV_VARS
        if not getattr(config, name.lower())
    ]
    if missing:
        raise ConfigError(
            f"对象存储配置不完整，缺少: {', '.join(missing)}。"
            "请在 .env 中配齐 S3_* 五项（S3 兼容端点，模板见 .env.example）。"
        )
    return S3Uploader(config)
