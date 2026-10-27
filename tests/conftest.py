"""全形态共享夹具：与开发机 .env 完全隔离（library / cli / demo 通用）"""
import pytest


@pytest.fixture(autouse=True)
def neutralize_optional_env(monkeypatch):
    """隔离开发机配置——load() 每次调用都重读 .env 查找链，测试把查找链
    指向空并清掉全部配置变量；测试需要的变量由各测试显式注入。"""
    import omnimd.config as cfg
    monkeypatch.setattr(cfg, "_env_files", lambda: ())
    for name in ("DOC_ENGINE", "CACHE_DIR",
                 "VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL",
                 "VLM_MAX_TOKENS", "VLM_CONCURRENCY", "VLM_TIMEOUT",
                 "VLM_THINKING",
                 "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET",
                 "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_KEY_PREFIX"):
        monkeypatch.delenv(name, raising=False)
