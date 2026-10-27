"""转换管线集成回归：呈现 × 任务的矩阵 + 类型路由缺省（stub 上传/视觉模型，不联网）

锁定上传与传图编排的不变量：上传只发生在 link 呈现时；
VLM 取图由呈现值推导（link 传 URL，drop/embed 传 data URI）；
图片文件裸命令注入完整转写（转写就是图片的解析）；文字类裸命令零调用零上传。
"""
import pytest
from PIL import Image, ImageDraw

from omnimd.converter import Converter
from omnimd.prompts import build_registry
from omnimd.vlm.base import CompletionResult, Usage
from tests.helpers import make_config


class StubUploader:
    """记录上传次数的假上传器"""

    def __init__(self):
        self.uploads = 0

    def upload_image(self, data, filename):
        self.uploads += 1
        return f"https://cdn.example/{filename}"


class StubVision:
    """记录图片引用与请求的假视觉模型：按 max_tokens 分流任务产物
    （describe=512 一句话，其余=完整转写），usage 可供汇总断言"""

    def __init__(self, usage=None):
        self.refs = []
        self.requests = []
        self.usage = usage

    def run(self, request, ref):
        self.refs.append(ref)
        self.requests.append(request)
        text = "一句话描述" if request.max_tokens == 512 else "转写内容"
        return CompletionResult(text=text, usage=self.usage)


@pytest.fixture()
def probe_png(tmp_path):
    """真实 PNG 字节（注册管线走真实解码与装饰图判定）"""
    png = tmp_path / "probe.png"
    img = Image.new("RGB", (400, 150), "white")
    ImageDraw.Draw(img).text((24, 60), "PROBE", fill="black")
    img.save(png)
    return png


def _convert_with_stubs(monkeypatch, tmp_path, path, **params):
    uploader, vision = StubUploader(), StubVision()
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = uploader                  # 跳过真实存储客户端构造
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(path, **params)
    return result, uploader, vision


# (参数, 期望上传数, 期望视觉调用数, 期望产出含公开链接, 期望含内嵌, 期望含转写文本, 期望含描述文本)
# 输入是图片文件（probe.png）：无任务动词时类型路由注入完整转写（呈现值不影响注入）
MATRIX = [
    ({},                                        0, 1, False, False, True,  False),  # 裸命令=完整转写
    ({"images": "link"},                        1, 1, True,  False, True,  False),  # 呈现不阻断注入
    ({"images": "link", "transcribe": True},    1, 1, True,  False, True,  False),
    ({"images": "link", "describe": True},      1, 1, True,  False, False, True),
    ({"images": "link", "transcribe": True,
      "describe": True},                        1, 2, True,  False, True,  True),
    ({"images": "embed"},                       0, 1, False, True,  True,  False),
    ({"images": "embed", "transcribe": True},   0, 1, False, True,  True,  False),
    ({"transcribe": True},                      0, 1, False, False, True,  False),  # 纯文本库：不留图只要文字
]


@pytest.mark.parametrize("params,uploads,calls,link,embed,text,desc", MATRIX)
def test_presentation_task_matrix(monkeypatch, tmp_path, probe_png,
                                  params, uploads, calls, link, embed, text, desc):
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, **params)
    assert uploader.uploads == uploads
    assert len(vision.refs) == calls
    assert ("https://cdn.example/" in result.markdown) == link
    assert ("data:image" in result.markdown) == embed
    assert ("转写内容" in result.markdown) == text
    assert ("一句话描述" in result.markdown) == desc
    # 图片块计数：link / embed 呈现计 1；drop（含任务文本独立成段）计 0
    has_presentation = params.get("images") is not None
    assert result.image_count == (1 if has_presentation else 0)
    # images 元数据：字段反映管线结果（texts 按任务名落值）
    if has_presentation:
        (info,) = result.images
        assert info.sha256 and len(info.sha256) == 64
        assert bool(info.url) == (params.get("images") == "link")


def test_bare_command_image_file_gets_full_transcription(
        monkeypatch, tmp_path, probe_png):
    """类型即契约的活样本：图片文件裸命令 → 完整转写（注入 transcribe），
    产出是纯文本（无图片块）"""
    result, uploader, vision = _convert_with_stubs(monkeypatch, tmp_path, probe_png)
    assert len(vision.refs) == 1
    assert vision.requests[0].system            # 生效 system（内置母本）非空
    assert result.markdown.strip() == "转写内容"
    assert result.image_count == 0
    assert uploader.uploads == 0


def test_text_file_bare_command_never_injects(monkeypatch, tmp_path):
    """文字类裸命令绝不注入：零上传零视觉调用，产出即正文"""
    txt = tmp_path / "note.txt"
    txt.write_text("纯正文内容", encoding="utf-8")
    result, uploader, vision = _convert_with_stubs(monkeypatch, tmp_path, txt)
    assert "纯正文内容" in result.markdown
    assert uploader.uploads == 0 and len(vision.refs) == 0
    assert result.image_count == 0


def test_text_file_with_link_never_constructs_vision(monkeypatch, tmp_path):
    """文字文件 + link 呈现且无任务动词：不构造视觉模型（openai SDK 不加载）。
    图片文件不存在此组合——类型路由会注入转写"""
    def fail(self):
        raise AssertionError("无图片任务的转换不应构造视觉模型")
    txt = tmp_path / "note.txt"
    txt.write_text("内容", encoding="utf-8")
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = StubUploader()
    monkeypatch.setattr(type(conv), "vision", property(fail))
    result = conv.convert(txt, images="link")
    assert result.image_count == 0


