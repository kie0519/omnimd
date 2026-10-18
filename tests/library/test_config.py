"""配置层回归：parse_output 取值域与 load 的校验分支（不联网，不触盘）"""
import pytest

from omnimd.config import load, parse_output
from omnimd.exceptions import ConfigError
from tests.helpers import REQUIRED_ENV

# ---------- parse_output：合法取值（含归一化） ----------
@pytest.mark.parametrize("raw,want", [
    (None, "body"),                     # 缺省纯解析
    ("", "body"),
    ("  ", "body"),
    ("body", "body"),
    ("url", "body,url"),                # body 锚点省略时自动包含
    ("url,ocr", "body,url,ocr"),
    ("ocr,url", "body,url,ocr"),        # 归一序
    (" url , ocr ", "body,url,ocr"),    # 容忍空白
    ("url,url", "body,url"),            # 去重
    ("url,ocr,url", "body,url,ocr"),    # 去重 + 归一序
    ("url,alt", "body,url,alt"),
    ("alt,url,ocr", "body,url,ocr,alt"),
    ("base64,ocr", "body,base64,ocr"),
    ("base64", "body,base64"),
    ("url,ocr,alt", "body,url,ocr,alt"),
    ("base64,ocr,alt", "body,base64,ocr,alt"),
])
def test_parse_output_valid(raw, want):
    assert parse_output(raw) == want


# ---------- parse_output：非法取值 fail-loud ----------
@pytest.mark.parametrize("raw", ["all", "both", "image", "URL", "url text", "desc", "text"])
def test_parse_output_rejects_unknown_value(raw):
    with pytest.raises(ConfigError) as e:
        parse_output(raw)
    assert "body, url, base64, ocr, alt" in str(e.value)   # 报错列出全部可选取值


@pytest.mark.parametrize("raw", ["url,,ocr", ",", " , "])
def test_parse_output_rejects_empty_piece(raw):
    with pytest.raises(ConfigError) as e:
        parse_output(raw)
    assert "空取值" in str(e.value)       # 连续逗号/纯空白给出针对性提示


def test_parse_output_url_base64_mutex():
    with pytest.raises(ConfigError) as e:
        parse_output("url,base64")
    assert "互斥" in str(e.value)


def test_parse_output_ocr_alt_require_presentation():
    with pytest.raises(ConfigError) as e:
        parse_output("ocr")
    assert "依赖" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_output("alt")
    assert "依赖" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_output("ocr,alt")
    assert "依赖" in str(e.value)


# ---------- load：校验分支 ----------

def test_load_missing_required_lazy(monkeypatch):
    """凭据按消费方惰性校验：纯解析零凭据可构造；首次转写（vision）时报缺"""
    for name in list(REQUIRED_ENV) + [
        "S3_KEY_PREFIX",
        "OUTPUT", "DOC_ENGINE", "CACHE_DIR",
        "VLM_MAX_TOKENS", "VLM_CONCURRENCY",
    ]:
        monkeypatch.delenv(name, raising=False)
    config = load()                      # 纯解析：构造成功
    assert config.output == "body"

    from omnimd.converter import Converter
    with pytest.raises(ConfigError) as e:
        Converter(config).vision         # 首次需要视觉模型时报缺
    assert "缺少凭据" in str(e.value)
    for name in ("VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL"):
        assert name in str(e.value)      # 全部缺失项一次列全


def test_vlm_thinking_defaults_to_none(monkeypatch, tmp_path):
    """VLM_THINKING 未设置时缺省 none（不发思考参数，纯 OpenAI 格式）"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    config = load()
    assert config.vlm_thinking == "none"


def test_vlm_thinking_rejects_unknown(monkeypatch, tmp_path):
    with pytest.raises(ConfigError) as e:
        for name, value in REQUIRED_ENV.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("VLM_THINKING", "disabled")
        monkeypatch.setenv("CACHE_DIR", str(tmp_path))
        load()
    assert "VLM_THINKING" in str(e.value)
    assert "none" in str(e.value)                       # 报错列出全部取值


def test_storage_is_on_demand(full_env, monkeypatch, tmp_path):
    """存储按需必填：产出不含 url 部件（base64 内嵌）可不配存储运行。

    VLM 凭据三项齐全 + 无任何存储变量 → 构造成功（存储客户端延迟到
    产出含 url 部件时才构造），此时要求 url 产出则报错并提示配置方法。
    """
    monkeypatch.setenv("OUTPUT", "body,base64,ocr")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    for name in ("STORAGE_PROVIDER", "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)

    from omnimd.converter import Converter
    config = load()
    assert config.output == "body,base64,ocr"
    converter = Converter(config)
    assert converter._uploader is None            # 构造期不触存储

    with pytest.raises(ConfigError) as e:
        converter.uploader                         # 要求 url 产出时才报
    assert "STORAGE_PROVIDER" in str(e.value)
    assert "base64" in str(e.value)               # 报错提示如何配置


def test_load_invalid_numeric_env(full_env, monkeypatch):
    """非法整数变量报 ConfigError（变量名 + 当前值），不是裸 ValueError"""
    monkeypatch.setenv("VLM_CONCURRENCY", "eight")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_CONCURRENCY" in str(e.value) and "eight" in str(e.value)


def test_load_concurrency_must_be_positive(full_env, monkeypatch):
    monkeypatch.setenv("VLM_CONCURRENCY", "0")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_CONCURRENCY" in str(e.value)


def test_load_invalid_output(full_env):
    with pytest.raises(ConfigError):
        load(output="all")


def test_load_output_override(full_env):
    assert load(output="body,url,ocr").output == "body,url,ocr"


def test_load_output_defaults_to_body(full_env):
    assert load().output == "body"


def test_load_invalid_doc_engine(full_env, monkeypatch):
    monkeypatch.setenv("DOC_ENGINE", "nope")
    with pytest.raises(ConfigError) as e:
        load()
    assert "markitdown" in str(e.value) and "anydoc" in str(e.value)


def test_rejects_bad_base_url(monkeypatch, tmp_path):
    """VLM_BASE_URL 缺协议头在首次转写（vision）时报错——连接面随凭据惰性校验"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VLM_BASE_URL", "api.example.com/v1")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    from omnimd.converter import Converter
    with pytest.raises(ConfigError) as e:
        Converter(load()).vision
    assert "VLM_BASE_URL" in str(e.value)


def test_rejects_bad_max_tokens(monkeypatch, tmp_path):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VLM_MAX_TOKENS", "0")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_MAX_TOKENS" in str(e.value)


def test_rejects_unknown_storage_provider(monkeypatch, tmp_path):
    """STORAGE_PROVIDER 拼写错误在构造期报错（不等首次上传）"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("STORAGE_PROVIDER", "S3")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    with pytest.raises(ConfigError) as e:
        load()
    assert "STORAGE_PROVIDER" in str(e.value)
