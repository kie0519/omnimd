"""VLM 层回归：瘦客户端契约（mock 客户端，不联网）"""
import pytest

from omnimd.vlm.openai_compat import OpenAICompatVlm
from conftest import make_config


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
