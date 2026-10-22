"""图片资产库回归：过滤、去重、缓存、data URI（真字节 + PIL 造图，不联网）"""
import hashlib

import pytest
from PIL import Image

from omnimd.images import ImageAsset, ImageAssetStore
from tests.helpers import make_config


def make_png_bytes(width: int, height: int) -> bytes:
    """PIL 生成真实 PNG 字节（is_decorative 走真实解码路径）"""
    import io
    img = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class StubUploader:
    """记录调用的假上传器——验证上传次数与缓存命中"""

    def __init__(self):
        self.uploads = 0

    def upload_image(self, data: bytes, filename: str) -> str:
        self.uploads += 1
        return f"https://cdn.example/{filename}"


def make_store(tmp_path, model="m"):
    return ImageAssetStore(
        make_config(vlm_model=model),
        tmp_path / "cache", None,
    )


# ---------- 注册：过滤 + 去重 ----------

def test_register_filters_decorative(tmp_path):
    store = make_store(tmp_path)
    assert store.register("tiny-icon", make_png_bytes(30, 30)) is None
    asset = store.register("real-image", make_png_bytes(200, 100))
    assert asset is not None and len(asset.sha256) == 64


def test_register_dedupes_same_content(tmp_path):
    store = make_store(tmp_path)
    data = make_png_bytes(200, 100)
    first = store.register("img-1", data)
    second = store.register("img-2", data)     # 同内容不同来源
    assert first is second                      # 同一资产对象，共享 URL


def test_register_keeps_undecodable_as_content(tmp_path):
    """解码失败按正文图片处理（不静默丢弃），进正常管线"""
    store = make_store(tmp_path)
    asset = store.register("broken.png", b"\x89PNG broken-bytes")
    assert asset is not None


# ---------- data URI ----------

def test_data_uri_roundtrip():
    data = make_png_bytes(50, 50)
    asset = ImageAsset("src", data)
    uri = asset.data_uri()
    assert uri.startswith("data:image/png;base64,")
    import base64
    assert base64.b64decode(uri.split(",", 1)[1]) == data


# ---------- 视觉产物缓存：按 kind 读写、按模型隔离 ----------

def test_text_cache_roundtrip(tmp_path):
    store = make_store(tmp_path)
    sha = hashlib.sha256(b"x").hexdigest()
    assert store.get_cached_text(sha, "ocr") is None
    store.save_cached_text(sha, "ocr", "转写内容")
    assert store.get_cached_text(sha, "ocr") == "转写内容"
    assert store.get_cached_text(sha, "alt") is None   # kind 互相独立


def test_cache_key_without_transport_dimension(tmp_path):
    """缓存键不含传图方式维度——VLM 对同一张图的转写不因 URL 还是 base64 传入而不同"""
    store = make_store(tmp_path)
    for path in store._prompt_dirs.values():
        assert "--url--" not in path.name and "--base64--" not in path.name


def test_cache_isolated_by_model(tmp_path):
    store_a = make_store(tmp_path, model="m-a")
    store_b = make_store(tmp_path, model="m-b")
    assert store_a._prompt_dirs != store_b._prompt_dirs


# ---------- 上传三级查找 ----------

def test_ensure_uploaded_three_levels(tmp_path):
    stub = StubUploader()
    store = ImageAssetStore(make_config(), tmp_path / "c1", stub)

    a1 = ImageAsset("s1", b"img-data")
    url = store.ensure_uploaded(a1)
    assert stub.uploads == 1 and a1.url == url

    # 进程内：同资产再取，不再上传
    assert store.ensure_uploaded(a1) == url
    assert stub.uploads == 1

    # 文件缓存：新资产对象（同 sha），不再上传
    a2 = ImageAsset("s2", b"img-data")
    assert store.ensure_uploaded(a2) == url
    assert stub.uploads == 1

    # 新 cache_dir（缓存文件不在）：重新上传
    store2 = ImageAssetStore(make_config(), tmp_path / "c2", stub)
    a3 = ImageAsset("s3", b"img-data")
    store2.ensure_uploaded(a3)
    assert stub.uploads == 2


def test_ensure_uploaded_rejects_no_uploader(tmp_path):
    store = make_store(tmp_path)
    asset = ImageAsset("s", b"data")
    with pytest.raises(RuntimeError):
        store.ensure_uploaded(asset)
