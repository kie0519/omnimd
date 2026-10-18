"""anydoc 渲染纯函数回归：Document 模型 → GFM（假对象离线测，不依赖 anydoc 包）

覆盖 anydoc_engine 的 _render_* 家族：块级（标题/表格/列表/引用/代码/公式）、
行内（样式转义/run 合并/链接/脚注引用/复选框）、表格摊平（数据表 GFM 化 /
布局表段落化）、图片（资产注册 / 外链保留）、脚注定义与空段过滤。
"""
from types import SimpleNamespace as NS

from omnimd.engines.anydoc_engine import _render_document, _styled_text
from omnimd.images import ImageAsset


class FakeStore:
    """register 的最小实现：返回真实资产（sha256 可校验），装饰图返回 None"""

    def __init__(self, reject=False):
        self.reject = reject
        self.registered: list[bytes] = []

    def register(self, source, data):
        if self.reject:
            return None
        self.registered.append(data)
        return ImageAsset(source, data)


def text(t, **style):
    return NS(kind="text", text=t, style=NS(bold=style.get("bold", False),
                                            italic=style.get("italic", False),
                                            code=style.get("code", False),
                                            strike=style.get("strike", False)) if style else None)


def para(*inlines):
    return NS(kind="paragraph", content=list(inlines))


def heading(level, *inlines):
    return NS(kind="heading", level=level, content=list(inlines))


def render(doc_blocks, notes=(), store=None):
    doc = NS(blocks=list(doc_blocks), notes=list(notes))
    return _render_document(doc, store or FakeStore())


# ---------- 块级基础形态 ----------

def test_heading_and_paragraph():
    md = render([heading(2, text("章节")), para(text("正文", bold=True))])
    assert md == "## 章节\n\n**正文**"


def test_code_block_rule_math_quote():
    doc = [
        NS(kind="code_block", lang="python", text="x = 1"),
        NS(kind="rule"),
        NS(kind="math", text="E = mc^2"),
        NS(kind="block_quote", blocks=[para(text("引用一")), para(text("引用二"))]),
    ]
    md = render(doc)
    assert "```python\nx = 1\n```" in md
    assert "\n---\n" in md
    assert "$$\nE = mc^2\n$$" in md
    assert "> 引用一\n>\n> 引用二" in md


def test_blank_paragraphs_dropped():
    """整段空白（Word 空行）不产出段落，避免孤儿标记"""
    md = render([para(text("")), para(text("   ")), para(text("实文"))])
    assert md == "实文"


# ---------- 行内：样式与转义 ----------

def test_styled_text_escapes_and_wraps():
    assert _styled_text("a*b_c", None) == r"a\*b\_c"
    s = NS(bold=True, italic=True, code=False, strike=False)
    assert _styled_text("x", s) == "***x***"
    code_style = NS(bold=False, italic=False, code=True, strike=False)
    assert _styled_text("a|b", code_style) == "`a|b`"   # code 不转义（围栏内原文）


def test_adjacent_same_style_runs_merge():
    """Word 把一句话拆成多个 run：同样式合并，避免 **a****b** 破碎标记"""
    md = render([para(text("你"), text("好"))])
    assert md == "你好"
    md = render([para(text("加", bold=True), text("粗", bold=True))])
    assert md == "**加粗**"
    md = render([para(text("粗", bold=True), text("斜", italic=True))])
    assert md == "**粗***斜*"          # 不同样式不合并


def test_line_break_inlines_collapsed():
    md = render([para(text("第一行"), NS(kind="line_break"), text("第二行"))])
    assert md == "第一行 第二行"       # 软换行折叠为空格


def test_link_and_anchor_and_note_ref():
    doc = [para(
        NS(kind="link", content=[text("文档")],
           text="文档", target=NS(kind="url", value="https://example.com")),
        text(" "),
        NS(kind="link", content=[], text="跳转",
           target=NS(kind="anchor", value="sec-1")),
        text(" "),
        NS(kind="note_ref", note_id="1"),
    )]
    md = render(doc)
    assert "[文档](https://example.com)" in md
    assert "[跳转](#sec-1)" in md
    assert "[^1]" in md


def test_checkbox_inline():
    md = render([para(NS(kind="checkbox", checked=True), NS(kind="checkbox", checked=False))])
    assert "[x] " in md and "[ ] " in md


# ---------- 列表 ----------

def test_bullet_list_with_continuation():
    item1 = NS(marker_label=None, blocks=[para(text("甲")), para(text("续行"))])
    item2 = NS(marker_label=None, blocks=[para(text("乙"))])
    md = render([NS(kind="list", list=NS(marker="bullet", start=None, items=[item1, item2]))])
    # 续段按列表标记缩进（嵌套多段落的标准 GFM 形态）
    assert "- 甲\n\n  续行\n- 乙" in md


