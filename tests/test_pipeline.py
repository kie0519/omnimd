"""转换管线集成回归：产出组合 × 传输方式的矩阵（stub 上传/视觉模型，不联网）

锁定 _process_assets 上传与传图编排的两条不变量：
仅链接产出也必须完成上传（否则图片静默丢弃）；
base64 传输时视觉任务直接接收 data URI，产出的链接位置不出现 data URI。
"""
import tempfile

import pytest
from PIL import Image, ImageDraw

from omnimd.converter import Converter
from conftest import make_config


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


def _convert_with_stubs(monkeypatch, tmp_path, png, output, base64):
    uploader, vision = StubUploader(), StubVision()
    conv = Converter(make_config(
        cache_dir=tmp_path, vlm_image_input="base64" if base64 else "url"))
    conv._uploader = uploader                  # 跳过真实存储客户端构造
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(png, output=output)
    return result, uploader, vision


# (output, base64, 期望上传数, 期望视觉调用数, 期望产出含公开链接, 期望含转写文本)
MATRIX = [
    ("url",          False, 1, 0, True,  False),
    ("url,text",     False, 1, 1, True,  True),
    ("url,text,alt", False, 1, 2, True,  True),
    ("url,alt",      False, 1, 1, True,  False),
    ("text",         False, 1, 1, False, True),
    ("text",         True,  0, 1, False, True),   # text 路线无存储：转写必须渲染
    ("url,text",     True,  1, 1, True,  True),   # 上传供渲染，传图走 data URI
]


@pytest.mark.parametrize("output,base64,uploads,calls,link,text", MATRIX)
def test_output_transport_matrix(monkeypatch, tmp_path, probe_png,
                                 output, base64, uploads, calls, link, text):
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, output, base64)
    assert uploader.uploads == uploads
    assert len(vision.refs) == calls
    assert ("https://cdn.example/" in result.markdown) == link
    assert "data:image" not in result.markdown      # data URI 不进产出
    assert ("转写内容" in result.markdown) == text   # 转写部件真实渲染
    assert result.image_count == 1


def test_url_only_skips_vlm_construction(monkeypatch, tmp_path, probe_png):
    """仅链接产出不构造视觉模型（openai SDK 不加载）"""
    def fail(self):
        raise AssertionError("仅链接产出不应构造视觉模型")
    conv = Converter(make_config(cache_dir=tmp_path))
    conv._uploader = StubUploader()
    monkeypatch.setattr(type(conv), "vision", property(fail))
    result = conv.convert(probe_png, output="url")
    assert result.image_count == 1


def test_base64_vlm_ref_is_data_uri(monkeypatch, tmp_path, probe_png):
    """base64 传输：视觉模型收到 data URI，渲染链接仍是公开 URL（两者分离）"""
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, "url,text", base64=True)
    assert vision.refs[0].startswith("data:image/png;base64,")
    assert "https://cdn.example/" in result.markdown
    assert result.images[0].url.startswith("https://cdn.example/")


def test_empty_result_not_cached_allows_retry(monkeypatch, tmp_path, probe_png):
    """空转写/空描述不落缓存：成功转写按提示词契约必非空，空串即失败
    产物，不缓存以免固化失败；非空结果正常缓存，重跑不再调用"""
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
        conv = Converter(make_config(cache_dir=tmp_path, vlm_image_input="base64"))
        conv._uploader = StubUploader()
        monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
        return conv.convert(probe_png, output="url,text,alt")

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
