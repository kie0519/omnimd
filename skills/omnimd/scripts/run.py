"""运行入口：加载 skill 目录 .env → 子进程调 omnimd CLI → 透传输出。

独立可迁移：整个 skill 目录复制到任意位置（项目 .claude/skills/、
用户 ~/.claude/skills/）均可用——omnimd 包由本目录 scripts/setup.py 安装进 Python
环境，本脚本只负责把 .env 密钥送进子进程环境并处理 Windows 管道编码。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _load_skill_dotenv() -> None:
    """加载 skill 目录的 .env（密钥就位处，若存在）。

    密钥进入本进程环境后由子进程继承；omnimd 的 config.py 按自己的
    规则查找（当前目录/包根），环境变量已存在时不覆盖。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    _load_skill_dotenv()
    # 子进程强制 UTF-8 输出：Windows 管道下子进程默认 GBK，中文日志
    # 会被错误解码成乱码替换符
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "omnimd.cli", *sys.argv[1:]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
