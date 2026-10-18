"""全形态共享夹具：屏蔽开发机 .env 的可选变量（library / cli / demo 通用）"""
import pytest


@pytest.fixture(autouse=True)
def neutralize_optional_env(monkeypatch):
    """屏蔽真实 .env 载入的可选变量——测试环境与开发机配置解耦"""
    for name in ("OUTPUT", "DOC_ENGINE",
                 "VLM_MAX_TOKENS", "VLM_CONCURRENCY", "VLM_THINKING",
                 "S3_KEY_PREFIX"):
        monkeypatch.delenv(name, raising=False)
