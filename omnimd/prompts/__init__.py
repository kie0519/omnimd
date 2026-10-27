"""提示词包：任务注册表 + 内置任务母本

- registry.py：TaskSpec / TaskRegistry / build_registry——任务知识的唯一注册处
- transcribe.py：图片完整转写（transcribe 任务）
- describe.py：图片一句话描述（describe 任务，填入链接描述位）

内置任务 = 普适动词集；自定义任务经工作目录 prompts/*.md 发现或库 API 显式传入。
"""
from .describe import DESCRIBE_SYSTEM_PROMPT, DESCRIBE_USER_PROMPT
from .registry import (
    GENERAL_SYSTEM_PROMPT,
    TaskRegistry,
    TaskSpec,
    adhoc_task_name,
    build_registry,
    builtin_specs,
    effective_system,
    failure_note,
    parse_task_file,
    task_digest,
    validate_name,
)
from .transcribe import TRANSCRIBE_SYSTEM_PROMPT, TRANSCRIBE_USER_PROMPT

__all__ = [
    "DESCRIBE_SYSTEM_PROMPT", "DESCRIBE_USER_PROMPT",
    "GENERAL_SYSTEM_PROMPT",
    "TRANSCRIBE_SYSTEM_PROMPT", "TRANSCRIBE_USER_PROMPT",
    "TaskRegistry", "TaskSpec",
    "build_registry", "builtin_specs", "effective_system", "adhoc_task_name",
    "failure_note", "parse_task_file", "task_digest", "validate_name",
]
