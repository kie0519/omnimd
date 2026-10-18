"""omnimd 命令行入口

用法：
    omnimd <文件或目录> [选项]

命令行参数只覆盖运行项（产出组成 / 并发 / 缓存目录 / 调试日志）；
连接配置（VLM 凭据、对象存储）在 .env 中给定。

示例：
    omnimd <文件路径>.pdf -o output/
    omnimd <输入目录>/ -o output/ --output body,url,ocr
"""
import argparse
import logging
import sys
from pathlib import Path

from .exceptions import OmniMDError, UnsupportedFormatError

logger = logging.getLogger(__name__)


def _collect_inputs(paths: list[str]) -> list[Path]:
    """收集输入文件：目录展开（按名排序），文件直接收入，不支持的格式一次报全"""
    from .engines import supported_extensions
    supported = set(supported_extensions())
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            # 跳过点前缀隐藏文件（.DS_Store / desktop.ini 等系统杂质），
            # 不让单个杂质文件废掉整批转换；可见文件仍显式报错
            files.extend(sorted(f for f in path.iterdir()
                                if f.is_file() and not f.name.startswith(".")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"路径不存在：{p}。请核对路径拼写后重试。")
    if not files:
        raise ValueError("没有找到可转换的文件。请确认输入路径下存在受支持格式的文件。")
    unsupported = [f.name for f in files if f.suffix.lower() not in supported]
    if unsupported:
        raise UnsupportedFormatError(
            f"以下文件的格式不支持：{', '.join(unsupported)}"
            f"（可选：{', '.join(sorted(supported))}）"
        )
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="omnimd",
        description="文件转 Markdown：正文锚点，图片链接/内嵌/转写/描述按需组合",
    )
    parser.add_argument("inputs", nargs="+", help="输入文件或目录（支持多个）")
    parser.add_argument("-o", "--out-dir", default="docs", help="输出目录（缺省 docs/）")
    parser.add_argument("--output", default=None, metavar="body,url,ocr",
                        help="产出组成，逗号分隔多选（缺省读 OUTPUT，未设置为 body 纯解析）：body=正文（必选锚点，省略自动包含），url=图片公开链接，base64=图片内嵌（与 url 互斥），ocr=转写文本，alt=描述文本（ocr/alt 依赖 url 或 base64，需调用视觉模型，产生模型费用）")
    parser.add_argument("--concurrency", type=int, default=None, help="图片转写并发数（缺省读 VLM_CONCURRENCY，未配置为 8）")
    parser.add_argument("--cache-dir", default=None, help="缓存目录：转写/URL 缓存（缺省读 CACHE_DIR，未配置为系统用户缓存目录）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # 连接配置只读 .env / 环境变量；CLI 只调运行参数
    from .config import load
    try:
        config = load(
            concurrency=args.concurrency, cache_dir=args.cache_dir,
            output=args.output,
        )
    except OmniMDError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    try:
        files = _collect_inputs(args.inputs)
    except (OmniMDError, ValueError, FileNotFoundError) as e:
        print(f"输入错误：{e}", file=sys.stderr)
        return 2

    try:
        config.cache_dir.mkdir(parents=True, exist_ok=True)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"目录创建失败（缓存或输出目录不可写）：{e}", file=sys.stderr)
        return 2

    logger.info(
        "解析引擎=%s（PDF 固定 pymupdf4llm） | VLM=%s（思考=%s） | 存储=%s | 产出组成=%s | 并发=%d",
        config.doc_engine, config.vlm_model, config.vlm_thinking,
        config.storage_provider, config.output, config.vlm_concurrency,
    )

    from .converter import Converter
    converter = Converter(config)
    exit_code = 0
    assigned: dict[Path, Path] = {}   # 输入路径 → 产出路径（批内分配登记）
    for file_path in files:
        logger.info("开始转换: %s", file_path.name)
        try:
            result = converter.convert(file_path)
        except Exception:
            logger.exception("转换失败: %s", file_path.name)
            exit_code = 1
            continue
        target = _assign_target(out_dir, file_path, assigned)
        target.write_text(result.markdown, encoding="utf-8")
        logger.info(
            "已写入: %s（%d 字符，%d 图，%.1fs）",
            target, result.char_count, result.image_count, result.elapsed_ms / 1000,
        )
    return exit_code


def _assign_target(out_dir: Path, file_path: Path, assigned: dict[Path, Path]) -> Path:
    """产出路径分配：同批内输入路径唯一决定产出名。

    - 首见输入 → {stem}.md；不同输入撞名（a.pdf 与 a.docx）→ 后来者
      追加序号（{stem}-1.md），序号只消解批内碰撞；
    - 同一输入重复出现（路径相同）→ 复用首次分配的产出名，重复转换
      的结果覆盖写入，不产生 -1-2-3 连续序号副本。

    跨次运行的重跑语义由此天然成立：每次运行都是新分配表，同一输入
    再次运行仍拿 {stem}.md，覆盖即「刷新产出」。"""
    if file_path in assigned:
        return assigned[file_path]
    stem = file_path.stem
    taken = {t.name for t in assigned.values()}
    name = f"{stem}.md"
    if name in taken:
        n = 1
        while f"{stem}-{n}.md" in taken:
            n += 1
        name = f"{stem}-{n}.md"
    target = out_dir / name
    assigned[file_path] = target
    return target


if __name__ == "__main__":
    sys.exit(main())
