"""配置层回归：load 的校验分支与变量解析（不联网，不触盘）"""
import pytest

from omnimd.config import load
from omnimd.exceptions import ConfigError
from tests.helpers import REQUIRED_ENV


def _clear_env(monkeypatch):
    """清空全部相关变量（凭据惰性测试从零开始）"""
    for name in list(REQUIRED_ENV) + [
        "S3_KEY_PREFIX", "DOC_ENGINE", "CACHE_DIR",
        "VLM_MAX_TOKENS", "VLM_CONCURRENCY", "VLM_TIMEOUT", "VLM_THINKING",
    ]:
        monkeypatch.delenv(name, raising=False)


# ---------- VLM_TIMEOUT：解析与校验 ----------

def test_vlm_timeout_defaults_to_300(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    assert load().vlm_timeout == 300.0


def test_vlm_timeout_env_override(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VLM_TIMEOUT", "120")
    assert load().vlm_timeout == 120.0


def test_vlm_timeout_rejects_non_numeric(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VLM_TIMEOUT", "2min")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_TIMEOUT" in str(e.value) and "2min" in str(e.value)


def test_vlm_timeout_rejects_non_positive(full_env, monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VLM_TIMEOUT", "0")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_TIMEOUT" in str(e.value)


# ---------- 凭据与连接的惰性校验 ----------

def test_load_missing_required_lazy(monkeypatch):
    """凭据按消费方惰性校验：纯解析零凭据可构造；首次视觉调用时报缺"""
    _clear_env(monkeypatch)
    config = load()                      # 纯解析：构造成功

    from omnimd.converter import Converter
    from omnimd.prompts import build_registry
    with pytest.raises(ConfigError) as e:
        Converter(config, build_registry()).vision   # 首次需要视觉模型时报缺
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
    """存储按需必填：呈现非 link（drop / embed）可不配存储运行。

    VLM 凭据三项齐全 + 无任何存储变量 → 构造成功（存储客户端延迟到 link 呈现时才构造），此时要求上传则报错并提示配置方法。
    """
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    for name in ("S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)

    from omnimd.converter import Converter
    from omnimd.prompts import build_registry
    config = load()
    converter = Converter(config, build_registry())
    assert converter._uploader is None            # 构造期不触存储

    with pytest.raises(ConfigError) as e:
        converter.uploader                         # link 呈现要求上传时才报
    assert "S3_ENDPOINT_URL" in str(e.value)
    assert "--images embed" in str(e.value)       # 报错提示替代出路


def test_load_invalid_numeric_env(full_env, monkeypatch):
    """非法整数变量报 ConfigError，不是裸 ValueError"""
    monkeypatch.setenv("VLM_CONCURRENCY", "eight")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_CONCURRENCY" in str(e.value) and "eight" in str(e.value)


def test_load_concurrency_must_be_positive(full_env, monkeypatch):
    monkeypatch.setenv("VLM_CONCURRENCY", "0")
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_CONCURRENCY" in str(e.value)


def test_load_invalid_doc_engine(full_env, monkeypatch):
    monkeypatch.setenv("DOC_ENGINE", "nope")
    with pytest.raises(ConfigError) as e:
        load()
    assert "markitdown" in str(e.value) and "anydoc" in str(e.value)


def test_rejects_bad_base_url(monkeypatch, tmp_path):
    """VLM_BASE_URL 缺协议头在首次视觉调用（vision）时报错——连接面随凭据惰性校验"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VLM_BASE_URL", "api.example.com/v1")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    from omnimd.converter import Converter
    from omnimd.prompts import build_registry
    with pytest.raises(ConfigError) as e:
        Converter(load(), build_registry()).vision
    assert "VLM_BASE_URL" in str(e.value)


def test_rejects_bad_max_tokens(monkeypatch, tmp_path):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VLM_MAX_TOKENS", "0")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    with pytest.raises(ConfigError) as e:
        load()
    assert "VLM_MAX_TOKENS" in str(e.value)


def test_storage_partial_config_lists_missing(full_env, monkeypatch, tmp_path):
    """配了 endpoint 但缺其余 S3_* 项：构造存储客户端时报全缺失清单"""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    for name in ("S3_REGION", "S3_BUCKET", "S3_ACCESS_KEY_ID",
                 "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    from omnimd.storage import create_uploader
    with pytest.raises(ConfigError) as e:
        create_uploader(load())
    for name in ("S3_REGION", "S3_BUCKET", "S3_ACCESS_KEY_ID",
                 "S3_SECRET_ACCESS_KEY"):
        assert name in str(e.value)


# ---------- CACHE_DIR 落点规则 ----------

def test_cache_dir_anchors_to_env_file_dir(monkeypatch, tmp_path):
    """相对 CACHE_DIR 锚定提供它的 .env 所在目录；进程注入锚工作目录；绝对路径原样"""
    from omnimd.config import _resolve_cache_dir
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("CACHE_DIR=my-cache\n", encoding="utf-8")
    files = (proj / ".env",)
    assert _resolve_cache_dir("my-cache", None, files) == proj / "my-cache"
    monkeypatch.chdir(tmp_path)   # 注入同值优先于文件：锚工作目录（优先序一致性）
    assert _resolve_cache_dir(
        "my-cache", "my-cache", files) == tmp_path / "my-cache"
    assert _resolve_cache_dir(
        str(tmp_path / "abs"), None, files) == tmp_path / "abs"


def test_cache_dir_unconfigured_uses_user_cache(monkeypatch, tmp_path):
    """查找链无 CACHE_DIR 时用系统用户缓存目录"""
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)                      # cwd 无 .env
    import omnimd.config as cfg
    monkeypatch.setattr(cfg, "_env_files", lambda: (tmp_path / "none.env",))
    assert cfg.load().cache_dir == cfg.default_cache_dir()


def test_cache_dir_rejects_existing_file(monkeypatch, tmp_path):
    """CACHE_DIR 指向已存在文件：构造期报错，附值与来源 .env 路径"""
    import os
    import omnimd.config as cfg
    monkeypatch.delenv("CACHE_DIR", raising=False)
    (tmp_path / "afile.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("CACHE_DIR=afile.txt\n", encoding="utf-8")
    # 显式接管查找链（autouse 夹具已把链置空）
    monkeypatch.setattr(cfg, "_env_files", lambda: (tmp_path / ".env",))
    try:
        with pytest.raises(ConfigError) as e:
            load()
        assert str(tmp_path / "afile.txt") in str(e.value)
        assert str(tmp_path / ".env") in str(e.value)   # 指出来源文件
    finally:
        os.environ.pop("CACHE_DIR", None)               # load_dotenv 不经 monkeypatch


def test_cache_dir_origin_three_branches(tmp_path):
    """报错指路三分支：显式参数 / 进程环境变量 / .env 文件路径"""
    from omnimd.config import _cache_origin
    f = tmp_path / ".env"
    f.write_text("CACHE_DIR=c\n", encoding="utf-8")
    assert _cache_origin("x", "c", None, (f,)) == "显式 cache_dir 参数"
    assert _cache_origin(None, "c", "c", (f,)) == "进程环境变量 CACHE_DIR"
    assert _cache_origin(None, "c", None, (f,)) == str(f)
