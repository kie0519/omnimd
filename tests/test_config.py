"""配置层回归：parse_output 取值域与 load 的校验分支（不联网，不触盘）"""
import pytest

from omnimd.config import load, parse_output
from omnimd.exceptions import ConfigError
from conftest import REQUIRED_ENV

# ---------- parse_output：合法取值（含归一化） ----------
@pytest.mark.parametrize("raw,want", [
    (None, "url,text"),
    ("", "url,text"),
    ("  ", "url,text"),
    ("url", "url"),
    ("text", "text"),
    ("url,text", "url,text"),
    ("text,url", "url,text"),
    (" url , text ", "url,text"),
    ("url,url", "url"),                 # 去重
    ("url,text,url", "url,text"),       # 去重 + 归一序
    ("url,alt", "url,alt"),
    ("alt,url,text", "url,text,alt"),   # alt 参与归一序
    ("url,text,alt", "url,text,alt"),
])
def test_parse_output_valid(raw, want):
    assert parse_output(raw) == want


# ---------- parse_output：非法取值 fail-loud ----------
@pytest.mark.parametrize("raw", ["all", "both", "image", "URL", "url text", "desc"])
def test_parse_output_rejects_unknown_value(raw):
    with pytest.raises(ConfigError) as e:
        parse_output(raw)
    assert "url, text, alt" in str(e.value)   # 报错列出全部可选取值


@pytest.mark.parametrize("raw", ["url,,text", ",", " , "])
def test_parse_output_rejects_empty_piece(raw):
    with pytest.raises(ConfigError) as e:
        parse_output(raw)
    assert "空取值" in str(e.value)       # 连续逗号/纯空白给出针对性提示


def test_parse_output_alt_requires_url():
    with pytest.raises(ConfigError) as e:
        parse_output("alt")
    assert "依赖 url" in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_output("text,alt")
    assert "依赖 url" in str(e.value)


# ---------- load：校验分支 ----------

def test_load_missing_required(monkeypatch):
    for name in list(REQUIRED_ENV) + [
        "S3_KEY_PREFIX",
        "OUTPUT", "DOC_ENGINE", "CACHE_DIR",
        "VLM_MAX_TOKENS", "VLM_CONCURRENCY", "VLM_IMAGE_INPUT",
    ]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError) as e:
        load()
    assert "缺少凭据" in str(e.value)
    assert "VLM_API_KEY" in str(e.value)


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
    """存储按需必填：仅转写文本（output=text）可不配存储运行。

    VLM 凭据三项齐全 + 无任何存储变量 → 构造成功（存储客户端延迟到
    产出含 url 时才构造），此时要求 url 产出则报错并提示配置方法。
    """
    monkeypatch.setenv("OUTPUT", "text")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    for name in ("STORAGE_PROVIDER", "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)

    from omnimd.converter import Converter
    config = load()
    assert config.output == "text"
    converter = Converter(config)
    assert converter._uploader is None            # 构造期不触存储

    with pytest.raises(ConfigError) as e:
        converter.uploader                         # 要求 url 产出时才报
    assert "STORAGE_PROVIDER" in str(e.value)
    assert "text" in str(e.value)                  # 报错提示如何配置


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
    assert load(output="text").output == "text"


def test_load_invalid_doc_engine(full_env, monkeypatch):
    monkeypatch.setenv("DOC_ENGINE", "nope")
    with pytest.raises(ConfigError) as e:
        load()
    assert "markitdown" in str(e.value) and "anydoc" in str(e.value)


def test_load_invalid_image_input(full_env, monkeypatch):
    monkeypatch.setenv("VLM_IMAGE_INPUT", "websocket")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_IMAGE_INPUT" in str(e.value)


def test_rejects_bad_base_url(monkeypatch, tmp_path):
    """VLM_BASE_URL 缺协议头在构造期报错（不等到首次转写）"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VLM_BASE_URL", "api.example.com/v1")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    with pytest.raises(ConfigError) as e:
        load()
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
