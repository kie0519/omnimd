"""omnimd 命令行入口

用法：
    omnimd <文件或目录> [选项]

命令行参数只决定产出内容与运行项（输出目录 / 图片处理 / 并发 / 缓存目录 / 调试日志）；
连接配置（VLM 凭据、对象存储）在 .env 中给定。不带图片参数时：文档仅提取正文
（不需要凭据、不产生费用），图片文件自动完整转写。

示例：
    omnimd <文件路径>.pdf -o output/
    omnimd <文件路径>.pdf -o output/ --images link --transcribe
    omnimd <输入目录>/ -o output/ --images embed --prompt walk
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
            # 跳过点前缀隐藏文件（.DS_Store / desktop.ini 等系统杂质），不让单个杂质文件废掉整批转换；可见文件仍显式报错
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
    from . import __version__
    parser = argparse.ArgumentParser(
        prog="omnimd",
        description="文件转 Markdown：正文逐字保留，图片可链接或内嵌，可转写、可自定义分析",
    )
    parser.add_argument("--version", action="version",
                        version=f"omnimd {__version__}")
    parser.add_argument("inputs", nargs="+", help="输入文件或目录（支持多个）")
    parser.add_argument("-o", "--out-dir", default="docs", help="输出目录（缺省 docs/）")
    parser.add_argument("--images", choices=["link", "embed"], default=None,
                        help="图片处理方式：link=公开链接（需存储配置），embed=base64 内嵌自包含；不传 = 不保留图片，只要文字")
    parser.add_argument("--transcribe", action="store_true",
                        help="完整转写每张图片——图片全部信息的 Markdown（等价 --prompt transcribe）")
    parser.add_argument("--describe", action="store_true",
                        help="一句话描述填入图片链接描述位（等价 --prompt describe；需 --images）")
    parser.add_argument("--prompt", action="append", default=None,
                        metavar="<提示词或任务名>",
                        help="以提示词驱动图片任务：值是任务名（内置 transcribe / describe，或工作目录 prompts/ 下的文件名）时引用该任务，否则作为提示词文本即时生效；可重复")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="图片任务并发数（缺省读 VLM_CONCURRENCY，未配置为 8）")
    parser.add_argument("--cache-dir", default=None,
                        help="任务产物/URL 缓存目录（相对路径相对当前目录；缺省读 .env 的 CACHE_DIR，未配置为系统用户缓存目录）")
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
        )
    except OmniMDError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    try:
        files = _collect_inputs(args.inputs)
    except (OmniMDError, ValueError, FileNotFoundError) as e:
        print(f"输入错误：{e}", file=sys.stderr)
        return 2

    task_names = (
        (["transcribe"] if args.transcribe else [])
        + (["describe"] if args.describe else [])
        + list(args.prompt or [])
    )
    # 参数编译期前置校验：呈现与任务的组合错误在启动期报出，不进逐文件转换循环
    from .converter import Converter, compile_plan
    from .prompts import build_registry
    try:
        registry = build_registry(workdir=Path.cwd())
        compile_plan(registry, images=args.images,
                     transcribe=args.transcribe, describe=args.describe,
                     prompts=tuple(args.prompt or ()))
    except OmniMDError as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    # 输出目录不可写是硬失败（产出无处落盘）；缓存目录由库在首次写入时创建，
    # 不可写只警告并停用缓存（转换不受影响），不在启动期预建
    try:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"输出目录创建失败（不可写）：{e}", file=sys.stderr)
        return 2
    logger.info(
        "解析引擎=%s（PDF 固定 pymupdf4llm） | VLM=%s（思考=%s，超时=%.0fs） | 存储=%s | 缓存=%s | 图片=%s | 任务=%s | 并发=%d",
        config.doc_engine, config.vlm_model, config.vlm_thinking,
        config.vlm_timeout,
        config.s3_endpoint_url or "未配置",
        config.cache_dir.resolve(),
        args.images or "不保留",
        ",".join(task_names) or "按输入类型", config.vlm_concurrency,
    )

    converter = Converter(config, registry)
    exit_code = 0
    assigned: dict[Path, Path] = {}
    for file_path in files:
        logger.info("开始转换: %s", file_path.name)
        try:
            result = converter.convert(
                file_path, images=args.images,
                transcribe=args.transcribe, describe=args.describe,
                prompt=args.prompt,
            )
        except Exception:
            logger.exception("转换失败: %s", file_path.name)
            exit_code = 1
            continue
        target = _assign_target(out_dir, file_path, assigned)
        target.write_text(result.markdown, encoding="utf-8")
        usage_note = ""
        if result.usage is not None:
            usage_note = f"，tokens={result.usage.total_tokens}"
        logger.info(
            "已写入: %s（%d 字符，%d 图%s，%.1fs）",
            target, result.char_count, result.image_count, usage_note,
            result.elapsed_ms / 1000,
        )
    return exit_code


def _assign_target(out_dir: Path, file_path: Path, assigned: dict[Path, Path]) -> Path:
    """产出路径分配：同批内输入路径唯一决定产出名。

    - 首见输入 → {stem}.md；不同输入撞名（a.pdf 与 a.docx）→ 后来者追加序号（{stem}-1.md），序号只消解批内碰撞；
    - 同一输入重复出现（路径相同）→ 复用首次分配的产出名，重复转换的结果覆盖写入，不产生 -1-2-3 连续序号副本。

    跨次运行的重跑语义由此天然成立：每次运行都是新分配表，同一输入再次运行仍拿 {stem}.md，覆盖即「刷新产出」。"""
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
