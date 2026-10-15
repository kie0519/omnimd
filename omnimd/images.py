"""图片资产处理：过滤、去重、缓存、上传

职责：
1. 尺寸过滤——短边 < min_dimension 的图片视为装饰性元素，直接丢弃
2. SHA-256 内容去重——相同图片只保留第一份，其余位置引用同一 URL
3. 视觉产物缓存——转写与 alt 按
   {thinking}--{model}--{input}--{kind}--{prompt_digest}/{sha}.{kind}.md 落盘：
   按思考控制方式、模型、传图方式与任务提示词摘要隔离，换配置或改提示词
   强制重新生成，同配置重跑不调模型
4. URL 缓存——上传结果按 {provider}--{端点与桶的摘要}/{sha256}.url 落盘：
   按存储端点与桶隔离，换桶/换端点自然重新上传，重跑不重复上传
5. 上传走可插拔存储后端，换后端只改配置
"""
import base64
import hashlib
import io
import logging
import re
from pathlib import Path

from PIL import Image

from .config import Config
from .storage.base import StorageUploader

logger = logging.getLogger(__name__)


def _prompt_digest(kind: str) -> str:
    """提示词对（system + user）的内容摘要——提示词变更即缓存失效"""
    from .prompts import ALT_SYSTEM_PROMPT, ALT_USER_PROMPT
    from .prompts import OCR_SYSTEM_PROMPT, OCR_USER_PROMPT

    pairs = {
        "ocr": (OCR_SYSTEM_PROMPT, OCR_USER_PROMPT),
        "alt": (ALT_SYSTEM_PROMPT, ALT_USER_PROMPT),
    }
    system, user = pairs[kind]
    return hashlib.sha256((system + user).encode("utf-8")).hexdigest()[:12]


def _slug(name: str) -> str:
    """目录名安全化：模型 ID 含 / 等字符（如 qwen/qwen3-vl），替换为 _"""
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


class ImageAsset:
    """一张待处理图片的完整生命周期描述"""

    def __init__(self, source: str, data: bytes):
        self.source = source          # 来源描述（文件路径或文档内位置），用于日志
        self.data = data
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.url: str = ""            # 存储后端公开 URL（上传后填充；产出含链接与 URL 传图转写时使用）
        self.ocr_text: str = ""       # VLM 完整转写结果
        self.alt_text: str = ""       # VLM alt 文本

    def data_uri(self) -> str:
        """图片字节的 base64 data URI 形态（VLM_IMAGE_INPUT=base64 时传给 VLM）。

        MIME 统一按 png 声明（与上传侧 {sha256}.png 命名保持一致）——多数
        端点按字节嗅探实际格式，严格校验 MIME 的端点不在支持范围。
        """
        encoded = base64.b64encode(self.data).decode()
        return f"data:image/png;base64,{encoded}"

    def is_decorative(self, min_dimension: int) -> bool:
        """短边低于阈值视为装饰性图片（分隔线、小图标等）。

        解码失败的图片按非装饰处理（进入正常管线，在上传/转写阶段
        错误原样上抛），只记警告日志——降级不静默。
        """
        try:
            with Image.open(io.BytesIO(self.data)) as img:
                width, height = img.size
            return min(width, height) < min_dimension
        except Exception as e:
            logger.warning("图片解码失败（%s: %s），按正文图片处理: %s",
                           type(e).__name__, e, self.source[:60])
            return False


class ImageAssetStore:
    """图片资产的过滤、去重与缓存管理"""

    def __init__(self, config: Config, cache_dir: Path, uploader: StorageUploader | None):
        self.config = config
        self._by_hash: dict[str, ImageAsset] = {}
        # uploader 允许为 None（本次转换不需要上传：产出不含 url 或 base64 直传）
        self._uploader = uploader
        # 视觉产物缓存按「思考控制方式+模型+传图方式+任务提示词摘要」隔离：
        # 提示词是转写结果的决定因素，进缓存键使提示词迭代可复现
        self._prompt_dirs = {
            kind: cache_dir / "ocr" / _slug(
                f"{config.vlm_thinking}--{config.vlm_model}"
                f"--{config.vlm_image_input}--{kind}--{_prompt_digest(kind)}"
            )
            for kind in ("ocr", "alt")
        }
        for path in self._prompt_dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        # URL 缓存按「存储端点+桶」隔离：换桶或换端点后强制重新上传，
        # 旧桶的 URL 不污染新桶的产出
        _dest = hashlib.sha1(
            f"{config.s3_endpoint_url}/{config.s3_bucket}".encode()
        ).hexdigest()[:16]
        self._url_cache_dir = cache_dir / "urls" / f"{config.storage_provider or 'none'}--{_dest}"
        self._url_cache_dir.mkdir(parents=True, exist_ok=True)

    def register(self, source: str, data: bytes) -> ImageAsset | None:
        """注册一张图片。返回去重后的资产对象；装饰性图片返回 None（直接丢弃）"""
        asset = ImageAsset(source, data)
        if asset.is_decorative(self.config.min_image_dimension):
            logger.debug("丢弃装饰性图片: %s", source)
            return None
        if asset.sha256 in self._by_hash:
            logger.debug("命中重复图片: %s (sha=%s)", source, asset.sha256[:12])
            return self._by_hash[asset.sha256]
        self._by_hash[asset.sha256] = asset
        return asset

    def lookup(self, sha256: str) -> ImageAsset | None:
        return self._by_hash.get(sha256)

    # ---------- 转写结果缓存（ocr=完整转写 / alt=短描述，按任务类型分文件） ----------
    def get_cached_text(self, sha256: str, kind: str) -> str | None:
        """kind: "ocr"（完整转写）或 "alt"（短描述）。缓存不存在返回 None。"""
        cache_file = self._prompt_dirs[kind] / f"{sha256}.{kind}.md"
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")
        return None

    def save_cached_text(self, sha256: str, kind: str, text: str) -> None:
        self._atomic_write(self._prompt_dirs[kind] / f"{sha256}.{kind}.md", text)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """写临时文件后原子替换：并发写同一缓存文件时读者不会读到半截内容。"""
        import os, uuid
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # ---------- 上传（含持久化 URL 缓存） ----------
    def ensure_uploaded(self, asset: ImageAsset) -> str:
        """确保图片已上传，返回公开 URL。

        三级查找：进程内 → URL 缓存文件 → 实际上传（上传后写缓存）。
        """
        if self._uploader is None:
            raise RuntimeError("ensure_uploaded 在无存储配置下被调用（违反内部约定）")
        if asset.url:
            return asset.url
        url = self._read_cached_url(asset.sha256)
        if url:
            asset.url = url
            return url
        # 文件名用内容哈希：同图同名，覆盖上传幂等，重跑天然去重
        filename = f"{asset.sha256}.png"
        asset.url = self._uploader.upload_image(asset.data, filename)
        self._write_cached_url(asset.sha256, asset.url)
        return asset.url

    def _read_cached_url(self, sha256: str) -> str | None:
        cache_file = self._url_cache_dir / f"{sha256}.url"
        if cache_file.exists():
            url = cache_file.read_text(encoding="utf-8").strip()
            if url:
                return url
        return None

    def _write_cached_url(self, sha256: str, url: str) -> None:
        self._atomic_write(self._url_cache_dir / f"{sha256}.url", url)

    def all_assets(self) -> list[ImageAsset]:
        return list(self._by_hash.values())
