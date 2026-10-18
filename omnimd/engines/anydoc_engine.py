"""anydoc 引擎：非 PDF 格式的高质量正文解析（可配置选项）

anydoc（Firecrawl，Rust 实现）在正文质量上优于 markitdown：结构保真更好
（合并单元格摊平、脚注、LaTeX 公式），且补齐 markitdown 不覆盖的旧格式
与开放文档格式（清单见 engines 注册表）。

anydoc 的 to_markdown 对嵌入图片有损（只剩 alt 文本，alt 为空则连标记都不留），
因此本引擎不用它的现成 Markdown 输出，而是解析为 Document 模型
（to_document——图片字节与正文位置全保留）后自行渲染为 GFM：

- 嵌入图片 → store.register 注册进图片管线，正文留 @@IMG:{sha256}@@ 占位符
- 正文结构（标题/表格/列表/公式/脚注/引用）→ 按 anydoc 序列化约定渲染

即正文质量用 anydoc 的，图片托管与转写是本工具的。

PDF 永不路由到本引擎：anydoc 的 PDF 路线不提取图片且不支持 to_document，
图片保真只有 pymupdf4llm 一条路。
"""
import logging
import re
from pathlib import Path

from .base import ParseEngine

logger = logging.getLogger(__name__)

# GFM 行内特殊字符转义（与 anydoc 自家序列化器同口径，产出风格一致）
_INLINE_ESCAPE_RE = re.compile(r"([\\*_`\[\]])")


class AnydocEngine(ParseEngine):
    def __init__(self):
        import anydoc
        self._anydoc = anydoc

    def convert(self, file_path: Path, store) -> str:
        anydoc = self._anydoc
        data = file_path.read_bytes()
        # 内容探测优先（改错扩展名的文件仍能正确转换），无签名格式回退扩展名
        fmt = anydoc.format_from_bytes(data) or anydoc.format_from_extension(
            file_path.suffix.lower()
        )
        if fmt is None:
            from ..exceptions import UnsupportedFormatError
            raise UnsupportedFormatError(
                f"无法识别文件格式：{file_path.name}。"
                f"请确认文件未损坏（内容与扩展名不符也会触发），"
                f"或改用 DOC_ENGINE=markitdown 重试。"
            )
        if fmt == "pdf":
            # to_document 不支持 pdf——不可达分支的防御性拒绝
            raise ValueError("anydoc 引擎不支持 PDF（不提取图片），PDF 走 pymupdf4llm")
        document = anydoc.to_document(data, fmt)
        markdown = _render_document(document, store)
        if not markdown.strip():
            raise ValueError(
                f"anydoc 解析结果为空: {file_path.name}。"
                f"请确认文件非空且非扫描件，或改用 DOC_ENGINE=markitdown 重试。"
            )
        return markdown


# ========== Document 模型 → GFM 渲染 ==========

_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

# 有序列表 marker 的 GFM 等价物；无映射的稀有 marker 与超出字母表/
# 罗马数字范围的序号退化为十进制
def _alpha(fmt: str):
    def label(n: int) -> str:
        if 1 <= n <= 26:
            return chr(ord(fmt) + n - 1)
        return str(n)
    return label


_ORDERED_MARKERS = {
    "decimal": str, "ordered": str,
    "lower_alpha": _alpha("a"),
    "upper_alpha": _alpha("A"),
    "lower_roman": lambda n: _ROMAN[n - 1].lower() if n <= 10 else str(n),
    "upper_roman": lambda n: _ROMAN[n - 1] if n <= 10 else str(n),
}


def _ordered_label(marker: str, item, index: int, start: int) -> str:
    fmt = _ORDERED_MARKERS.get(marker, str)
    if item.marker_label:
        # 源文档的显式标签：归一为单句点结尾（'3' 与 '3.' 同渲染）
        return f"{item.marker_label.rstrip('..')}."
    return f"{fmt((start or 1) + index)}."


def _render_document(doc, store) -> str:
    """正文 + 脚注定义（GFM 脚注语法 [^id]）"""
    parts = [_render_blocks(doc.blocks, doc, store)]
    if doc.notes:
        parts.append("\n".join(_render_note(n, doc, store) for n in doc.notes))
    return "\n\n".join(p for p in parts if p.strip())


def _render_blocks(blocks, doc, store) -> str:
    parts = []
    for block in blocks:
        text = _render_block(block, doc, store)
        # 整段只剩空白（Word 空行、孤立换行符）不产出段落，避免 "\" 之类的孤儿标记
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _render_block(block, doc, store) -> str:
    kind = block.kind
    if kind == "heading":
        return f"{'#' * (block.level or 1)} {_render_inlines(block.content, doc, store)}"
    if kind == "paragraph":
        return _render_inlines(block.content, doc, store)
    if kind == "list":
        return _render_list(block.list, doc, store)
    if kind == "table":
        return _render_table(block.table, doc, store)
    if kind == "block_quote":
        inner = _render_blocks(block.blocks, doc, store)
        return "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
    if kind == "code_block":
        return f"```{block.lang or ''}\n{block.text or ''}\n```"
    if kind == "rule":
        return "---"
    if kind == "math":
        return f"$$\n{block.text or ''}\n$$"
    logger.debug("未知块类型 %s，按行内内容渲染", kind)
    return _render_inlines(block.content, doc, store)