def test_same_task_cache_shared_across_presentations(monkeypatch, tmp_path, probe_png):
    """取图方式不进缓存键（行为级）：同图同任务，embed 与 link 两次转换只调一次模型"""
    calls = []

    class CountVision:
        def run(self, request, ref):
            calls.append(ref)
            return CompletionResult(text="转写内容", usage=None)

    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = StubUploader()
    monkeypatch.setattr(type(conv), "vision", property(lambda self: CountVision()))
    conv.convert(probe_png, images="embed", transcribe=True)
    conv.convert(probe_png, images="link", transcribe=True)
    assert len(calls) == 1          # 第二次（URL 传图）命中第一次（base64 传图）的缓存


def test_upload_failure_skips_vlm_call(monkeypatch, tmp_path, probe_png):
    """管线不变量：URL 传图时上传失败的图跳过视觉调用（无可用 ref），
    该图走失败渲染（整图块删除），其余行为不受影响"""
    class FailUploader:
        def upload_image(self, data, filename):
            raise RuntimeError("upload exploded")

    vision = StubVision()
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = FailUploader()
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(probe_png, images="link", transcribe=True)
    assert len(vision.refs) == 0                   # 上传失败 → 零视觉调用
    assert result.image_count == 0                 # 整图块删除
    assert "（图片转写失败）" not in result.markdown  # drop 后无载体，任务产物随图丢弃
    assert result.markdown.strip() == ""           # 单图文件的产出为空正文


def test_pure_parse_zero_credentials_end_to_end(tmp_path):
    """文字类裸命令零凭据端到端：无任何 VLM/存储配置（空串凭据）构造成功并完成转换"""
    from omnimd.config import Config
    txt = tmp_path / "note.txt"
    txt.write_text("纯正文内容", encoding="utf-8")
    config = Config(
        vlm_api_key="", vlm_base_url="", vlm_model="",
        s3_endpoint_url="", s3_region="",
        s3_bucket="", s3_access_key_id="", s3_secret_access_key="",
        s3_key_prefix="", cache_dir=tmp_path,
    )
    config.validate()                 # 纯解析：构造期校验通过（凭据惰性）
    result = Converter(config, build_registry()).convert(txt)
    assert "纯正文内容" in result.markdown
    assert result.image_count == 0


def test_embed_vlm_ref_is_data_uri(monkeypatch, tmp_path, probe_png):
    """非 link 呈现：视觉模型收到 data URI（推导），产出渲染内嵌或独立文本"""
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, images="embed", transcribe=True)
    assert vision.refs[0].startswith("data:image/png;base64,")
    assert uploader.uploads == 0
    assert result.markdown.count("data:image/png;base64,") >= 1


def test_link_vlm_ref_is_public_url(monkeypatch, tmp_path, probe_png):
    """link 呈现：视觉模型收到公开 URL（推导），产出渲染公开链接"""
    result, uploader, vision = _convert_with_stubs(
        monkeypatch, tmp_path, probe_png, images="link", transcribe=True)
    assert vision.refs[0].startswith("https://cdn.example/")
    assert "data:image" not in result.markdown


def test_usage_accumulates_into_result(monkeypatch, tmp_path, probe_png):
    """token 用量透传：任务返回 usage → ConversionResult 汇总（成本可见）"""
    usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    uploader, vision = StubUploader(), StubVision(usage=usage)
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = uploader
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(probe_png, transcribe=True)
    assert result.usage is not None
    assert result.usage.total_tokens == 150
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 50


def test_usage_is_per_conversion_not_cumulative(monkeypatch, tmp_path, probe_png):
    """usage 是本次转换的增量，不随实例累计漂移——同一实例第二次转换
    仍是自己的 150，不是 300（共享实例的服务方依赖此语义）"""
    usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = StubUploader()
    monkeypatch.setattr(type(conv), "vision", property(lambda self: StubVision(usage=usage)))
    first = conv.convert(probe_png, transcribe=True)
    second = conv.convert(probe_png, transcribe=True)   # 缓存命中路径 usage 为增量 0
    assert first.usage is not None and first.usage.total_tokens == 150
    # 第二次命中缓存（零调用）→ 本次增量为 0 → usage 为 None（而非累计 300）
    assert second.usage is None


def test_no_usage_yields_none(monkeypatch, tmp_path, tmp_path_factory, probe_png):
    """端点未返回用量（或无调用）：ConversionResult.usage 为 None"""
    uploader, vision = StubUploader(), StubVision(usage=None)
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    conv._uploader = uploader
    monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
    result = conv.convert(probe_png, transcribe=True)
    assert result.usage is None


def test_empty_result_not_cached_allows_retry(monkeypatch, tmp_path, probe_png):
    """空产物：空串即失败产物，不缓存以免固化失败；
    非空结果正常缓存，重跑不再调用"""
    class EmptyVision:
        def __init__(self):
            self.calls = 0

        def run(self, request, ref):
            self.calls += 1
            return CompletionResult(text="", usage=None)

    def run_with(vision):
        conv = Converter(make_config(cache_dir=tmp_path), build_registry())
        conv._uploader = StubUploader()
        monkeypatch.setattr(type(conv), "vision", property(lambda self: vision))
        return conv.convert(probe_png, images="link",
                            transcribe=True, describe=True)

    empty = EmptyVision()
    first = run_with(empty)
    run_with(empty)
    assert empty.calls == 4          # 空结果两次都真实调用：失败未被固化
    assert "（图片转写失败）" in first.markdown   # 转写失败占位真实落进产出
    assert "![图片]" in first.markdown            # describe 失败回落「图片」二字

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
    cfg = make_config(s3_endpoint_url="",
                      s3_region="", s3_bucket="", s3_access_key_id="",
                      s3_secret_access_key="")
    with _pytest.raises(ConfigError) as e:
        create_uploader(cfg)
    for name in ("S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        assert name in str(e.value)
