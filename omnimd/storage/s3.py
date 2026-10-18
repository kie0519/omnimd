"""S3 兼容对象存储上传后端

一套实现覆盖华为 OBS / 阿里云 OSS / 腾讯云 COS（均兼容 S3 API，其他 S3 兼容端点同理），
差异只体现在环境变量：endpoint / region / bucket / AK/SK / key 前缀，
切换云厂商不改一行代码。

实现约束（各 S3 兼容端点通用）：
- SigV4 签名 + path 风格寻址（虚拟主机风格在自建网关不可用）
- 公有读桶直接拼接 URL：无签名、无过期时间、永久有效
- key 含特殊字符时 URL 必须 quote
"""
import io
import logging
import urllib.parse

from ..config import Config
from .base import StorageUploader

logger = logging.getLogger(__name__)


class S3Uploader(StorageUploader):
    REQUIRED_ENV_VARS = (
        "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
        "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    )

    def __init__(self, config: Config):
        import boto3
        from botocore.config import Config as BotoConfig

        self.bucket = config.s3_bucket
        self.public_base = f"{config.s3_endpoint_url.rstrip('/')}/{config.s3_bucket}/"
        # 前缀归一化：前导斜杠会产生 // 双斜杠 URL（多数网关归一为 /，公开访问 404）
        prefix = (config.s3_key_prefix or "").lstrip("/")
        self.key_prefix = prefix if prefix == "" or prefix.endswith("/") else prefix + "/"
        self._client = boto3.client(
            service_name="s3",
            region_name=config.s3_region,
            endpoint_url=config.s3_endpoint_url,
            aws_access_key_id=config.s3_access_key_id,
            aws_secret_access_key=config.s3_secret_access_key,
            config=BotoConfig(
                signature_version="s3v4",            # S3 兼容端点统一 SigV4 签名
                s3={"addressing_style": "path"},     # 自建网关不支持虚拟主机寻址
            ),
        )

    def upload_image(self, image_data: bytes, filename: str = "image.png") -> str:
        """上传图片，返回永久公开 URL。对象 key 内容寻址，重复上传幂等。"""
        key = f"{self.key_prefix}{filename}"
        self._client.upload_fileobj(
            io.BytesIO(image_data),
            self.bucket,
            key,
            ExtraArgs={"ContentType": "image/png"},  # 浏览器内联显示而非触发下载
        )
        url = f"{self.public_base}{urllib.parse.quote(key)}"
        logger.debug("S3 上传完成: %s/%s", self.bucket, key)
        return url
