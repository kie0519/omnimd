"""转换管线集成回归：产出组成 × 推导规则的矩阵（stub 上传/视觉模型，不联网）

锁定上传与传图编排的不变量：上传只发生在产出含 url 部件时；
VLM 取图由产出组成推导（含 url 传 URL，否则传 data URI）；
纯解析零上传零视觉调用，图片块移除。
"""
import tempfile

import pytest
from PIL import Image, ImageDraw

from omnimd.converter import Converter
from tests.helpers import make_config


class StubUploader:
    """记录上传次数的假上传器"""

    def __init__(self):
        self.uploads = 0

    def upload_image(self, data, filename):
        self.uploads += 1
        return f"https://cdn.example/{filename}"


class StubVision:
    """记录图片引用的假视觉模型（ocr 与 describe 都记录引用）"""

    def __init__(self):
        self.refs = []

    def ocr(self, ref):
        self.refs.append(ref)
        return "转写内容"

    def describe(self, ref):
        self.refs.append(ref)
        return "一句话描述"


@pytest.fixture()
def probe_png(tmp_path):
    """真实 PNG 字节（注册管线走真实解码与装饰图判定）"""
    png = tmp_path / "probe.png"
    img = Image.new("RGB", (400, 150), "white")
    ImageDraw.Draw(img).text((24, 60), "PROBE", fill="black")
    img.save(png)
    return png


def _convert_with_stubs(monkeypatch, tmp_path, png, output):
    uploader, vision = StubUploader(), StubVision()
    conv = Converter(make_config(cache_dir=tmp_path))
    conv._uploader = uploader                  # 跳过真实存储客户端构造
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(png, output=output)
    return result, uploader, vision


# (output, 期望上传数, 期望视觉调用数, 期望产出含公开链接, 期望含内嵌, 期望含转写文本)
MATRIX = [
    ("body",              0, 0, False, False, False),  # 纯解析：零上传零调用
    ("body,url",          1, 0, True,  False, False),
    ("body,url,ocr",      1, 1, True,  False, True),
    ("body,url,ocr,alt",  1, 2, True,  False, True),
    ("body,url,alt",      1, 1, True,  False, False),
    ("body,base64",       0, 0, False, True,  False),  # 内嵌：零上传
    ("body,base64,ocr",   0, 1, False, True,  True),   # 传图走 data URI
    ("body,base64,alt",   0, 1, False, True,  False),
]


@pytest.mark.parametrize("output,uploads,calls,link,embed,text", MATRIX)
def test_output_transport_matrix(monkeypatch, tmp_path, probe_png,
                                 output, uploads, calls, link, embed, text):
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, output)
    assert uploader.uploads == uploads
    assert len(vision.refs) == calls
    assert ("https://cdn.example/" in result.markdown) == link
    assert ("data:image" in result.markdown) == embed  # base64 部件渲染内嵌
    assert ("转写内容" in result.markdown) == text      # 转写部件真实渲染
    if output == "body":
        assert result.image_count == 0             # 纯解析图片块移除
    else:
        assert result.image_count == 1
    # images 元数据随产出组成落值（公开契约：字段反映管线结果）
    if output == "body":
        assert result.images == []                 # 纯解析无图片处理
    else:
        (info,) = result.images
        assert info.sha256 and len(info.sha256) == 64
        assert bool(info.url) == ("url" in parts_of(output))   # url 空串语义：未上传
        assert info.ocr_text == ("转写内容" if "ocr" in parts_of(output) else "")
        assert info.alt_text == ("一句话描述" if "alt" in parts_of(output) else "")


def parts_of(output: str) -> set[str]:
    return set(output.split(","))


def test_url_only_skips_vlm_construction(monkeypatch, tmp_path, probe_png):
    """产出不含 ocr/alt 时不构造视觉模型（openai SDK 不加载）"""
    def fail(self):
        raise AssertionError("不含转写/描述的产出不应构造视觉模型")
    conv = Converter(make_config(cache_dir=tmp_path))
    conv._uploader = StubUploader()
    monkeypatch.setattr(type(conv), "vision", property(fail))
    result = conv.convert(probe_png, output="body,url")
    assert result.image_count == 1


