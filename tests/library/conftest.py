"""库测试专属夹具：补齐必填环境变量（哑凭据）"""
import pytest

from tests.helpers import REQUIRED_ENV


@pytest.fixture()
def full_env(monkeypatch):
    """补齐全部必填环境变量——测试不依赖仓库 .env 是否存在"""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    yield
