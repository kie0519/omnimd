"""跨形态共享的测试工具：哑凭据环境与哑配置工厂。

测试目录按形态分（library / cli / demo），共享工具经包路径导入：
`from tests.helpers import make_config, REQUIRED_ENV`
"""

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
