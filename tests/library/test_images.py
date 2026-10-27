"""图片资产库回归：过滤、去重、缓存、data URI（真字节 + PIL 造图，不联网）"""
import hashlib

import pytest
from PIL import Image

from omnimd.images import ImageAsset, ImageAssetStore
from omnimd.prompts import TaskSpec
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

_TRANSCRIBE = TaskSpec(name="transcribe", prompt="p")
_DESCRIBE = TaskSpec(name="describe", prompt="p", system="s", render="attribute")


def make_store(tmp_path, model="m", tasks=()):
    return ImageAssetStore(
        make_config(vlm_model=model), tmp_path / "cache", None, tasks=tasks,
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


# ---------- 任务产物缓存：按任务读写、按模型与提示词隔离 ----------

def test_text_cache_roundtrip(tmp_path):
    store = make_store(tmp_path, tasks=(_TRANSCRIBE, _DESCRIBE))
    sha = hashlib.sha256(b"x").hexdigest()
    assert store.get_cached_text(_TRANSCRIBE, sha) is None
    store.save_cached_text(_TRANSCRIBE, sha, "转写内容")
    assert store.get_cached_text(_TRANSCRIBE, sha) == "转写内容"
    assert store.get_cached_text(_DESCRIBE, sha) is None   # 任务互相隔离


def test_cache_miss_for_task_not_in_pipeline(tmp_path):
    """不在本次管线任务集里的任务查缓存：恒 miss（目录按注册表生成）"""
    store = make_store(tmp_path, tasks=(_TRANSCRIBE,))
    sha = hashlib.sha256(b"x").hexdigest()
    assert store.get_cached_text(_DESCRIBE, sha) is None


def test_cache_key_without_transport_dimension(tmp_path):
    """缓存键不含取图方式维度——VLM 对同一张图的转写不因 URL 还是 base64 传入而不同"""
    store = make_store(tmp_path, tasks=(_TRANSCRIBE, _DESCRIBE))
    for path in store._task_dirs.values():
        assert "--url--" not in path.name and "--base64--" not in path.name


def test_cache_isolated_by_model(tmp_path):
    store_a = make_store(tmp_path, model="m-a", tasks=(_TRANSCRIBE,))
    store_b = make_store(tmp_path, model="m-b", tasks=(_TRANSCRIBE,))
    assert store_a._task_dirs != store_b._task_dirs


def test_cache_isolated_by_task(tmp_path):
    """同图不同任务两目录——任务名是顶层隔离维度"""
    store = make_store(tmp_path, tasks=(_TRANSCRIBE, _DESCRIBE))
    t_dir = store._task_dirs["transcribe"]
    d_dir = store._task_dirs["describe"]
    assert t_dir.parent.name == "transcribe"
    assert d_dir.parent.name == "describe"
    assert t_dir != d_dir


def test_cache_isolated_by_prompt_digest(tmp_path):
    """同任务同模型、提示词不同（digest 不同）→ 目录不同：改提示词缓存自动失效"""
    variant = TaskSpec(name="transcribe", prompt="另一版提示词")
    store_a = make_store(tmp_path, tasks=(_TRANSCRIBE,))
    store_b = make_store(tmp_path, tasks=(variant,))
    assert store_a._task_dirs["transcribe"] != store_b._task_dirs["transcribe"]


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


def test_url_cache_isolated_by_storage_endpoint(tmp_path):
    """URL 缓存按端点/桶/前缀隔离（目录前缀 s3--）：换端点重传，同端点跨实例命中"""
    up_a, up_b = StubUploader(), StubUploader()
    store_a = ImageAssetStore(
        make_config(s3_endpoint_url="https://a.example"), tmp_path / "c", up_a)
    store_b = ImageAssetStore(
        make_config(s3_endpoint_url="https://b.example"), tmp_path / "c", up_b)
    store_a.ensure_uploaded(ImageAsset("s1", b"img"))
    store_b.ensure_uploaded(ImageAsset("s2", b"img"))
    assert up_a.uploads == 1 and up_b.uploads == 1   # 不同端点互不命中

    # 同端点新实例（磁盘缓存）：零上传；目录名锁定 s3--{摘要} 结构
    up_a2 = StubUploader()
    store_a2 = ImageAssetStore(
        make_config(s3_endpoint_url="https://a.example"), tmp_path / "c", up_a2)
    store_a2.ensure_uploaded(ImageAsset("s3", b"img"))
    assert up_a2.uploads == 0
    names = sorted(p.name for p in (tmp_path / "c" / "urls").iterdir())
    assert len(names) == 2 and all(n.startswith("s3--") for n in names)


def test_ensure_uploaded_rejects_no_uploader(tmp_path):
    store = make_store(tmp_path)
    asset = ImageAsset("s", b"data")
    with pytest.raises(RuntimeError):
        store.ensure_uploaded(asset)


# ---------- 缓存不可用降级 ----------

def test_cache_unwritable_degrades_gracefully(tmp_path, caplog):
    """缓存目录不可写：读降级为 miss、写跳过、上传照常，警告一次"""
    import logging
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    stub = StubUploader()
    store = ImageAssetStore(make_config(), blocker, stub, tasks=(_TRANSCRIBE,))
    with caplog.at_level(logging.WARNING, logger="omnimd.images"):
        assert store.get_cached_text(_TRANSCRIBE, "s1") is None  # 读降级为 miss
        store.save_cached_text(_TRANSCRIBE, "s1", "文本")          # 不抛、不写
        asset = ImageAsset("s1", b"img")
        url = store.ensure_uploaded(asset)                         # 上传不受影响
    assert stub.uploads == 1 and asset.url == url
    assert list(tmp_path.iterdir()) == [blocker]      # 未产生任何缓存文件
    assert caplog.text.count("缓存不可用") == 1        # 停用警告只发一次


def test_cache_write_failure_disables_cache(tmp_path, caplog, monkeypatch):
    """目录可建但写入失败（无写权限/磁盘满形态）：同样停用缓存且警告一次，不抛出"""
    import logging
    stub = StubUploader()
    cache = tmp_path / "cache"
    store = ImageAssetStore(make_config(), cache, stub, tasks=(_TRANSCRIBE,))

    def broken_write(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ImageAssetStore, "_atomic_write",
                        staticmethod(broken_write))
    with caplog.at_level(logging.WARNING, logger="omnimd.images"):
        store.save_cached_text(_TRANSCRIBE, "s1", "文本")        # 写失败 → 停用
        assert store.get_cached_text(_TRANSCRIBE, "s1") is None  # 停用后读直接跳过
        store.save_cached_text(_TRANSCRIBE, "s2", "文本")        # 停用后不再尝试写
    task_dir = cache / "transcribe"
    assert task_dir.is_dir() and not list(task_dir.glob("*.md"))
    assert caplog.text.count("缓存不可用") == 1
