"""渲染与注册纯函数回归：产出组成形态、失败路径、计数口径（不联网）"""
import base64
import hashlib

from omnimd.converter import register_images, render_blocks
from omnimd.images import ImageAsset


class FakeStore:
    """register / lookup 的最小实现——不触网、不落盘"""

    def __init__(self, assets=(), register_result="unset"):
        self._assets = {a.sha256: a for a in assets}
        self._register_result = register_result
        self.registered_sources = []

    def register(self, source, data):
        self.registered_sources.append(source)
        if self._register_result == "unset":
            return ImageAsset(source, data)   # 真实行为：注册并返回资产
        return None                            # 装饰性图片：丢弃

    def lookup(self, sha):
        return self._assets.get(sha)


def make_asset(data=b"image-bytes", url="https://cdn.example/img.png",
               ocr="图片转写文本", alt=""):
    """构造已上传、已转写的资产（真实 sha256，由 FakeStore 按哈希索引）"""
    asset = ImageAsset("test", data)
    asset.url = url
    asset.ocr_text = ocr
    asset.alt_text = alt
    return asset


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(md, store, parts):
    """parts 传集合——render_blocks 的契约形态"""
    return render_blocks(md, store, set(parts))


# ---------- render_blocks：产出组成形态 ----------

def test_render_full():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n", store, {"url", "text"})
    assert f"![图片]({asset.url})" in md
    assert asset.ocr_text in md
    assert count == 1


def test_render_url_only():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, {"url"})
    assert f"![图片]({asset.url})" in md
    assert asset.ocr_text not in md
    assert count == 1


def test_render_text_only():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, {"text"})
    assert "![" not in md
    assert asset.ocr_text in md
    assert count == 1


def test_render_url_alt():
    asset = make_asset(alt="架构图：三层分栏")
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, {"url", "alt"})
    assert f"![架构图：三层分栏]({asset.url})" in md
    assert asset.ocr_text not in md          # alt 不附带转写文本
    assert count == 1


def test_render_url_alt_text():
    asset = make_asset(alt="季度营收图")
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, {"url", "alt", "text"})
    assert f"![季度营收图]({asset.url})" in md
    assert asset.ocr_text in md
    assert count == 1


def test_render_alt_falls_back_when_empty():
    asset = make_asset(alt="")               # 描述失败：alt 为空回落「图片」
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, {"url", "alt"})
    assert f"![图片]({asset.url})" in md


# ---------- render_blocks：失败路径与边界 ----------

def test_render_drops_failed_upload():
    asset = make_asset(url="")          # 上传失败：无 URL
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n尾\n", store, {"url", "text"})
    assert asset.sha256 not in md and "正文" in md and "尾" in md
    assert count == 0


def test_render_failed_ocr_marker():
    asset = make_asset(ocr="")          # 转写失败：占位说明
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, {"url", "text"})
    assert "（图片转写失败）" in md


def test_render_unknown_placeholder():
    store = FakeStore()                 # lookup 返回 None
    md, count = render("@@IMG:" + "0" * 64 + "@@", store, {"url", "text"})
    assert md == ""
    assert count == 0


def test_render_counts_each_block():
    a1, a2 = make_asset(b"img-1"), make_asset(b"img-2")
    store = FakeStore(assets=[a1, a2])
    md, count = render(
        f"@@IMG:{a1.sha256}@@\n\n@@IMG:{a2.sha256}@@", store, {"url", "text"})
    assert count == 2


def test_render_collapses_blank_lines():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@\n\n\n\n尾", store, {"url", "text"})
    assert "\n\n\n" not in md


# ---------- register_images：data URI → 占位符 ----------

def test_register_images_replaces_data_uri():
    store = FakeStore()
    b64 = base64.b64encode(b"image-bytes").decode()
    md = register_images(f"![alt](data:image/png;base64,{b64})", store)
    assert md == f"@@IMG:{sha_of(b'image-bytes')}@@"
    assert len(store.registered_sources) == 1


def test_register_images_drops_decorative():
    store = FakeStore(register_result=None)
    b64 = base64.b64encode(b"icon").decode()
    md = register_images(f"前\n\n![x](data:image/png;base64,{b64})\n\n后", store)
    assert "data:" not in md and "@@IMG" not in md
    assert "前" in md and "后" in md
