"""转换器：将各类文件转换为「正文 + 图片链接 + 图片转写文本」三合一的 Markdown

四步管线（每步职责单一）：
1. 解析——按扩展名路由到解析引擎（pdf 固定 pymupdf4llm；其余按 DOC_ENGINE 配置，
   缺省 markitdown，不支持的格式回退另一引擎）
2. 注册——图片统一注册到资产库：装饰图过滤、SHA-256 去重、占位符替换
3. 处理——并发上传 + VLM 转写（双层缓存，重跑不产生模型调用；产出不含
   转写与描述时止步于上传；base64 传图时视觉任务直接接收图片数据、跳过上传）
4. 渲染——占位符按产出组成（url=链接 / text=转写文本 / alt=描述文本）回填为图片块

并发模型：实例跨调用共享的只有不可变配置、存储客户端与（惰性构造的）
视觉模型客户端——三者皆线程安全（惰性构造在首次并发调用时可能重复执行，
结果一致，无正确性影响）；资产库随每次转换创建（图片字节不跨
转换滞留内存），解析引擎按线程隔离复用（第三方引擎的线程安全性不受
本工具控制）。同一实例可安全并发调用 convert。

产出组成是每次 convert 的参数（缺省取配置的 OUTPUT）：同一个转换器
实例可产出任意组成的 Markdown，视觉模型客户端惰性构造——首次遇到
含转写文本的转换才加载 openai SDK。
"""
import base64
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ConfigError, parse_output
from .engines import resolve_engine
from .images import ImageAsset, ImageAssetStore
from .storage import create_uploader

logger = logging.getLogger(__name__)

# data URI 图片（pymupdf4llm embed_images / markitdown keep_data_uris 输出）
_DATA_URI_PATTERN = re.compile(r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)')

# 图片块模板：URL 行（alt 是链接的描述文本）+ 可选的转写文本块
_URL_BLOCK_TEMPLATE = """![{alt}]({url})"""

# 仅转写文本块模板：内容可检索，不保留链接
_TEXT_ONLY_TEMPLATE = """{ocr_text}
"""


@dataclass(frozen=True)
class ImageInfo:
    """产出中一张图片的结构化元数据（程序消费方免解析 Markdown）

    字段值反映管线结果而非产出组成：url 为空表示未上传（base64 传图且
    产出不含链接）或上传失败；ocr_text / alt_text 为空表示本次产出
    组成不含该部件或转写失败。
    """

    sha256: str          # 内容哈希（同图去重的标识）
    url: str = ""        # 对象存储公开链接（未上传或上传失败时为空）
    alt_text: str = ""   # 链接描述文本（产出组成含 alt 时填充）
    ocr_text: str = ""   # 完整转写（产出组成含 text 时填充）


@dataclass(frozen=True)
class ConversionResult:
    """单文件转换结果（convert 的返回值）

    markdown 是产出全文；images 是每张图片的结构化元数据；其余字段
    是转换统计——调用方不必自行解析产出。
    """

    markdown: str             # 完整 Markdown 文本
    images: list[ImageInfo]   # 图片清单（元数据，与产出中的图片块一一对应）
    image_count: int          # 产出中的图片块数（链接或转写形态均计数）
    char_count: int           # 产出字符数
    elapsed_ms: int           # 转换耗时（毫秒）


# ========== 第二步：图片注册（过滤 + 去重），模块级纯函数 ==========
def register_images(markdown: str, store: ImageAssetStore) -> str:
    """把 markdown 中的 data URI 图片注册到资产库，替换为 sha256 占位符。
    引擎已直接注册的图片（@@IMG:{sha256}@@）与外链图原样保留；
    装饰性图片（注册返回 None）替换为空串。
    """

    def replace_data_uri(match: re.Match) -> str:
        data = base64.b64decode(match.group(3))
        asset = store.register(match.group(0)[:60], data)
        return f"@@IMG:{asset.sha256}@@" if asset else ""

    return _DATA_URI_PATTERN.sub(replace_data_uri, markdown)


