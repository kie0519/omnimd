"""任务注册表回归：TaskSpec 校验、三层并入、文件发现（frontmatter 子集）、digest（不联网）"""
import pytest

from omnimd.exceptions import ConfigError
from omnimd.prompts import (
    GENERAL_SYSTEM_PROMPT,
    TaskSpec,
    build_registry,
    builtin_specs,
    effective_system,
    failure_note,
    parse_task_file,
    task_digest,
    validate_name,
)


def spec(name="walk", prompt="做点什么", system=""):
    return TaskSpec(name=name, prompt=prompt, system=system)


# ---------- 内置任务 ----------

def test_builtin_specs_are_universal_verbs():
    """内置任务 = 普适动词集（transcribe / describe），场景化任务不进内置"""
    names = [s.name for s in builtin_specs()]
    assert set(names) == {"transcribe", "describe"}
    registry = build_registry()
    assert registry.names() == ["describe", "transcribe"]   # names() 排序返回


def test_describe_spec_shape():
    """describe：attribute 渲染（填链接描述位）+ 独立输出上限 512"""
    registry = build_registry()
    d = registry["describe"]
    assert d.render == "attribute"
    assert d.max_tokens == 512


def test_transcribe_spec_shape():
    """transcribe：block 渲染 + 跟配置的上限（None）"""
    t = build_registry()["transcribe"]
    assert t.render == "block"
    assert t.max_tokens is None
    assert "转写" in t.prompt                        # 提示词母本非空


# ---------- 注册校验 ----------

@pytest.mark.parametrize("bad", ["Walk", "1x", "UPPER", "has space", "", "中文", "a:b"])
def test_validate_name_rejects_illegal(bad):
    with pytest.raises(ConfigError) as e:
        validate_name(bad, "测试")
    assert "任务名不合法" in str(e.value)


@pytest.mark.parametrize("good", ["w", "walk", "ui-check", "chart_2", "a-b_c9"])
def test_validate_name_accepts_legal(good):
    validate_name(good, "测试")   # 不抛即通过


def test_registry_add_rejects_empty_prompt():
    with pytest.raises(ConfigError, match="提示词为空"):
        build_registry(explicit=[spec(prompt="   ")])


def test_registry_add_rejects_bad_render():
    with pytest.raises(ConfigError, match="render"):
        build_registry(explicit=[TaskSpec(name="x", prompt="p", render="inline")])


# ---------- 三层并入与同名覆盖 ----------

def test_explicit_overrides_builtin():
    """显式定义覆盖内置——提示词就是任务的本体"""
    custom = TaskSpec(name="transcribe", prompt="我的转写法")
    registry = build_registry(explicit=[custom])
    assert registry["transcribe"].prompt == "我的转写法"


def test_workdir_discovery_and_priority(tmp_path):
    """工作目录 prompts/ 自动发现；显式定义覆盖同名文件（显式在后）"""
    task_dir = tmp_path / "prompts"
    task_dir.mkdir()
    (task_dir / "walk.md").write_text("走查这个界面", encoding="utf-8")
    (task_dir / "notes.txt").write_text("非 md 忽略", encoding="utf-8")
    registry = build_registry(workdir=tmp_path)
    assert "walk" in registry
    assert registry["walk"].prompt == "走查这个界面"
    # 显式覆盖文件
    explicit = build_registry(
        explicit=[spec(name="walk", prompt="显式版")], workdir=tmp_path)
    assert explicit["walk"].prompt == "显式版"


def test_workdir_missing_dir_is_fine(tmp_path):
    """prompts/ 目录不存在：只有内置任务，不报错"""
    registry = build_registry(workdir=tmp_path)
    assert set(registry.names()) == {"transcribe", "describe"}


# ---------- 任务文件解析（frontmatter 子集） ----------

def test_parse_task_file_full(tmp_path):
    path = tmp_path / "audit.md"
    path.write_text(
        "---\n"
        "name: audit\n"
        "system: |\n"
        "  你是审计专家。\n"
        "  只看可见内容。\n"
        "---\n"
        "检查这个界面的合规问题\n",
        encoding="utf-8",
    )
    spec_ = parse_task_file(path)
    assert spec_.name == "audit"
    assert spec_.system == "你是审计专家。\n只看可见内容。"
    assert spec_.prompt == "检查这个界面的合规问题"


def test_parse_task_file_defaults(tmp_path):
    """缺省链：name ← 文件名、system ← 空串（= 通用骨架）、正文必填"""
    path = tmp_path / "walk.md"
    path.write_text("走查这个界面", encoding="utf-8")
    spec_ = parse_task_file(path)
    assert spec_.name == "walk"
    assert spec_.system == ""
    assert effective_system(spec_) == GENERAL_SYSTEM_PROMPT   # 空串落到骨架


def test_parse_task_file_empty_body_rejected(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("---\nname: empty\n---\n\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="正文为空"):
        parse_task_file(path)


def test_parse_task_file_unclosed_frontmatter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("---\nname: bad\n走查", encoding="utf-8")
    with pytest.raises(ConfigError, match="未闭合"):
        parse_task_file(path)


def test_parse_task_file_unknown_key_rejected(tmp_path):
    path = tmp_path / "k.md"
    path.write_text("---\nname: k\ntemperature: 0.2\n---\n正文", encoding="utf-8")
    with pytest.raises(ConfigError, match="不支持键"):
        parse_task_file(path)


def test_parse_task_file_illegal_name_rejected(tmp_path):
    path = tmp_path / "MyTask.md"
    path.write_text("正文", encoding="utf-8")
    with pytest.raises(ConfigError, match="任务名不合法"):
        parse_task_file(path)


# ---------- digest 与失败文案 ----------

def test_digest_changes_with_prompt_and_skeleton():
    """digest 基于生效提示词：改 prompt、改 system、改骨架（system 空）都变"""
    a = spec(prompt="A")
    b = spec(prompt="B")
    assert task_digest(a) != task_digest(b)
    c = spec(prompt="A", system="自定义骨架")
    assert task_digest(a) != task_digest(c)          # 自定义 system 改变生效内容


def test_digest_stable_for_same_content():
    assert task_digest(spec(prompt="A")) == task_digest(spec(prompt="A"))


def test_failure_note_transcribe_is_byte_compatible():
    """内置 transcribe 的失败占位逐字节锁定（产出兼容契约——下游按此识别失败标记）"""
    assert failure_note(build_registry()["transcribe"]) == "（图片转写失败）"


def test_failure_note_custom_task_names_task():
    assert failure_note(spec(name="walk", prompt="p")) == "（图片任务 walk 失败）"
