"""VLM 层回归：瘦客户端契约（mock 客户端，不联网）"""
import pytest

from openai import BadRequestError

from omnimd.exceptions import OmniMDError
from omnimd.vlm.openai_compat import OpenAICompatVlm
from tests.helpers import make_config


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeClient:
    """记录调用次数的假 OpenAI 客户端"""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                if outer._error is not None:
                    raise outer._error
                return outer._response

        class _Chat:
            def __init__(self):
                self.completions = _Completions()

        self.chat = _Chat()


def _vlm_with(response=None, error=None) -> tuple[OpenAICompatVlm, _FakeClient]:
    vlm = OpenAICompatVlm(make_config())   # 构造期无网络请求
    fake = _FakeClient(response, error)
    vlm.client = fake
    return vlm, fake


def test_returns_stripped_content():
    """正常返回：内容经空白归一，单次调用"""
    vlm, fake = _vlm_with(_Response("  # 转写结果  \n"))
    assert vlm.ocr("data:image/png;base64,xxx") == "# 转写结果"
    assert fake.calls == 1


def test_empty_content_returns_empty_string():
    """内容为空：返回空串（契约恒为 str），处置归转换层"""
    vlm, fake = _vlm_with(_Response(None))
    assert vlm.ocr("data:image/png;base64,xxx") == ""
    assert vlm.describe("data:image/png;base64,xxx") == ""
    assert fake.calls == 2                    # 每任务各一次，无重试


def test_api_error_propagates_unchanged():
    """API 异常：原样上抛（不包装、不重试）"""
    boom = RuntimeError("api 连接失败")
    vlm, fake = _vlm_with(error=boom)
    with pytest.raises(RuntimeError, match="api 连接失败"):
        vlm.ocr("data:image/png;base64,xxx")
    assert fake.calls == 1


# ---------- URL 传图不支持的端点能力边界（唯一改写分支） ----------

def _bad_request(msg: str):
    import httpx

    request = httpx.Request("POST", "https://vlm.example/v1/chat/completions")
    response = httpx.Response(400, request=request,
                              json={"error": {"message": msg}})
    return BadRequestError(msg, response=response, body=response.json())


def test_url_unsupported_error_becomes_guidance():
    """URL 传图被拒（Ollama 文案）：改写为带两条出路的 OmniMDError"""
    vlm, _ = _vlm_with(
        error=_bad_request(
            "image URLs are not currently supported, "
            "please use base64 encoded data instead"
        ))
    with pytest.raises(OmniMDError) as e:
        vlm.ocr("https://cdn.example/img.png")
    msg = str(e.value)
    assert "base64" in msg and "端点" in msg   # 指引两条出路：换部件 / 换端点


def test_other_bad_request_propagates():
    """不匹配能力边界的 400：原样透传，不改写"""
    boom = _bad_request("Invalid model: nope")
    vlm, _ = _vlm_with(error=boom)
    with pytest.raises(BadRequestError, match="Invalid model"):
        vlm.ocr("https://cdn.example/img.png")


def test_data_uri_bad_request_not_rewritten():
    """data URI 传图时的 400：即使文案命中 marker 也原样透传——改写分支
    只作用于 URL 传图（image_ref 前缀是前置条件）"""
    boom = _bad_request("please use base64 encoded data instead")
    vlm, _ = _vlm_with(error=boom)
    with pytest.raises(BadRequestError):
        vlm.ocr("data:image/png;base64,xxx")