def test_ordered_markers():
    def one_list(marker, start=1):
        items = [NS(marker_label=None, blocks=[para(text("项"))]) for _ in range(2)]
        return NS(kind="list", list=NS(marker=marker, start=start, items=items))

    assert "1. 项\n2. 项" in render([one_list("decimal")])
    assert "a. 项\nb. 项" in render([one_list("lower_alpha")])
    assert "I. 项\nII. 项" in render([one_list("upper_roman")])
    assert "5. 项\n6. 项" in render([one_list("decimal", start=5)])
    # 显式 marker_label 优先于序列推算
    labeled = NS(marker_label="3.", blocks=[para(text("带标签"))])
    md = render([NS(kind="list", list=NS(marker="ordered", start=9, items=[labeled]))])
    assert "3. 带标签" in md


# ---------- 表格 ----------

def cell(*inlines):
    return NS(blocks=[para(*inlines)])


def origin(c):
    return NS(kind="origin", cell=c)


COVERED = NS(kind="covered", cell=None)


def test_data_table_gfm_with_merge_and_escape():
    """数据表 → GFM：covered 槽位留空（合并单元格摊平）、管道转义、多段 <br>"""
    two_para_cell = NS(blocks=[para(text("x|y")), para(text("续"))])
    grid = [
        [origin(cell(text("名称"))), origin(cell(text("值")))],
        [origin(two_para_cell), COVERED],            # 横向合并 + 多段落单元格
        [origin(cell(text("B"))), COVERED],          # 纵向合并的从属槽位
    ]
    md = render([NS(kind="table", table=NS(kind="data", grid=grid))])
    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert lines[0] == "| 名称 | 值 |"
    assert lines[1].startswith("| --- ")
    assert "| x\\|y<br>续 |  |" in md
    assert "| B |  |" in md


def test_layout_table_flattened_to_paragraphs():
    """布局脚手架（文本框定位表）不是数据表：摊平为段落，不造伪表格"""
    grid = [[origin(cell(text("文本框一"))), COVERED],
            [origin(cell(text("文本框二"))), origin(cell(text("并列表意")))]]
    md = render([NS(kind="table", table=NS(kind="layout", grid=grid))])
    assert "|" not in md
    assert "文本框一" in md and "文本框二" in md and "并列表意" in md
    assert md.count("\n\n") >= 1            # 摊平为多段落


def test_ragged_grid_padded():
    """行长度不齐的 grid 补空槽，不越界崩溃"""
    grid = [[origin(cell(text("只有一格")))],         # 第二列缺失
            [origin(cell(text("a"))), origin(cell(text("b")))]]
    md = render([NS(kind="table", table=NS(kind="data", grid=grid))])
    assert "| 只有一格 |  |" in md


# ---------- 图片与脚注 ----------

def test_asset_image_registers_to_store():
    store = FakeStore()
    png = b"\x89PNG-fake-bytes"
    doc = [para(NS(kind="image", alt="示意图",
                   source=NS(kind="asset", asset_id=0)))]
    doc_assets = [NS(media_type="image/png", data=png, origin_part=None)]
    _render_document(NS(blocks=doc, notes=[], assets=doc_assets), store)
    assert store.registered == [png]


def test_asset_image_rejected_drops_image():
    """注册被拒（装饰图）时图片整体丢弃（与装饰图语义一致），不留占位符"""
    store = FakeStore(reject=True)
    doc = [para(text("前")), para(NS(kind="image", alt="装饰",
                   source=NS(kind="asset", asset_id=0))), para(text("后"))]
    out = _render_document(
        NS(blocks=doc, notes=[], assets=[NS(media_type="image/png", data=b"x", origin_part=None)]),
        store)
    assert "装饰" not in out and "@@IMG" not in out
    assert "前" in out and "后" in out


def test_non_image_asset_keeps_alt():
    """非图片资产（OLE 嵌入等）不可转写，保留 alt 描述"""
    doc = [para(NS(kind="image", alt="嵌入对象",
                   source=NS(kind="asset", asset_id=0)))]
    out = _render_document(
        NS(blocks=doc, notes=[],
           assets=[NS(media_type="application/ole", data=b"z", origin_part=None)]),
        FakeStore())
    assert out == "嵌入对象"


def test_external_image_kept_as_is():
    """外链图已是公开 URL：保留链接，不进托管管线"""
    md = render([para(NS(kind="image", alt="外图",
                         source=NS(kind="external", url="https://cdn.example/a.png")))])
    assert "![外图](https://cdn.example/a.png)" in md


def test_footnote_definitions_rendered():
    notes = [NS(id="1", blocks=[para(text("脚注内容"))])]
    md = render([para(text("正文"), NS(kind="note_ref", note_id="1"))], notes=notes)
    assert "[^1]: 脚注内容" in md
    assert "正文[^1]" in md


def test_unknown_block_falls_back_to_inline_content():
    """未知块类型不丢内容：按行内渲染（前向兼容新版本 anydoc）"""
    md = render([NS(kind="future_kind", content=[text("内容")])])
    assert "内容" in md
