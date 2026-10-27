"""性能基准：管线自身开销的三个可控指标（pytest-benchmark 多次采样）。

只测不受外部服务影响的路径；断言为宽松防退化上限，统计数字供人工对比，不作 CI 门槛。
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
    """文字类裸命令（仅正文）：单文件全管线耗时——解析引擎 + 注册 + 渲染"""
    from omnimd.converter import Converter
    from omnimd.prompts import build_registry
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    src = _text_file(tmp_path)

    def run():
        return conv.convert(src)

    result = benchmark(run)
    assert "基准样例" in result.markdown and result.image_count == 0
    # 宽松上限：纯解析 300 行文本应在秒级内（防性能退化断言，非精确承诺）
    assert benchmark.stats.stats.mean < 5.0


def test_rerun_overhead_benchmark(benchmark, tmp_path):
    """同文件二跑开销：纯文本转换（无图片）度量重跑全管线自身的开销上界
    （任务产物/URL 两层缓存挂在图片产物上，纯文本无缓存可命中）"""
    from omnimd.converter import Converter
    from omnimd.prompts import build_registry
    conv = Converter(make_config(cache_dir=tmp_path), build_registry())
    src = _text_file(tmp_path, "second.md")
    conv.convert(src)   # 首跑（引擎加载）

    def run():
        return conv.convert(src)

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
