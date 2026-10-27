"""参数编译层回归：糖等价、双语义分流、呈现校验、describe 依赖（纯函数，不联网）"""
import pytest

from omnimd.converter import PipelinePlan, compile_plan
from omnimd.exceptions import ConfigError
from omnimd.prompts import TaskSpec, build_registry


@pytest.fixture()
def registry():
    return build_registry()


def test_sugar_equivalence(registry):
    """语法糖等价由编译结构保证：--transcribe 与 --prompt transcribe 编译为同一 TaskSpec"""
    via_flag = compile_plan(registry, transcribe=True)
    via_prompt = compile_plan(registry, prompts=("transcribe",))
    assert via_flag.tasks == via_prompt.tasks
    assert via_flag.tasks[0] is registry["transcribe"]      # 引用注册表同一对象
    assert via_flag == PipelinePlan(presentation="drop",
                                    tasks=(registry["transcribe"],))


def test_describe_sugar_equivalence(registry):
    via_flag = compile_plan(registry, images="link", describe=True)
    via_prompt = compile_plan(registry, images="link", prompts=("describe",))
    assert via_flag.tasks == via_prompt.tasks


def test_default_plan_is_drop_no_tasks(registry):
    plan = compile_plan(registry)
    assert plan.presentation == "drop"
    assert plan.tasks == ()


def test_presentation_values(registry):
    assert compile_plan(registry, images="link").presentation == "link"
    assert compile_plan(registry, images="embed").presentation == "embed"


def test_presentation_rejects_unknown(registry):
    with pytest.raises(ConfigError) as e:
        compile_plan(registry, images="url")
    assert "link" in str(e.value) and "embed" in str(e.value)   # 报错列出全部取值


# ---------- --prompt 双语义 ----------

def test_prompt_reference_resolves_registered_task(registry):
    """值命中注册表键 → 任务引用（非即时文本）"""
    plan = compile_plan(registry, prompts=("transcribe",))
    assert plan.tasks[0] is registry["transcribe"]


def test_prompt_free_text_becomes_adhoc_task(registry):
    """值未命中注册表 → 即时任务：提示词本体即任务；名从内容稳定派生（缓存命中不随参数顺序漂移）"""
    import hashlib
    plan = compile_plan(registry, prompts=("重点看配色",))
    (task,) = plan.tasks
    assert task.prompt == "重点看配色"
    want = "prompt-" + hashlib.sha256("重点看配色".encode()).hexdigest()[:8]
    assert task.name == want
    assert task.render == "block"


def test_prompt_repeats_accumulate_sequence(registry):
    """--prompt 可重复：即时任务名从内容派生，同提示词重复出现得到同名任务
    （名只用于缓存目录与日志，不进渲染）"""
    plan = compile_plan(registry,
                        prompts=("重点看配色", "transcribe", "重点看配色"))
    names = [t.name for t in plan.tasks]
    assert names[1] == "transcribe"
    assert names[0] == names[2]                     # 同提示词 → 同名（缓存命中）
    assert names[0].startswith("prompt-")


def test_prompt_empty_rejected(registry):
    for bad in ("", "   "):
        with pytest.raises(ConfigError, match="不能为空"):
            compile_plan(registry, prompts=(bad,))


def test_prompt_accepts_taskspec_directly(registry):
    """库路径：TaskSpec 直接进编译（TaskPrompt 已在库层转换）"""
    walk = TaskSpec(name="walk", prompt="走查")
    plan = compile_plan(registry, prompts=(walk,))
    assert plan.tasks == (walk,)


# ---------- describe 依赖 ----------

def test_describe_without_images_rejected_with_guidance(registry):
    """describe 填在图片链接的文字说明里，drop 呈现下报错并给两条出路"""
    with pytest.raises(ConfigError) as e:
        compile_plan(registry, describe=True)
    msg = str(e.value)
    assert "--images link" in msg and "--images embed" in msg
    assert "图片链接" in msg                     # 依赖原因写进报错


def test_describe_via_prompt_without_images_rejected(registry):
    """糖等价的依赖校验也走同一通道：--prompt describe 同样受限"""
    with pytest.raises(ConfigError, match="--images"):
        compile_plan(registry, prompts=("describe",))


def test_transcribe_without_images_is_legal(registry):
    """转写不依赖呈现：drop + transcribe 合法（纯文本库形态）"""
    plan = compile_plan(registry, transcribe=True)
    assert plan.presentation == "drop"
    assert len(plan.tasks) == 1


def test_adhoc_task_not_subject_to_describe_dependency(registry):
    """依赖只限 attribute 渲染任务；即时任务（block）在 drop 下合法"""
    plan = compile_plan(registry, prompts=("重点看配色",))
    assert plan.presentation == "drop"
