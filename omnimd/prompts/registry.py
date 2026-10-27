"""任务注册表：任务的唯一家

任务 = 一段提示词（用户提示词必填、系统提示词可选）。任务知识全部收敛于此：
内置任务母本在 transcribe.py / describe.py，自定义任务从工作目录 prompts/ 发现
或经库 API 显式传入，三层按「内置 → 目录发现 → 显式对象」顺序并入，同名后者
覆盖（提示词就是任务的本体）。

digest 基于生效提示词（system 缺省取通用骨架）计算——骨架文案更新时缓存自然失效。
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import ConfigError
from .describe import DESCRIBE_SYSTEM_PROMPT, DESCRIBE_USER_PROMPT
from .transcribe import TRANSCRIBE_SYSTEM_PROMPT, TRANSCRIBE_USER_PROMPT

# 任务名规则：小写字母开头，后接小写字母/数字/短横线/下划线——兼作注册表键、
# prompts/ 文件名与缓存目录名
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

# describe 的输出上限：一句话描述；其余任务跟配置 VLM_MAX_TOKENS
_DESCRIBE_MAX_TOKENS = 512

# 通用骨架：任务无关的「看图回答」底座（即时任务的缺省 system）。与内置母本
# 同水位但克制——它是垫底不是主角：服从用户提示词的元指令优先（用户 prompt 是
# 任务定义，先解读意图再按需调焦），可见性纪律与输出形态次之，不预设领域结构。
# 领域方法论随任务自带（任务文件 frontmatter 的 system 覆盖骨架）。
GENERAL_SYSTEM_PROMPT = """You are a senior visual analyst serving \
engineers, analysts, and writers who consume your output as reference \
material. The user's prompt is the task definition: interpret it first — \
what is being asked, at what depth, in what structure — and deliver exactly \
that, adjusting focus and depth to the request rather than a fixed template.

Ground every statement in what the image visibly shows. Distinguish observed \
facts from inference, and mark inference as such. Transcribe visible text, \
numbers, and identifiers verbatim — no rounding, conversion, or completion. \
Where content is illegible or partially visible, state that plainly instead \
of guessing.

Respond in well-structured Markdown: headings and lists mirroring the \
image's structure, tables for tabular data, fenced code blocks with \
language tags for code. No preamble or closing remarks."""


@dataclass(frozen=True)
class TaskSpec:
    """一个任务的完整定义（frozen——注册后不可变）"""

    name: str                     # 任务名（注册表键）
    prompt: str                   # 用户提示词（必填——任务本体）
    system: str = ""              # 系统提示词；空串 = 通用骨架
    render: str = "block"         # block=独立文本块；attribute=填入图片链接描述位
    max_tokens: int | None = None # None = 跟配置 VLM_MAX_TOKENS


def builtin_specs() -> tuple[TaskSpec, ...]:
    """内置任务：普适动词集——每个任务对任意图片成立，场景化任务不进内置"""
    return (
        TaskSpec(
            name="transcribe",
            prompt=TRANSCRIBE_USER_PROMPT,
            system=TRANSCRIBE_SYSTEM_PROMPT,
        ),
        TaskSpec(
            name="describe",
            prompt=DESCRIBE_USER_PROMPT,
            system=DESCRIBE_SYSTEM_PROMPT,
            render="attribute",
            max_tokens=_DESCRIBE_MAX_TOKENS,
        ),
    )


def effective_system(spec: TaskSpec) -> str:
    """生效系统提示词：任务未自带（空串）时取通用骨架"""
    return spec.system or GENERAL_SYSTEM_PROMPT


def task_digest(spec: TaskSpec) -> str:
    """生效提示词的内容摘要——提示词或骨架变更即缓存失效"""
    content = effective_system(spec) + spec.prompt
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def failure_note(spec: TaskSpec) -> str:
    """任务失败的产出占位。内置 transcribe 的文案是产出兼容契约（下游按「（图片转写失败）」
    识别失败标记，不可改写）；自定义任务点名任务名。"""
    if spec.name == "transcribe":
        return "（图片转写失败）"
    return f"（图片任务 {spec.name} 失败）"


def adhoc_task_name(prompt: str) -> str:
    """即时任务名：从提示词内容稳定派生（不进渲染）——同一提示词跨运行、
    跨序号命中同一缓存目录。编译层与集成方共用此规则，一处定义。"""
    import hashlib
    return f"prompt-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:8]}"


