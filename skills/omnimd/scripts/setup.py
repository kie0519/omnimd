"""首次安装：安装 omnimd 包并生成 .env 模板（幂等，可重复执行）。

安装顺序：
  ① omnimd 已可导入 → 跳过安装（最常见：本机已装过）；
  ② 探测到本地仓库 → pip install -e（开发机形态，代码即最新）；
  ③ pip install omnimd（PyPI，标准分发形态）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def _installed() -> str | None:
    """已安装则返回版本号（供跳过提示展示），未安装返回 None"""
    proc = subprocess.run(
        [sys.executable, "-c", "import omnimd; print(omnimd.__version__)"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _find_local_repo() -> Path | None:
    """定位 omnimd 仓库根：OMNIMD_ROOT 环境变量 → 脚本位置逐级向上。

    仓库根标志：同时存在 pyproject.toml 与 omnimd/cli.py。
    """
    candidates: list[Path] = []
    env = os.getenv("OMNIMD_ROOT")
    if env:
        candidates.append(Path(env))
    candidates.extend(Path(__file__).resolve().parents)
    for cand in dict.fromkeys(candidates):
        for root in (cand, cand / "omnimd"):
            if ((root / "pyproject.toml").is_file()
                    and (root / "omnimd" / "cli.py").is_file()):
                return root
    return None


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"需要 Python 3.10+，当前：{sys.version.split()[0]}", file=sys.stderr)
        return 1

    installed = _installed()
    if installed:
        print(f"omnimd {installed} 已安装，跳过安装"
              "（版本过旧时先 pip uninstall omnimd 再重跑本脚本）。")
    else:
        repo = _find_local_repo()
        if repo:
            cmd = [sys.executable, "-m", "pip", "install", "-e", str(repo)]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "omnimd"]
        print("安装：", " ".join(cmd))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            return proc.returncode

    env_file = SKILL_ROOT / ".env"
    if not env_file.exists():
        shutil.copyfile(SKILL_ROOT / ".env.example", env_file)
        print(f"已生成 {env_file}（模板）。请填入真实密钥后执行 "
              f"`python {SKILL_ROOT / 'scripts' / 'run.py'} --help` 自检。")
    else:
        print(f"依赖就绪。可用 `python {SKILL_ROOT / 'scripts' / 'run.py'} --help` 自检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
