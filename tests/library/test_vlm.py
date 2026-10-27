"""VLM 层回归：纯协议契约（run 单方法 / 状态码转译 / 用量透传 / 超时注入；mock 客户端，不联网）"""
import pytest
from openai import APIStatusError, BadRequestError

from omnimd.exceptions import ConfigError, OmniMDError
from omnimd.vlm.base import VisionRequest
from omnimd.vlm.openai_compat import OpenAICompatVlm
from tests.helpers import make_config


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Usage:
    def __init__(self, prompt=0, completion=0, total=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _Response:
    def __init__(self, content, usage=None):
        self.choices = [_Choice(content)]
        self.usage = usage


class _FakeClient:
    """记录调用次数与参数的假 OpenAI 客户端"""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = 0
        self.last_kwargs = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                outer.last_kwargs = kwargs
                if outer._error is not None:
                    raise outer._error
                return outer._response

        class _Chat:
            def __init__(self):
                self.completions = _Completions()

        self.chat = _Chat()


def _request(system="s", user="u", max_tokens=100):
    return VisionRequest(system=system, user=user, max_tokens=max_tokens)


def _vlm_with(response=None, error=None, config=None) -> tuple[OpenAICompatVlm, _FakeClient]:
    vlm = OpenAICompatVlm(config or make_config())   # 构造期无网络请求
    fake = _FakeClient(response, error)
    vlm.client = fake
    return vlm, fake


# ---------- run：正常路径 ----------

def test_run_returns_text_and_usage():
    """正常返回：文本经空白归一，usage 三字段透传，单次调用"""
    vlm, fake = _vlm_with(_Response(
        "  # 转写结果  \n", usage=_Usage(prompt=11, completion=22, total=33)))
    result = vlm.run(_request(), "data:image/png;base64,xxx")
    assert result.text == "# 转写结果"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens,
            result.usage.total_tokens) == (11, 22, 33)
    assert fake.calls == 1


def test_run_without_usage_yields_none():
    """端点未返回用量：usage 为 None，文本照常"""
    vlm, _ = _vlm_with(_Response("内容"))
    result = vlm.run(_request(), "data:image/png;base64,xxx")
    assert result.text == "内容"
    assert result.usage is None


def test_run_sends_request_fields():
    """请求字段进 SDK：system/user/max_tokens 直达 messages 与参数，思考参数经 extra_body 注入"""
    vlm, fake = _vlm_with(_Response("ok"))
    vlm.run(_request(system="SYS", user="USER", max_tokens=777),
            "data:image/png;base64,xxx")
    kw = fake.last_kwargs
    assert kw["max_tokens"] == 777
    assert kw["messages"][0] == {"role": "system", "content": "SYS"}
    content = kw["messages"][1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[1] == {"type": "text", "text": "USER"}
    # VLM_THINKING 取值生效：thinking-off 的 extra_body 注入 SDK 调用
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}


def test_empty_content_returns_empty_text():
    """内容为空：返回空文本（处置归转换层）"""
    vlm, fake = _vlm_with(_Response(None))
    result = vlm.run(_request(), "data:image/png;base64,xxx")
    assert result.text == ""
    assert fake.calls == 1


def test_api_error_propagates_unchanged():
    """非状态码异常：原样上抛（不包装、不重试）"""
    boom = RuntimeError("api 连接失败")
    vlm, fake = _vlm_with(error=boom)
    with pytest.raises(RuntimeError, match="api 连接失败"):
        vlm.run(_request(), "data:image/png;base64,xxx")
    assert fake.calls == 1


# ---------- 超时注入（构造参数） ----------

def test_timeout_injected_into_client(monkeypatch):
    """VLM_TIMEOUT 构造期注入 OpenAI 客户端（超时显式化）"""
    captured = {}

    class _CapturingOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = None

    import omnimd.vlm.openai_compat as mod
    monkeypatch.setattr(mod, "OpenAI", _CapturingOpenAI)
    OpenAICompatVlm(make_config(vlm_timeout=123.0))
    assert captured["timeout"] == 123.0


