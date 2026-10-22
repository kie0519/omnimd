"""安全不变量回归（库）：密钥样内容不得进入产出与日志。"""
import logging

from omnimd.converter import Converter
from tests.helpers import make_config


def test_secret_never_enters_output(tmp_path, caplog):
    """内容里出现过的密钥样标记串不得进入产出——
    转写/渲染各环节只搬运文档内容，不搬运配置值"""
    fake_secret = "sk-FAKE-MARKER-do-not-leak-0123456789abcdef"
    config = make_config(cache_dir=tmp_path, vlm_api_key=fake_secret)
    conv = Converter(config)
    src = tmp_path / "s.md"
    src.write_text("# 含密钥场景\n\n正文引用环境里的密钥做标记。\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="omnimd"):
        result = conv.convert(src, output="body")

    assert fake_secret not in result.markdown
    assert fake_secret not in caplog.text
