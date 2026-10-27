"""渲染与注册纯函数回归：呈现 × 任务形态、失败路径、图片块计数（不联网）"""
import base64
import hashlib

from omnimd.converter import PipelinePlan, register_images, render_blocks
from omnimd.images import ImageAsset
from omnimd.prompts import TaskSpec


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


_TRANSCRIBE = TaskSpec(name="transcribe", prompt="p")
_DESCRIBE = TaskSpec(name="describe", prompt="p", render="attribute")


def make_asset(data=b"image-bytes", url="https://cdn.example/img.png",
               transcribe="图片转写文本", describe=""):
    """构造已上传、已有任务产物的资产（真实 sha256，由 FakeStore 按哈希索引）"""
    asset = ImageAsset("test", data)
    asset.url = url
    if transcribe is not None:
        asset.texts["transcribe"] = transcribe
    if describe is not None:
        asset.texts["describe"] = describe
    return asset


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(md, store, presentation, tasks=()):
    """组装 PipelinePlan 调用 render_blocks；计数取渲染清单长度"""
    plan = PipelinePlan(presentation=presentation, tasks=tuple(tasks))
    out, rendered = render_blocks(md, store, plan)
    return out, len(rendered)


# ---------- render_blocks：呈现 × 任务形态 ----------

def test_render_link_transcribe():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n", store, "link", [_TRANSCRIBE])
    assert f"![图片]({asset.url})" in md
    assert asset.texts["transcribe"] in md
    assert count == 1


def test_render_link_only():
    asset = make_asset(transcribe=None, describe=None)
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, "link")
    assert f"![图片]({asset.url})" in md
    assert count == 1


def test_render_drop_without_tasks_removes_images():
    """drop 无任务（纯解析）：图片块移除，只留正文"""
    asset = make_asset(transcribe=None, describe=None)
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n\n尾", store, "drop")
    assert "![" not in md
    assert "正文" in md and "尾" in md
    assert count == 0


def test_render_drop_with_transcribe_keeps_text():
    """drop + 转写：不留图，转写文本独立成段占图片原位（纯文本库形态）"""
    asset = make_asset(url="")
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n\n尾", store,
                       "drop", [_TRANSCRIBE])
    assert "![" not in md and "data:image" not in md
    assert asset.texts["transcribe"] in md
    assert "正文" in md and "尾" in md
    assert count == 0                    # drop 形态无图片块


def test_render_embed_uses_data_uri():
    """embed 呈现：图片以 data URI 内嵌产出（不依赖上传）"""
    asset = make_asset(url="", transcribe=None, describe=None)
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, "embed")
    assert md.startswith("![图片](data:image/png;base64,")
    assert count == 1


def test_render_embed_transcribe():
    asset = make_asset(url="")
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, "embed", [_TRANSCRIBE])
    assert "data:image/png;base64," in md
    assert asset.texts["transcribe"] in md
    assert count == 1


def test_render_describe_fills_alt_slot():
    """describe（attribute）：产物填入链接描述位，不产生文本块"""
    asset = make_asset(transcribe=None, describe="架构图：三层分栏")
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, "link", [_DESCRIBE])
    assert f"![架构图：三层分栏]({asset.url})" in md
    assert count == 1


def test_render_describe_and_transcribe_combined():
    """描述入描述位 + 转写跟图片块后：两任务并存"""
    asset = make_asset(describe="季度营收图")
    store = FakeStore(assets=[asset])
    md, count = render(f"@@IMG:{asset.sha256}@@", store, "link",
                       [_DESCRIBE, _TRANSCRIBE])
    assert f"![季度营收图]({asset.url})" in md
    assert asset.texts["transcribe"] in md
    assert count == 1


def test_render_describe_empty_falls_back():
    asset = make_asset(transcribe=None, describe="")   # 描述失败：回落「图片」
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, "link", [_DESCRIBE])
    assert f"![图片]({asset.url})" in md


def test_render_multiple_block_tasks_in_order():
    """多 block 任务并存：按任务顺序追加，渲染无任何标签"""
    walk = TaskSpec(name="walk", prompt="p")
    asset = ImageAsset("test", b"image-bytes")
    asset.url = "https://cdn.example/img.png"
    asset.texts["walk"] = "走查结论"
    asset.texts["transcribe"] = "转写文本"
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, "link",
                   [_TRANSCRIBE, walk])
    assert md.index("转写文本") < md.index("走查结论")   # 声明顺序即渲染顺序


# ---------- render_blocks：失败路径与边界 ----------

def test_render_drops_failed_upload():
    asset = make_asset(url="")          # 上传失败：无 URL
    store = FakeStore(assets=[asset])
    md, count = render(f"正文\n\n@@IMG:{asset.sha256}@@\n尾\n", store,
                       "link", [_TRANSCRIBE])
    assert asset.sha256 not in md and "正文" in md and "尾" in md
    assert count == 0


def test_render_failed_transcribe_marker():
    """转写失败的产物是管线写入的失败占位（渲染层原样透传，逐字节锁定产出兼容契约）"""
    asset = make_asset(transcribe="（图片转写失败）")
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, "link", [_TRANSCRIBE])
    assert "（图片转写失败）" in md


def test_render_failed_custom_task_names_task():
    """自定义任务失败：管线写入的占位点名任务名，渲染原样透传"""
    walk = TaskSpec(name="walk", prompt="p")
    asset = make_asset(transcribe=None)
    asset.texts["walk"] = "（图片任务 walk 失败）"
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@", store, "link", [walk])
    assert "（图片任务 walk 失败）" in md


def test_render_unknown_placeholder():
    store = FakeStore()                 # lookup 返回 None
    md, count = render("@@IMG:" + "0" * 64 + "@@", store, "link", [_TRANSCRIBE])
    assert md == ""
    assert count == 0


def test_render_counts_each_block():
    a1, a2 = make_asset(b"img-1"), make_asset(b"img-2")
    store = FakeStore(assets=[a1, a2])
    md, count = render(
        f"@@IMG:{a1.sha256}@@\n\n@@IMG:{a2.sha256}@@", store, "link", [_TRANSCRIBE])
    assert count == 2


def test_render_collapses_blank_lines():
    asset = make_asset()
    store = FakeStore(assets=[asset])
    md, _ = render(f"@@IMG:{asset.sha256}@@\n\n\n\n尾", store, "link", [_TRANSCRIBE])
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


def test_collapse_blank_lines_fence_close_rules():
    """闭合围栏按 CommonMark 判定：外层 ```` 内的 ``` 是内容不是闭合标记，
    围栏内空行原样保留，围栏外连续空行照常压缩"""
    from omnimd.converter import _collapse_blank_lines
    md = "a\n\n\n````\n```js\nx = 1\n\n\n```\n````\n\n\nb"
    out = _collapse_blank_lines(md)
    assert "```js\nx = 1\n\n\n```" in out     # 内层 ``` 与围栏内空行原样
    assert out.endswith("\n\nb")              # 围栏外压缩为单个空行