def validate_name(name: str, context: str) -> None:
    """任务名非法即报错——错误带规则与出处（文件路径或参数名）"""
    if not _NAME_PATTERN.match(name):
        raise ConfigError(
            f"任务名不合法：{name!r}（出处：{context}）。"
            f"规则：小写字母开头，后接小写字母/数字/短横线/下划线（{ _NAME_PATTERN.pattern }）"
        )


def _dedent(lines: list[str]) -> str:
    """去掉块内各行的最小公共前导空白"""
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    if not indents:
        return ""
    n = min(indents)
    return "\n".join(l[n:] if l.strip() else "" for l in lines)


def parse_task_file(path: Path) -> TaskSpec:
    """解析任务文件：Markdown + frontmatter（name / system 两键的子集），正文 = 提示词。

    缺省链：name ← 文件名、system ← 空串（= 通用骨架）、正文必填。
    frontmatter 只支持 name 与 system（system 用 | 块标量）；其他键报错并附支持清单。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    meta: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        close = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                close = i
                break
        if close is None:
            raise ConfigError(f"任务文件 {path} 的 frontmatter 未闭合（缺结尾 ---）")
        i = 1
        while i < close:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            matched = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if not matched:
                raise ConfigError(
                    f"任务文件 {path} 的 frontmatter 无法解析：{line!r}"
                    "（支持 name: 单值 与 system: | 块标量两键）"
                )
            key, value = matched.group(1), matched.group(2).strip()
            if key == "name":
                if not value:
                    raise ConfigError(f"任务文件 {path} 的 name 为空")
                meta["name"] = value
                i += 1
            elif key == "system" and value == "|":
                i += 1
                block: list[str] = []
                while i < close:
                    nxt = lines[i]
                    if not nxt.strip():
                        block.append("")
                        i += 1
                        continue
                    if nxt[:1] in (" ", "\t"):
                        block.append(nxt)
                        i += 1
                    else:
                        break
                meta["system"] = _dedent(block).strip("\n")
            else:
                raise ConfigError(
                    f"任务文件 {path} 的 frontmatter 不支持键 {key!r}"
                    "（支持：name、system）"
                )
        body_lines = lines[close + 1:]
    else:
        body_lines = lines
    prompt = "\n".join(body_lines).strip()
    if not prompt:
        raise ConfigError(
            f"任务文件 {path} 的正文为空——正文是提示词本体，必填"
        )
    name = meta.get("name") or path.stem
    validate_name(name, str(path))
    return TaskSpec(name=name, prompt=prompt, system=meta.get("system", ""))


class TaskRegistry:
    """已注册任务的只读视图：编译层与缓存目录按任务名取用"""

    def __init__(self, specs=()):
        self._specs: dict[str, TaskSpec] = {}
        for spec in specs:
            self.add(spec)

    def add(self, spec: TaskSpec) -> None:
        """注册（同名覆盖——后层覆盖前层）；入库前校验名与提示词"""
        validate_name(spec.name, f"任务 {spec.name}")
        if not spec.prompt.strip():
            raise ConfigError(
                f"任务 {spec.name!r} 的提示词为空——提示词是任务本体，必填"
            )
        if spec.render not in ("block", "attribute"):
            raise ConfigError(
                f"任务 {spec.name!r} 的 render 非法：{spec.render!r}（可选：block、attribute）"
            )
        self._specs[spec.name] = spec

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __getitem__(self, name: str) -> TaskSpec:
        return self._specs[name]

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def items(self) -> list[tuple[str, TaskSpec]]:
        return sorted(self._specs.items())


def build_registry(
    explicit=(), workdir: Path | None = None,
) -> TaskRegistry:
    """组装任务注册表：内置 → 工作目录 prompts/ 发现 → 显式对象，同名后者覆盖。

    显式对象是库调用方传入的 TaskSpec（已校验基本形态）；目录发现只收 *.md，
    非法文件名报错、空正文报错、其余非 md 文件忽略。
    """
    registry = TaskRegistry(builtin_specs())
    if workdir is not None:
        task_dir = workdir / "prompts"
        if task_dir.is_dir():
            for path in sorted(task_dir.glob("*.md")):
                registry.add(parse_task_file(path))
    for spec in explicit:
        registry.add(spec)
    return registry
