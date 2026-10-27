"""CLI 回归：参数解析、退出码、产出落盘（哑配置 + 文本文件，全程不联网）"""
import pytest

from omnimd.cli import main
from tests.helpers import REQUIRED_ENV


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


def test_bare_command_text_file_is_pure_parse(env_and_input):
    """文字类裸命令 = 仅正文（类型即契约；零上传零模型调用全程不联网）"""
    src, out = env_and_input
    assert main([str(src), "-o", str(out)]) == 0
    assert "正文" in (out / (src.stem + ".md")).read_text(encoding="utf-8")


def test_verb_flags_parse_and_run(env_and_input):
    """动词参数组可解析：呈现 + 任务在文字文件上正常运行（无图即无调用）"""
    src, out = env_and_input
    code = main([str(src), "-o", str(out),
                 "--images", "link", "--transcribe",
                 "--prompt", "重点看配色", "--prompt", "再看间距"])
    assert code == 0
    assert (out / (src.stem + ".md")).exists()


def test_describe_without_images_exits_two(env_and_input, capsys):
    """--describe 无 --images：编译期前置校验报错（描述位需要链接载体），且不产生目录副作用"""
    src, out = env_and_input
    assert main([str(src), "-o", str(out), "--describe"]) == 2
    err = capsys.readouterr().err
    assert "--images link" in err and "--images embed" in err
    assert not out.exists()          # 参数报错在目录创建之前——零目录副作用


def test_bad_task_file_reports_param_error(tmp_path, monkeypatch, capsys):
    """工作目录 prompts/ 的坏任务文件（正文为空）：走参数错误路径（退出码 2），
    不是裸 traceback——与编译层校验同一报错形态"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "bad.md").write_text(
        "---\nname: bad\n---\n", encoding="utf-8")
    src = tmp_path / "x.txt"
    src.write_text("正文", encoding="utf-8")
    assert main([str(src), "-o", str(tmp_path / "out")]) == 2
    assert "参数错误" in capsys.readouterr().err


def test_out_dir_unwritable_exits_two(tmp_path, monkeypatch, capsys):
    """输出目录不可写：硬失败（退出码 2，产出无处落盘）——与缓存不可写的
    警告继续形成对照"""
    from pathlib import Path
    src = tmp_path / "a.txt"
    src.write_text("正文", encoding="utf-8")

    def deny(self, *a, **k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", deny)
    assert main([str(src), "-o", str(tmp_path / "out")]) == 2
    assert "输出目录创建失败" in capsys.readouterr().err


def test_unknown_images_choice_rejected_by_argparse(env_and_input, capsys):
    """--images 取值域由 argparse 收口（link / embed 之外直接拒绝）"""
    src, out = env_and_input
    with pytest.raises(SystemExit) as e:
        main([str(src), "-o", str(out), "--images", "url"])
    assert e.value.code == 2


# ---------- 通用路径行为 ----------

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


def test_version_flag_prints_and_exits(capsys):
    """--version：打印版本并退出 0（issue 模板的版本获取入口）"""
    from omnimd import __version__
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out