# ========== 第四步：渲染图片块，模块级纯函数 ==========
def render_blocks(
    markdown: str, store: ImageAssetStore, parts: set[str]
) -> tuple[str, int]:
    """把 sha256 占位符按产出组成渲染为图片块，返回 (markdown, 图片块数)。

    parts 是 {url, text, alt} 的非空子集（alt 依赖 url 已在 parse 处校验）；
    alt 为空时回退「图片」。URL 部件要求链接——该图上传失败（无 URL）
    时丢弃；仅转写/描述形态不依赖 URL（base64 直传且不含 url 时全程不上传）。
    未知占位符替换为空串；装饰图丢弃后的连续空行统一压缩为单个空行。
    """
    count = 0

    def render(match: re.Match) -> str:
        nonlocal count
        sha = match.group(1)
        asset = store.lookup(sha)
        if asset is None:
            return ""
        if "url" in parts and not asset.url:
            return ""
        count += 1
        ocr_text = asset.ocr_text or "（图片转写失败）"
        if "url" not in parts:
            return _TEXT_ONLY_TEMPLATE.format(ocr_text=ocr_text)
        block = _URL_BLOCK_TEMPLATE.format(alt=asset.alt_text or "图片", url=asset.url)
        if "text" in parts:
            block += f"\n{ocr_text}\n"
        return block

    markdown = re.sub(r"@@IMG:([0-9a-f]{64})@@", render, markdown)
    # 丢弃的装饰图与占位替换会留下连续空行，压缩为单个空行；
    # 围栏代码块整体跳过——代码内容逐字保留，不做改写
    return _collapse_blank_lines(markdown), count


def _collapse_blank_lines(markdown: str) -> str:
    """连续空行压缩为单个空行，围栏代码块（``` / ~~~）内的行原样保留。"""
    result: list[str] = []
    blank_run = 0
    in_fence = False
    fence_marker = ""
    for line in markdown.split("\n"):
        stripped = line.strip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            fence_marker = stripped[:3]
            blank_run = 0
        elif in_fence and stripped.startswith(fence_marker):
            in_fence = False
            blank_run = 0
        if in_fence or stripped:
            result.append(line)
            blank_run = 0
        else:
            blank_run += 1
            if blank_run == 1:
                result.append(line)
    return "\n".join(result)


