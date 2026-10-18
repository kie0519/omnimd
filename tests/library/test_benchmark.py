"""性能基准：管线自身开销的三个可控指标（pytest-benchmark 多次采样）。

只测不受外部服务影响的路径——含 VLM 的转换耗时由模型服务商决定，
不进基准（不可控）。断言用宽松上限防退化；benchmark 的统计数字
（min/mean/ops）供版本间人工对比，不做 CI 硬门槛（CI 机器性能
差异会误报）。
"""
import pytest

from tests.helpers import make_config

pytest.importorskip("pytest_benchmark")


def _text_file(tmp_path, name="bench.md", lines=300):
    """固定样例：纯文本 Markdown（真实走解析引擎，无图片零外部调用）"""
    src = tmp_path / name
    body = "\n".join(f"第 {i} 行：基准样例正文，含中文与 English mix。" for i in range(lines))
    src.write_text(f"# 基准样例\n\n{body}\n", encoding="utf-8")
    return src


def test_pure_parse_benchmark(benchmark, tmp_path):
    """纯解析（body）：单文件全管线耗时——解析引擎 + 注册 + 渲染"""
    from omnimd.converter import Converter
    conv = Converter(make_config(cache_dir=tmp_path))
    src = _text_file(tmp_path)

    def run():
        return conv.convert(src, output="body")

    result = benchmark(run)
    assert "基准样例" in result.markdown and result.image_count == 0
    # 宽松上限：纯解析 300 行文本应在秒级内（防性能退化断言，非精确承诺）
    assert benchmark.stats.stats.mean < 5.0


def test_cache_hit_benchmark(benchmark, tmp_path):
    """缓存命中路径：同文件二跑的耗时承诺（纯解析无外部调用，
    此处度量的是「重跑全管线自身开销」的上界形态）"""
    from omnimd.converter import Converter
    conv = Converter(make_config(cache_dir=tmp_path))
    src = _text_file(tmp_path, "second.md")
    conv.convert(src, output="body")   # 首跑（建缓存/引擎加载）

    def run():
        return conv.convert(src, output="body")

    benchmark(run)
    assert benchmark.stats.stats.mean < 5.0


def test_batch_throughput_benchmark(benchmark, tmp_path):
    """批处理吞吐：20 个小文件的目录批转换（文件/秒）"""
    from omnimd.library import OmniMD
    md = OmniMD(cache_dir=tmp_path)
    d = tmp_path / "batch"
    d.mkdir()
    for i in range(20):
        (d / f"f{i:02d}.md").write_text(f"# 文件 {i}\n\n正文行。\n", encoding="utf-8")

    def run():
        return md.convert_many([d])

    results = run()   # 预热一次（引擎导入/构造），benchmark 度量稳态
    assert len(results) == 20 and all(
        not isinstance(r, Exception) for r in results.values()
    )
    benchmark(run)
    assert benchmark.stats.stats.mean < 30.0   # 20 文件 < 30s（宽松）
