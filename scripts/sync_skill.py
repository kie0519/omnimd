#!/usr/bin/env python3
"""sync_skill — 母本 skills/omnimd/ → 部署副本的一键同步。

流程：frontmatter 校验 → .env.example 对齐断言 → 复制 → 逐字节 diff 断言 → 冒烟自检（omnimd CLI 可用）。
运行时产物（.env、__pycache__/）不参与同步、不删除。

skill 是纯知识（SKILL.md 指令 + .env.example 模板），同步即整体复制。

仓库的 skills/ 即部署源容器（与生态惯例同构：skills/<name>/SKILL.md），也可手动 cp -r skills/omnimd <目标>/.claude/skills/ 部署。

用法（仓库根执行）：
    python scripts/sync_skill.py [部署目标目录]
    # 默认 <仓库上级>/.claude/skills/omnimd
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "omnimd"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
DEFAULT_TARGET = ROOT.parent / ".claude" / "skills" / SKILL_NAME

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _is_runtime_artifact(path: Path) -> bool:
    """运行时产物：__pycache__/ 目录、*.pyc、.env——不部署、不比对、不告警。"""
    return "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == ".env"



def validate_skill() -> None:
    """SKILL.md frontmatter 门禁（name/description 契约，对齐官方规范）。"""
    skill_md = SKILL_DIR / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        raise SystemExit("SKILL.md 缺少 YAML frontmatter")
    import yaml
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        raise SystemExit("frontmatter 必须是 YAML 字典")
    unexpected = set(frontmatter) - {"name", "description"}
    if unexpected:
        raise SystemExit(f"frontmatter 含非法字段： {sorted(unexpected)}"
                         "（只允许 name/description）")
    name = frontmatter.get("name", "")
    if name != SKILL_NAME:
        raise SystemExit(f"frontmatter name（{name}）须与部署目录名 {SKILL_NAME} 一致")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise SystemExit(f"name 须为 hyphen-case： {name}")
    desc = frontmatter.get("description", "")
    if not desc or len(desc) > 1024 or "<" in desc or ">" in desc:
        raise SystemExit("description 须非空、≤1024 字符、不含尖括号")
    print(f"✓ SKILL.md 校验通过（name={name}）")


def assert_env_example_aligned() -> None:
    """skill 模板与仓库根模板逐字节一致（配置模板定义在仓库根）。"""
    root_tpl = ROOT / ".env.example"
    skill_tpl = SKILL_DIR / ".env.example"
    for tpl in (root_tpl, skill_tpl):
        if not tpl.is_file():
            raise SystemExit(f"缺少模板文件： {tpl}——请先在仓库根创建 .env.example")
    if root_tpl.read_bytes() != skill_tpl.read_bytes():
        raise SystemExit("skills/omnimd/.env.example 与仓库根 .env.example 不一致——"
                         "请以仓库根为源整体复制。")
    print("✓ .env.example 对齐断言通过")


def deploy(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for src in sorted(SKILL_DIR.rglob("*")):
        if not src.is_file() or _is_runtime_artifact(src):
            continue
        dst = target / src.relative_to(SKILL_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"✓ 已复制 skills/{SKILL_NAME}/ → {target}")


def assert_identical(target: Path) -> None:
    mismatches = [str(p.relative_to(SKILL_DIR)) for p in sorted(SKILL_DIR.rglob("*"))
                  if p.is_file() and not _is_runtime_artifact(p)
                  and (not (target / p.relative_to(SKILL_DIR)).exists()
                       or p.read_bytes() != (target / p.relative_to(SKILL_DIR)).read_bytes())]
    if mismatches:
        raise SystemExit(f"diff 断言失败（{len(mismatches)} 处不一致）： {mismatches}")
    print(f"✓ diff 断言通过：{target} 与母本逐字节一致")


def smoke() -> None:
    """skill 依赖的 omnimd CLI 在当前 Python 环境可用（skill 是纯知识，
    执行依赖 pip 安装的命令行入口）。"""
    proc = subprocess.run([sys.executable, "-X", "utf8", "-m", "omnimd.cli", "--help"],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or "omnimd" not in (proc.stdout + proc.stderr):
        print(proc.stdout, proc.stderr, sep="\n", file=sys.stderr)
        raise SystemExit("冒烟自检失败：omnimd CLI 不可用（pip install omnimd）")
    print("✓ 冒烟自检通过：omnimd CLI 可用")


def main() -> int:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_TARGET
    validate_skill()
    assert_env_example_aligned()
    deploy(target)
    assert_identical(target)
    smoke()
    extras = [str(p.relative_to(target)) for p in target.rglob("*")
              if p.is_file() and not _is_runtime_artifact(p)
              and not (SKILL_DIR / p.relative_to(target)).exists()]
    if extras:
        print(f"注意：目标目录存在母本外文件（确认是否过期残留）： {extras}")
    print(f"同步完成 → {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