class Converter:
    def __init__(self, config: Config):
        self.config = config
        # 存储客户端按需构造：产出组成含 url 时才需要（仅转写文本可不配存储）
        self._uploader = None
        self._vlm = None       # 视觉模型客户端（惰性构造，见 vision 属性）
        self._engines = threading.local()  # 线程内复用的引擎实例（见 _get_engine）

    @property
    def uploader(self):
        """存储客户端——首次需要上传时才构造；缺存储配置给出指引性报错"""
        if self._uploader is None:
            if not self.config.storage_provider:
                raise ConfigError(
                    "本次转换需要文件存储（产出图片链接，或按 URL 方式向视觉模型"
                    "传图），但 STORAGE_PROVIDER 未配置。请在 .env 中配置存储"
                    "（S3_*），或改用 base64 直传"
                    "（VLM_IMAGE_INPUT=base64）并仅产出转写文本（output=text）。"
                )
            self._uploader = create_uploader(self.config)
        return self._uploader

    @property
    def vision(self):
        """视觉模型客户端——首次转写时才构造，产出不含转写文本的转换不加载 openai SDK"""
        if self._vlm is None:
            from .vlm import create_vlm
            self._vlm = create_vlm(self.config)
        return self._vlm

    # ========== 对外入口 ==========
    def convert(self, file_path: Path, output: str | None = None) -> ConversionResult:
        """转换单个文件。

        output 是本次转换的产出组成（逗号分隔多选 url/text/alt，
        alt 依赖 url），未设置时取配置的 OUTPUT。
        """
        started = time.perf_counter()
        parts = set(parse_output(output or self.config.output).split(","))

        # 产出含图片链接时先确认存储可用（缺配置在拿到文件前报错，
        # 而不是解析完整个文件后才失败）；base64 直传不需要上传
        base64_input = self.config.vlm_image_input == "base64"
        need_upload = "url" in parts or (
            ("text" in parts or "alt" in parts) and not base64_input
        )
        uploader = self.uploader if need_upload else None
        # 资产库随本次转换创建：同文件去重走内存，跨文件去重走双层磁盘缓存
        store = ImageAssetStore(self.config, self.config.cache_dir, uploader)
        engine = self._get_engine(file_path.suffix.lower())

        markdown = engine.convert(file_path, store)
        markdown = register_images(markdown, store)
        self._process_assets(store, parts, need_upload)
        markdown, image_count = render_blocks(markdown, store, parts)

        images = [
            ImageInfo(
                sha256=a.sha256,
                url=a.url,
                alt_text=a.alt_text,
                ocr_text=a.ocr_text,
            )
            for a in store.all_assets()
        ]
        return ConversionResult(
            markdown=markdown,
            images=images,
            image_count=image_count,
            char_count=len(markdown),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def _get_engine(self, suffix: str):
        """解析引擎类并返回本线程的实例（构造有成本，线程内复用）。

        第三方引擎实例的线程安全性不受本工具控制，按线程隔离——
        CLI 单线程全程一个实例，demo 的工作线程各得其一。
        """
        engine_cls = resolve_engine(suffix, self.config.doc_engine)
        cache = getattr(self._engines, "by_cls", None)
        if cache is None:
            cache = self._engines.by_cls = {}
        engine = cache.get(engine_cls)
        if engine is None:
            engine = cache[engine_cls] = engine_cls()
        return engine

    # ========== 第三步：上传 + 转写（并发） ==========
    def _process_assets(self, store: ImageAssetStore, parts: set[str],
                        need_upload: bool) -> None:
        assets = store.all_assets()
        logger.info("待处理图片 %d 张（已去重，产出组成=%s）",
                    len(assets), ",".join(sorted(parts)))
        if not assets:
            return
        # 上传与转写同为 IO 密集，走同一并发框架——两段式（全部上传 →
        # 全部转写）：渲染需要链接（url 部件）或视觉任务走 URL 传输时上传，
        # need_upload 由 convert 按同一逻辑判定后传入
        if need_upload:
            self._upload_all(store, assets)
        need_text = "text" in parts
        need_alt = "alt" in parts
        if not need_text and not need_alt:
            return

        # 视觉任务的图片引用与渲染链接分离：base64 传输给 data URI，
        # URL 传输给公开链接（上传阶段已完成）
        base64_input = self.config.vlm_image_input == "base64"

        def image_ref(asset: ImageAsset) -> str:
            return asset.data_uri() if base64_input else asset.url

        vision = self.vision   # 在调用线程先构造，线程池内只读

        def work(asset: ImageAsset) -> None:
            # URL 传图模式下上传失败的图没有可用的 ref，跳过转写（该图走失败渲染）
            if not base64_input and not asset.url:
                return
            if need_text:
                cached = store.get_cached_text(asset.sha256, "ocr")
                if cached is not None:
                    asset.ocr_text = cached
                else:
                    asset.ocr_text = vision.ocr(image_ref(asset))
                    # 空结果不落缓存：成功转写必非空（无文字的图片也输出一行描述），
                    # 空串即失败产物——缓存固化失败，重跑无法重试
                    if asset.ocr_text:
                        store.save_cached_text(asset.sha256, "ocr", asset.ocr_text)
            if need_alt:
                cached = store.get_cached_text(asset.sha256, "alt")
                if cached is not None:
                    asset.alt_text = cached
                else:
                    asset.alt_text = vision.describe(image_ref(asset))
                    if asset.alt_text:
                        store.save_cached_text(asset.sha256, "alt", asset.alt_text)

        with ThreadPoolExecutor(max_workers=self.config.vlm_concurrency) as pool:
            futures = {pool.submit(work, a): a for a in assets}
            done = 0
            for future in as_completed(futures):
                asset = futures[future]
                done += 1
                try:
                    future.result()
                    logger.info("[%d/%d] 图片处理完成: %s", done, len(assets), asset.sha256[:12])
                except Exception:
                    logger.exception("图片处理失败: sha=%s", asset.sha256[:12])
                    # 只清理失败子任务：另一子任务已成功（含缓存命中）的结果不覆盖
                    if need_text and not asset.ocr_text:
                        asset.ocr_text = "（图片转写失败）"
                    if need_alt:
                        asset.alt_text = ""   # 渲染回退「图片」

    def _upload_all(self, store: ImageAssetStore, assets: list[ImageAsset]) -> None:
        """并发上传全部资产；单张失败不中断（该图渲染为失败占位）。"""
        failed: list[ImageAsset] = []

        def work(asset: ImageAsset) -> None:
            store.ensure_uploaded(asset)

        with ThreadPoolExecutor(max_workers=self.config.vlm_concurrency) as pool:
            futures = {pool.submit(work, a): a for a in assets}
            for future in as_completed(futures):
                asset = futures[future]
                try:
                    future.result()
                except Exception:
                    logger.exception("图片上传失败: sha=%s", asset.sha256[:12])
                    failed.append(asset)
        for asset in failed:
            asset.url = ""   # 上传失败：渲染阶段丢弃该图