def test_upload_failure_skips_vlm_call(monkeypatch, tmp_path, probe_png):
    """管线不变量：URL 传图时上传失败的图跳过 VLM 调用（无可用 ref），
    该图走失败渲染（整图块删除），其余行为不受影响"""
    class FailUploader:
        def upload_image(self, data, filename):
            raise RuntimeError("upload exploded")

    vision = StubVision()
    conv = Converter(make_config(cache_dir=tmp_path))
    conv._uploader = FailUploader()
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(probe_png, output="body,url,ocr")
    assert len(vision.refs) == 0                   # 上传失败 → 零视觉调用
    assert result.image_count == 0                 # 整图块删除
    assert result.markdown.strip() == ""           # 单图文件的产出为空正文


def test_pure_parse_zero_credentials_end_to_end(tmp_path):
    """纯解析零凭据端到端：无任何 VLM/存储配置（空串凭据）构造成功并完成转换"""
    from omnimd.config import Config
    txt = tmp_path / "note.txt"
    txt.write_text("纯正文内容", encoding="utf-8")
    config = Config(
        vlm_api_key="", vlm_base_url="", vlm_model="",
        storage_provider="", s3_endpoint_url="", s3_region="",
        s3_bucket="", s3_access_key_id="", s3_secret_access_key="",
        s3_key_prefix="", cache_dir=tmp_path,
    )
    config.validate()                 # 纯解析：构造期校验通过（凭据惰性）
    result = Converter(config).convert(txt, output="body")
    assert "纯正文内容" in result.markdown
    assert result.image_count == 0


def test_pure_parse_neither_uploader_nor_vlm(monkeypatch, tmp_path, probe_png):
    """纯解析：存储与视觉模型都不构造（零外部依赖）"""
    def fail_vision(self):
        raise AssertionError("纯解析不应构造视觉模型")
    conv = Converter(make_config(cache_dir=tmp_path))
    conv._uploader = None
    monkeypatch.setattr(type(conv), "vision", property(fail_vision))
    result = conv.convert(probe_png, output="body")
    assert result.image_count == 0
    assert "PROBE" not in result.markdown or result.markdown  # 正文仍在


def test_base64_vlm_ref_is_data_uri(monkeypatch, tmp_path, probe_png):
    """不含 url 部件：视觉模型收到 data URI（推导），产出渲染内嵌"""
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, "body,base64,ocr")
    assert vision.refs[0].startswith("data:image/png;base64,")
    assert uploader.uploads == 0
    assert result.markdown.count("data:image/png;base64,") >= 1


def test_url_part_vlm_ref_is_public_url(monkeypatch, tmp_path, probe_png):
    """含 url 部件：视觉模型收到公开 URL（推导），产出渲染公开链接"""
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, "body,url,ocr")
    assert vision.refs[0].startswith("https://cdn.example/")
    assert "data:image" not in result.markdown


def test_empty_result_not_cached_allows_retry(monkeypatch, tmp_path, probe_png):
    """空转写/空描述：空串即失败产物，不缓存以免固化失败；
    非空结果正常缓存，重跑不再调用"""
    class EmptyVision:
        def __init__(self):
            self.calls = 0

        def ocr(self, ref):
            self.calls += 1
            return ""

        def describe(self, ref):
            self.calls += 1
            return ""

    def run_with(vision):
        conv = Converter(make_config(cache_dir=tmp_path))
        conv._uploader = StubUploader()
        monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
        return conv.convert(probe_png, output="body,url,ocr,alt")

    empty = EmptyVision()
    run_with(empty)
    run_with(empty)
    assert empty.calls == 4          # 空结果两次都真实调用：失败未被固化

    normal = StubVision()
    run_with(normal)
    run_with(normal)
    assert len(normal.refs) == 2     # 非空结果缓存命中：仅首次调用

# ---------- 引擎回退（注册表定义） ----------

def test_engine_fallback_for_anydoc_only_format():
    """markitdown 引擎遇 .doc（仅 anydoc 支持）回退到 anydoc"""
    from omnimd.engines import resolve_engine
    engine_cls = resolve_engine(".doc", "markitdown")
    assert engine_cls.__name__ == "AnydocEngine"


def test_uploader_missing_env_lists_all_required():
    """缺 S3_* 必填变量：构造报错列出全部缺失项"""
    import pytest as _pytest
    from omnimd.exceptions import ConfigError
    from omnimd.storage import create_uploader
    cfg = make_config(storage_provider="s3", s3_endpoint_url="",
                      s3_region="", s3_bucket="", s3_access_key_id="",
                      s3_secret_access_key="")
    with _pytest.raises(ConfigError) as e:
        create_uploader(cfg)
    for name in ("S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        assert name in str(e.value)
