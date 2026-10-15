"""CLI 回归：参数解析、退出码、产出落盘（哑配置 + 文本文件，全程不联网）"""
import pytest

from omnimd.cli import main
from conftest import REQUIRED_ENV


@pytest.fixture()
def env_and_input(monkeypatch, tmp_path):
    """齐全的哑配置 + 一个待转换文本文件。

    文本文件无图片——不产生上传与模型调用，全程不联网；
    S3 用哑端点构造 boto3 客户端（构造期无网络请求）。
    """
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "cli.md.txt"
    src.write_text("# CLI 回归\n\n正文。\n", encoding="utf-8")
    yield src, tmp_path / "out"


def test_convert_writes_markdown(env_and_input, capsys):
    src, out = env_and_input
    code = main([str(src), "-o", str(out)])
    assert code == 0
    produced = out / (src.stem + ".md")
    assert "CLI 回归" in produced.read_text(encoding="utf-8")


def test_output_flag_accepted_and_writes(env_and_input):
    """--output flag 可解析、可落盘（图片块形态差异由 test_pipeline 矩阵覆盖）"""
    src, out = env_and_input
    assert main([str(src), "-o", str(out), "--output", "url"]) == 0
    assert (out / (src.stem + ".md")).exists()


def test_invalid_output_exits_two(env_and_input, capsys):
    src, out = env_and_input
    assert main([str(src), "-o", str(out), "--output", "all"]) == 2
    assert "配置错误" in capsys.readouterr().err


def test_missing_path_exits_two(env_and_input, capsys):
    src, out = env_and_input
    assert main([str(src.parent / "nope.pdf"), "-o", str(out)]) == 2
    assert "路径不存在" in capsys.readouterr().err


def test_unsupported_format_exits_two(env_and_input, capsys):
    src, out = env_and_input
    bad = src.parent / "bad.xyz"
    bad.write_text("x", encoding="utf-8")
    assert main([str(bad), "-o", str(out)]) == 2
    assert ".xyz" in capsys.readouterr().err


def test_empty_dir_exits_two(env_and_input, capsys):
    src, out = env_and_input
    empty_dir = src.parent / "empty"
    empty_dir.mkdir()
    assert main([str(empty_dir), "-o", str(out)]) == 2
    assert "没有找到可转换的文件" in capsys.readouterr().err


def test_directory_expansion_sorted(env_and_input):
    """目录展开按名排序，多个文件全部转换"""
    src, out = env_and_input
    d = src.parent / "batch"
    d.mkdir()
    for name in ("b.txt", "a.txt", "c.txt"):
        (d / name).write_text(f"# {name}\n", encoding="utf-8")
    assert main([str(d), "-o", str(out)]) == 0
    for name in ("a", "b", "c"):
        assert (out / f"{name}.md").exists()


def test_same_stem_collision_gets_unique_suffix(env_and_input):
    """同批 dup.txt 与 dup.md 撞名：后者追加序号（dup-1.md），互不覆盖"""
    src, out = env_and_input
    first = src.parent / "dup.txt"
    first.write_text("第一个\n", encoding="utf-8")
    second = src.parent / "dup.md"
    second.write_text("# 第二个\n\n Markdown 源文件直转\n", encoding="utf-8")
    assert main([str(first), str(second), "-o", str(out)]) == 0
    assert "第一个" in (out / "dup.md").read_text(encoding="utf-8")
    assert "第二个" in (out / "dup-1.md").read_text(encoding="utf-8")


def test_same_input_in_batch_reuses_target(env_and_input):
    """同一输入在同批重复出现：复用首次分配的产出名，覆盖时不产生 -1 序号副本"""
    src, out = env_and_input
    src.write_text("只此一份\n", encoding="utf-8")
    assert main([str(src), str(src), "-o", str(out)]) == 0
    produced = sorted(p.name for p in out.iterdir())
    assert produced == ["cli.md.md"]              # 只有一个产出（stem + .md）
    assert all("-1" not in p for p in produced)
