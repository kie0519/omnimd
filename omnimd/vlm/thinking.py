"""思考控制方式 → chat.completions 的 extra_body 参数表

思考控制是 OpenAI 兼容端点之间的主要请求差异，由 VLM_THINKING 配置。
取值覆盖两个维度——方向（开 / 关）× 协议（thinking / chat_template / reasoning_effort / 双发），对称展开。
转写是提取任务，通常关闭思考以省输出预算；缺省 none 不干预，思考由端点默认行为决定。

- none：不发思考参数（缺省）——无思考能力的模型（非推理模型）或不接受思考参数的端点（严格校验未知参数的实现会直接报错）
- thinking-off / thinking-on：thinking.type=disabled / enabled——云端 API 服务（DeepSeek、智谱 GLM 等及多数聚合网关）的通用形态
- template-off / template-on：chat_template_kwargs.enable_thinking——自部署开源权重
  （vLLM / Ollama / SGLang 运行 Qwen3、GLM、DeepSeek 等开源模型，对话模板的通用思考开关）
- effort-off / effort-on：reasoning_effort=minimal / high——档位制思考控制的端点（Grok、Gemini 兼容层等）。
  OpenAI 官方推理模型（o 系 / GPT-5）要求 max_completion_tokens 而不接受 max_tokens，与本实现不兼容，不适用；
  minimal 是 GPT-5 系起的最低思考档（近关闭），high 为全开
- both-off / both-on：thinking 与 chat_template 两组参数同发——同一入口的路由混合这两种协议渠道

接入新端点不改代码；出现新的思考控制协议才加一对取值。
"""

_DISABLED = {"thinking": {"type": "disabled"}}
_TEMPLATE_OFF = {"chat_template_kwargs": {"enable_thinking": False}}

THINKING_EXTRA_BODY: dict[str, dict] = {
    "none": {},
    "thinking-off": _DISABLED,
    "thinking-on": {"thinking": {"type": "enabled"}},
    "template-off": _TEMPLATE_OFF,
    "template-on": {"chat_template_kwargs": {"enable_thinking": True}},
    "effort-off": {"reasoning_effort": "minimal"},
    "effort-on": {"reasoning_effort": "high"},
    "both-off": {**_DISABLED, **_TEMPLATE_OFF},
    "both-on": {
        "thinking": {"type": "enabled"},
        "chat_template_kwargs": {"enable_thinking": True},
    },
}
