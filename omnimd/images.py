"""图片资产处理：过滤、去重、缓存、上传

职责：
1. 尺寸过滤——短边 < min_dimension 的图片视为装饰性元素，直接丢弃
2. SHA-256 内容去重——相同图片只保留第一份，其余位置引用同一 URL
3. 任务产物缓存——按 {thinking}--{model}--{任务名}--{digest}/{sha256}.md 落盘：
   按思考控制方式、模型、任务与提示词摘要隔离，换配置、换任务或改提示词
   强制重新生成，同配置重跑不调模型
   （VLM 对同一张图的任务产物不因 URL 还是 base64 传入而不同，取图方式不进缓存键）
4. URL 缓存——上传结果按 s3--{端点与桶与前缀的摘要}/{sha256}.url 落盘：按存储端点、桶与对象前缀隔离，换任意一项自然重新上传，重跑不重复上传
5. 上传委托 storage 层的 S3 兼容实现（凭据直接接入）
"""
import base64
import hashlib
import io
import logging
import re
import threading
from pathlib import Path

from PIL import Image

from .config import Config
from .prompts.registry import TaskSpec, task_digest
from .storage.base import StorageUploader

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """目录名安全化：模型 ID 与任务名含 / : 等字符（如 qwen/qwen3-vl、prompt:1），替换为 _"""
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


class ImageAsset:
    """一张待处理图片的完整生命周期描述"""

    def __init__(self, source: str, data: bytes):
        self.source = source          # 来源描述（文件路径或文档内位置），用于日志
        self.data = data
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.url: str = ""            # 存储公开 URL（上传后填充；link 呈现与 URL 传图时使用）
        self.texts: dict[str, str] = {}   # 各任务产物 {任务名: 文本}

    def data_uri(self) -> str:
        """图片字节的 base64 data URI 形态（embed 呈现渲染与传图用）。

        MIME 统一按 png 声明——多数端点按字节嗅探实际格式，严格校验 MIME 的端点不在支持范围。
        """
        encoded = base64.b64encode(self.data).decode()
        return f"data:image/png;base64,{encoded}"

    def is_decorative(self, min_dimension: int) -> bool:
        """短边低于阈值视为装饰性图片（分隔线、小图标等）。

        解码失败的图片按非装饰处理（进入正常管线，在上传/任务阶段错误原样上抛），只记警告日志。
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

    def __init__(self, config: Config, cache_dir: Path,
                 uploader: StorageUploader | None, tasks=()):
        self.config = config
        self._by_hash: dict[str, ImageAsset] = {}
        # uploader 允许为 None（本次转换不需要上传：呈现非 link）
        self._uploader = uploader
        # 任务产物缓存目录按注册表生成：{任务名}/{thinking}--{model}--{任务名}--{digest}
        # （即时任务名 prompt:N 经 _slug 落为 prompt_N；digest 含提示词与骨架摘要）
        self._task_dirs = {
            spec.name: cache_dir / _slug(spec.name) / _slug(
                f"{config.vlm_thinking}--{config.vlm_model}"
                f"--{_slug(spec.name)}--{task_digest(spec)}"
            )
            for spec in tasks
        }
        _dest = hashlib.sha1(
            f"{config.s3_endpoint_url}/{config.s3_bucket}"
            f"/{config.s3_key_prefix}".encode()
        ).hexdigest()[:16]
        self._url_cache_dir = cache_dir / "urls" / f"s3--{_dest}"
        # 缓存是可再生的加速器：目录不可写（权限/网络盘/磁盘满）时停用缓存、
        # 警告一次并继续——转换与上传不受影响，仅重跑不再省调用
        self._cache_lock = threading.Lock()
        self._cache_disabled = False

    def _disable_cache(self, cache_dir: Path, e: Exception) -> None:
        """停用本实例缓存（并发下警告只发一次）。"""
        with self._cache_lock:
            first = not self._cache_disabled
            self._cache_disabled = True
        if first:
            logger.warning(
                "缓存不可用，本次转换不使用缓存（转换不受影响，"
                "仅重跑不再省调用）：%s（%s）", cache_dir, e)

    def _cache_writable(self, cache_dir: Path) -> bool:
        """缓存目录可用性守卫：建目录失败（无权限/保留名/网络盘）即停用。"""
        if self._cache_disabled:
            return False
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            self._disable_cache(cache_dir, e)
            return False

    def _cache_write(self, path: Path, text: str) -> None:
        """写缓存；写入失败（目录存在但无写权限/磁盘满）同样停用缓存。"""
        try:
            self._atomic_write(path, text)
        except OSError as e:
            self._disable_cache(path.parent, e)

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

    # ---------- 任务产物缓存（按任务隔离，同名任务同提示词命中） ----------
    def get_cached_text(self, spec: TaskSpec, sha256: str) -> str | None:
        """该任务对该图的缓存；未命中、已停用或文件损坏（读失败按 miss）返回 None。"""
        if self._cache_disabled:
            return None
        cache_dir = self._task_dirs.get(spec.name)
        if cache_dir is None:
            return None
        cache_file = cache_dir / f"{sha256}.md"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8")
            except (OSError, ValueError):
                logger.debug("缓存文件读取失败，按未命中处理: %s", cache_file)
        return None

    def save_cached_text(self, spec: TaskSpec, sha256: str, text: str) -> None:
        cache_dir = self._task_dirs[spec.name]
        if not self._cache_writable(cache_dir):
            return
        self._cache_write(cache_dir / f"{sha256}.md", text)

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
        if self._cache_disabled:
            return None
        cache_file = self._url_cache_dir / f"{sha256}.url"
        if cache_file.exists():
            try:
                url = cache_file.read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                logger.debug("URL 缓存读取失败，按未命中处理: %s", cache_file)
                return None
            if url:
                return url
        return None

    def _write_cached_url(self, sha256: str, url: str) -> None:
        if not self._cache_writable(self._url_cache_dir):
            return
        self._cache_write(self._url_cache_dir / f"{sha256}.url", url)

    def all_assets(self) -> list[ImageAsset]:
        return list(self._by_hash.values())