def _render_list(lst, doc, store) -> str:
    lines = []
    for index, item in enumerate(lst.items):
        body = _render_blocks(item.blocks, doc, store)
        first, *rest = body.split("\n") if body else [""]
        if lst.marker == "bullet":
            label = "-"
        else:
            label = _ordered_label(lst.marker, item, index, lst.start)
        lines.append(f"{label} {first}".rstrip())
        # 续行（嵌套块、多段落）按列表标记宽度缩进
        indent = " " * (len(label) + 1)
        lines.extend(f"{indent}{line}" if line else "" for line in rest)
    return "\n".join(lines)


def _render_table(table, doc, store) -> str:
    if not table.grid:
        return ""
    if table.kind == "layout":
        # 布局脚手架（文本框定位表等）不是数据表：摊平为段落，不造伪表格
        parts = []
        for row in table.grid:
            for slot in row:
                if slot.kind != "origin":
                    continue
                text = _render_blocks(slot.cell.blocks, doc, store)
                if text.strip():
                    parts.append(text)
        return "\n\n".join(parts)
    # 数据表 → GFM 表格；跨行跨列摊平（covered 槽位留空）
    ncols = max(len(row) for row in table.grid)
    lines = []
    for row_index, row in enumerate(table.grid):
        cells = []
        for col in range(ncols):
            slot = row[col] if col < len(row) else None
            if slot is None or slot.kind != "origin":
                cells.append("")
                continue
            cell = _render_blocks(slot.cell.blocks, doc, store)
            # GFM 表格单元不能含块级元素：多段落以单个 <br> 相接
            # （段落分隔 \n\n 与段内单 \n 统一收敛），管道转义
            flattened = "<br>".join(
                seg.replace("\n", "<br>") for seg in cell.split("\n\n"))
            cells.append(flattened.replace("|", "\\|").strip())
        lines.append(f"| {' | '.join(cells)} |")
        if row_index == 0:
            lines.append("|" + " --- |" * ncols)
    return "\n".join(lines)


def _render_note(note, doc, store) -> str:
    body = _render_blocks(note.blocks, doc, store).replace("\n", " ").strip()
    return f"[^{note.id}]: {body}"


class _SpaceInline:
    """软换行折叠出的空格段（无样式 text，可参与 run 合并）"""
    kind = "text"
    text = " "
    style = None


_SPACE = _SpaceInline()


def _render_inlines(inlines, doc, store) -> str:
    # 段内软换行（line_break）是排版换行不是语义分段，折叠为空格——
    # 与 Markdown 渲染器对单换行的处理一致；空格以无样式 text 参与
    # 后续 run 合并（并入相邻同样式段，不产生破碎标记）
    inlines = [_SPACE if il.kind == "line_break" else il for il in inlines or []]
    # 相邻同样式 text 段先合并（Word 常把一句话拆成多个 run），
    # 避免 **360****AI** 这种破碎的强调标记
    merged = []
    for il in inlines:
        if (il.kind == "text" and merged and merged[-1].kind == "text"
                and _style_key(merged[-1].style) == _style_key(il.style)):
            merged[-1] = _MergedInline(il.kind, merged[-1].text + (il.text or ""), il.style)
        else:
            merged.append(il)
    return "".join(_render_inline(il, doc, store) for il in merged)


class _MergedInline:
    """同类样式 text 段合并后的替身，只携带渲染所需字段"""
    def __init__(self, kind, text, style):
        self.kind, self.text, self.style = kind, text, style


def _style_key(style) -> tuple:
    if style is None:
        return None
    return (style.bold, style.italic, style.code, style.strike)


def _render_inline(il, doc, store) -> str:
    kind = il.kind
    if kind == "text":
        return _styled_text(il.text or "", il.style)
    if kind == "link":
        inner = _render_inlines(il.content, doc, store) or _styled_text(il.text or "", None)
        url = f"#{il.target.value}" if il.target.kind == "anchor" else il.target.value
        return f"[{inner}]({url})"
    if kind == "image":
        return _render_image(il, doc, store)
    if kind == "anchor":
        return f'<a id="{il.anchor}"></a>'
    if kind == "note_ref":
        return f"[^{il.note_id}]"
    if kind == "math":
        return f"${il.text or ''}$"
    if kind == "checkbox":
        return "[x] " if il.checked else "[ ] "
    logger.debug("未知行内类型 %s，跳过", kind)
    return ""


def _render_image(il, doc, store) -> str:
    source = il.source
    if source.kind == "asset":
        asset_obj = None
        if source.asset_id is not None and source.asset_id < len(doc.assets):
            asset_obj = doc.assets[source.asset_id]
        if asset_obj is not None and (asset_obj.media_type or "").startswith("image/"):
            registered = store.register(asset_obj.origin_part or asset_obj.media_type,
                                        asset_obj.data)
            return f"@@IMG:{registered.sha256}@@" if registered else ""
        # 非图片资产（OLE 嵌入对象等）不可转写，保留 alt 描述
        return il.alt or ""
    if source.kind == "external" and source.url:
        # 外链图已是公开 URL，保留链接，不进图片托管管线
        return f"![{il.alt or '图片'}]({source.url})"
    return il.alt or ""


def _styled_text(text: str, style) -> str:
    if style is not None and style.code:
        return f"`{text}`"
    out = _INLINE_ESCAPE_RE.sub(r"\\\1", text)
    if style is None:
        return out
    if style.bold:
        out = f"**{out}**"
    if style.italic:
        out = f"*{out}*"
    if style.strike:
        out = f"~~{out}~~"
    return out