# ---------- 状态码转译（零猜测指引） ----------

def _status_error(status: int, msg: str = "boom"):
    import httpx
    request = httpx.Request("POST", "https://vlm.example/v1/chat/completions")
    response = httpx.Response(status, request=request,
                              json={"error": {"message": msg}},
                              headers={"x-request-id": "req-abc"})
    return APIStatusError(msg, response=response, body=response.json())


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_translated_to_config_guidance(status):
    """401/403 → 凭据指引（ConfigError），附原始错误与 request_id"""
    vlm, _ = _vlm_with(error=_status_error(status, "bad key"))
    with pytest.raises(ConfigError) as e:
        vlm.run(_request(), "data:image/png;base64,xxx")
    msg = str(e.value)
    assert f"HTTP {status}" in msg
    assert "VLM_API_KEY" in msg and "VLM_BASE_URL" in msg
    assert "bad key" in msg                    # 原始错误附在报错内
    assert "req-abc" in msg                    # request_id 附在报错内


def test_not_found_translated_to_model_or_path_guidance():
    """404 → 模型名 / base_url 路径指引（缺 /v1 是常见成因）"""
    vlm, _ = _vlm_with(error=_status_error(404, "model not found"))
    with pytest.raises(ConfigError) as e:
        vlm.run(_request(), "data:image/png;base64,xxx")
    msg = str(e.value)
    assert "VLM_MODEL" in msg and "VLM_BASE_URL" in msg
    assert "/v1" in msg


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_status_errors_propagate(status):
    """429/5xx：透传原异常（SDK 已重试过，原始证据最准，不包装不改写）"""
    vlm, _ = _vlm_with(error=_status_error(status, "overloaded"))
    with pytest.raises(APIStatusError, match="overloaded"):
        vlm.run(_request(), "data:image/png;base64,xxx")


# ---------- URL 传图不支持的端点能力边界（唯一按请求形态改写的分支） ----------

def _bad_request(msg: str):
    import httpx
    request = httpx.Request("POST", "https://vlm.example/v1/chat/completions")
    response = httpx.Response(400, request=request,
                              json={"error": {"message": msg}})
    return BadRequestError(msg, response=response, body=response.json())


def test_url_unsupported_error_becomes_guidance():
    """URL 传图被拒（400 + URL 形态）：改写为带两条出路的 OmniMDError，附原始错误"""
    vlm, _ = _vlm_with(
        error=_bad_request(
            "image URLs are not currently supported, "
            "please use base64 encoded data instead"
        ))
    with pytest.raises(OmniMDError) as e:
        vlm.run(_request(), "https://cdn.example/img.png")
    msg = str(e.value)
    assert "--images embed" in msg          # 出路①：内嵌呈现（base64 直传）
    assert "端点" in msg                     # 出路②：换端点
    assert "image URLs are not currently supported" in msg   # 原始错误全文


def test_other_bad_request_via_url_still_gives_guidance():
    """URL 形态的 400 一律触发组合判定指引（不依赖文案匹配）——
    「URL 传图 + 400」就是能力边界的充分证据"""
    vlm, _ = _vlm_with(error=_bad_request("some unrelated 400 message"))
    with pytest.raises(OmniMDError, match="--images embed"):
        vlm.run(_request(), "https://cdn.example/img.png")


def test_data_uri_bad_request_not_rewritten():
    """data URI 传图时的 400：即使端点文案相似也原样透传——改写分支
    只作用于 URL 传图（image_ref 前缀是前置条件）"""
    boom = _bad_request("please use base64 encoded data instead")
    vlm, _ = _vlm_with(error=boom)
    with pytest.raises(BadRequestError):
        vlm.run(_request(), "data:image/png;base64,xxx")
