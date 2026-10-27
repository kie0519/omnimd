"""库接口回归：OmniMD 构造（显式配置 / 环境变量覆盖链 / 类型校验 / 任务定义）、
convert_many 目录展开与异常透传（stub 转换器，不联网）"""
import pytest

from omnimd import OmniMD, ConversionResult, TaskPrompt, VlmConfig, S3Config, ConfigError
from tests.helpers import make_config


# ---------- 构造：显式配置覆盖链 ----------

def test_explicit_config_works_in_clean_env(monkeypatch, tmp_path):
    """干净环境（无 .env 变量）下显式 vlm + storage 构造成功"""
    for name in ("VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL",
                 "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    md = OmniMD(
        vlm=VlmConfig(api_key="k", base_url="https://x", model="m",
                      thinking="thinking-off"),
        storage=S3Config(endpoint="e", region="r", bucket="b",
                         access_key_id="a", secret_access_key="s"),
        cache_dir=tmp_path,
    )
    assert md._converter.config.vlm_thinking == "thinking-off"


def test_partial_override_falls_back_to_env(full_env, monkeypatch, tmp_path):
    """只给 vlm 显式对象：storage 维度回落环境值"""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    md = OmniMD(
        vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
        cache_dir=tmp_path,
    )
    config = md._converter.config
    assert config.vlm_base_url == "https://x"      # 显式对象生效
    assert config.s3_endpoint_url == "https://s3.example"  # 回落环境值（与显式对象生效对照）


def test_vlmconfig_optional_fields_fall_back_to_env(full_env, monkeypatch, tmp_path):
    """VlmConfig 可选字段不给（None）时回落环境值——对象默认值不覆盖 env"""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VLM_MAX_TOKENS", "16000")
    monkeypatch.setenv("VLM_CONCURRENCY", "3")
    monkeypatch.setenv("VLM_THINKING", "thinking-off")
    md = OmniMD(
        vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
        cache_dir=tmp_path,
    )
    config = md._converter.config
    assert config.vlm_max_tokens == 16000
    assert config.vlm_concurrency == 3
    assert config.vlm_thinking == "thinking-off"


def test_concurrency_kwarg_not_beaten_by_vlmconfig(monkeypatch, tmp_path):
    """构造参数 concurrency 优先于 VlmConfig（未显式给的）字段"""
    md = OmniMD(
        vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
        concurrency=16,
        cache_dir=tmp_path,
    )
    assert md._converter.config.vlm_concurrency == 16


def test_invalid_thinking_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        OmniMD(
            vlm=VlmConfig(api_key="k", base_url="https://u", model="m", thinking="nope"),
            storage=S3Config(endpoint="e", region="r", bucket="b",
                             access_key_id="a", secret_access_key="s"),
            cache_dir=tmp_path,
        )
    assert "nope" in str(e.value) and "none" in str(e.value)


def test_wrong_storage_type_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        OmniMD(
            vlm=VlmConfig(api_key="k", base_url="https://u", model="m"),
            storage="s3",   # 字符串不是配置对象
            cache_dir=tmp_path,
        )
    assert "S3Config" in str(e.value)


# ---------- 任务定义（TaskPrompt：随代码管理） ----------

def test_tasks_registered_from_constructor(monkeypatch, tmp_path):
    """构造期任务注册：tasks 定义进注册表，可被 prompt= 引用"""
    md = OmniMD(
        vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
        cache_dir=tmp_path,
        tasks=[TaskPrompt(name="walk", prompt="走查这个界面")],
    )
    registry = md._converter.registry
    assert "walk" in registry
    assert registry["walk"].prompt == "走查这个界面"
    assert registry["walk"].system == ""            # None = 通用骨架


def test_task_prompt_invalid_name_rejected(tmp_path):
    """TaskPrompt 命名非法：构造期报错（不等到使用时）"""
    with pytest.raises(ConfigError, match="任务名不合法"):
        OmniMD(
            vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
            cache_dir=tmp_path,
            tasks=[TaskPrompt(name="Walk", prompt="走查")],
        )


def test_task_prompt_empty_prompt_rejected(tmp_path):
    with pytest.raises(ConfigError, match="提示词为空"):
        OmniMD(
            vlm=VlmConfig(api_key="k", base_url="https://x", model="m"),
            cache_dir=tmp_path,
            tasks=[TaskPrompt(name="walk", prompt="  ")],
        )


# ---------- convert_many：目录展开、异常透传 ----------

def _fake_converter(fake_convert):
    md = OmniMD.__new__(OmniMD)
    md._converter = type("C", (), {"convert": staticmethod(fake_convert)})()
    return md


def test_convert_many_expands_directories(tmp_path, monkeypatch):
    """目录按名排序展开；全部文件出现在结果里"""
    d = tmp_path / "docs"
    d.mkdir()
    for name in ("b.txt", "a.txt", "c.txt"):
        (d / name).write_text("x", encoding="utf-8")

    calls = []

    def fake_convert(path, *, images=None, transcribe=False,
                     describe=False, prompt=None):
        calls.append(path.name)
        return ConversionResult(markdown=f"# {path.name}", images=[],
                                image_count=0, char_count=10, elapsed_ms=1)

    results = _fake_converter(fake_convert).convert_many([d])

    assert calls == ["a.txt", "b.txt", "c.txt"]    # 按名排序
    assert set(results) == {str(d / n) for n in ("a.txt", "b.txt", "c.txt")}
    assert all(isinstance(r, ConversionResult) for r in results.values())


def test_convert_many_isolates_failures(tmp_path):
    """单文件失败不中断整批，异常对象本身作为该文件的结果值"""
    (tmp_path / "good.txt").write_text("x", encoding="utf-8")
    (tmp_path / "bad.txt").write_text("x", encoding="utf-8")

    def fake_convert(path, *, images=None, transcribe=False,
                     describe=False, prompt=None):
        if path.name == "bad.txt":
            raise RuntimeError("解析故障")
        return ConversionResult(markdown="ok", images=[], image_count=0,
                                char_count=2, elapsed_ms=1)

    results = _fake_converter(fake_convert).convert_many(
        [tmp_path / "good.txt", tmp_path / "bad.txt"])

    assert isinstance(results[str(tmp_path / "good.txt")], ConversionResult)
    assert isinstance(results[str(tmp_path / "bad.txt")], RuntimeError)
    assert "解析故障" in str(results[str(tmp_path / "bad.txt")])


def test_convert_many_concurrent_matches_serial(monkeypatch, tmp_path):
    """max_workers=2 与串行结果一致（线程安全承诺的行为验证）"""
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")

    def fake_convert(path, *, images=None, transcribe=False,
                     describe=False, prompt=None):
        return ConversionResult(markdown=f"R:{path.name}", images=[],
                                image_count=0, char_count=3, elapsed_ms=1)

    md = _fake_converter(fake_convert)
    serial = md.convert_many([tmp_path], max_workers=1)
    concurrent = md.convert_many([tmp_path], max_workers=2)
    assert serial == concurrent
