"""tests 共享夹具：哑配置环境变量（避免真实 .env 依赖）"""
import pytest


REQUIRED_ENV = {
    "VLM_API_KEY": "key",
    "VLM_BASE_URL": "https://vlm.example/v1",
    "VLM_MODEL": "vision-model",
    "STORAGE_PROVIDER": "s3",
    "S3_ENDPOINT_URL": "https://s3.example",
    "S3_REGION": "region",
    "S3_BUCKET": "bucket",
    "S3_ACCESS_KEY_ID": "ak",
    "S3_SECRET_ACCESS_KEY": "sk",
}


@pytest.fixture()
def full_env(monkeypatch):
    """补齐全部必填环境变量——测试不依赖仓库 .env 是否存在"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    yield


def make_config(**overrides) -> "Config":
    """哑配置构造（全部必填字段齐全，供需要直接构造 Config 的测试）"""
    from omnimd.config import Config
    fields = dict(
        vlm_api_key="key",
        vlm_base_url="https://vlm.example/v1", vlm_model="vision-model",
        vlm_thinking="thinking-off",
        storage_provider="s3", s3_endpoint_url="https://s3.example",
        s3_region="region", s3_bucket="bucket", s3_access_key_id="ak",
        s3_secret_access_key="sk", s3_key_prefix="",
    )
    fields.update(overrides)
    return Config(**fields)


@pytest.fixture(autouse=True)
def neutralize_optional_env(monkeypatch):
    """屏蔽真实 .env 载入的可选变量——测试环境与开发机配置解耦"""
    for name in ("OUTPUT", "DOC_ENGINE", "VLM_IMAGE_INPUT",
                 "VLM_MAX_TOKENS", "VLM_CONCURRENCY", "VLM_THINKING",
                 "S3_KEY_PREFIX"):
        monkeypatch.delenv(name, raising=False)
