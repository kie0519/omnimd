"""转换器：将各类文件转换为 Markdown（正文锚点 + 图片部件按需组合）

四步管线（每步职责单一）：
1. 解析——按扩展名路由到解析引擎（pdf 固定 pymupdf4llm；其余按 DOC_ENGINE 配置，
   缺省 markitdown，不支持的格式回退另一引擎）
2. 注册——图片统一注册到资产库：装饰图过滤、SHA-256 去重、占位符替换
3. 处理——并发上传 + VLM 转写（双层缓存，重跑不产生模型调用；产出不含
   ocr/alt 时止步于上传；纯解析时本步整体跳过）
4. 渲染——占位符按产出组成（url=链接 / base64=内嵌 / ocr=转写文本 /
   alt=描述文本）回填为图片块；纯解析时图片块移除

VLM 取图方式由产出组成推导（无配置项）：含 url 部件传公开 URL（图片
已上传），否则传 base64（字节本地持有，天然兼容本地端点）。

并发模型：实例跨调用共享的只有不可变配置、存储客户端与（惰性构造的）
视觉模型客户端——三者皆线程安全（惰性构造在首次并发调用时可能重复执行，
结果一致，无正确性影响）；资产库随每次转换创建（图片字节不跨
转换滞留内存），解析引擎按线程隔离复用（第三方引擎的线程安全性不受
本工具控制）。同一实例可安全并发调用 convert。

产出组成是每次 convert 的参数（缺省取配置的 OUTPUT，body 纯解析）：
同一个转换器实例可产出任意组成的 Markdown，视觉模型客户端惰性构造——
首次遇到含 ocr/alt 的转换才加载 openai SDK。
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

# 图片引用行模板（alt=描述文本，url=公开链接或 data URI）；
# 转写文本块由渲染代码按产出组成追加
_URL_BLOCK_TEMPLATE = """![{alt}]({url})"""


@dataclass(frozen=True)
class ImageInfo:
    """产出中一张图片的结构化元数据（程序消费方免解析 Markdown）

    字段值反映管线结果而非产出组成：url 为空表示未上传（base64 传图且
    产出不含链接）或上传失败；ocr_text / alt_text 为空表示本次产出
    组成不含该部件或转写失败。
    """

    sha256: str          # 内容哈希（同图去重的标识）
    url: str = ""        # 对象存储公开链接（未上传或上传失败时为空）
    alt_text: str = ""   # 链接描述文本（产出组成含 alt 时填充；生成失败为空，渲染回退「图片」）
    ocr_text: str = ""   # 完整转写（产出组成含 ocr 时填充；转写失败为「（图片转写失败）」标记串）


@dataclass(frozen=True)
class ConversionResult:
    """单文件转换结果（convert 的返回值）

    markdown 是产出全文；images 是每张图片的结构化元数据；其余字段
    是转换统计——调用方不必自行解析产出。
    """

    markdown: str             # 完整 Markdown 文本
    images: list[ImageInfo]   # 图片清单（元数据，与产出中的图片块一一对应）
    image_count: int          # 产出中的图片块数（链接或内嵌形态均计数；纯解析为 0）
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
) -> tuple[str, list[ImageAsset]]:
    """把 sha256 占位符按产出组成渲染为图片块，返回 (markdown, 渲染块清单)。

    parts 是 {body, url, base64, ocr, alt} 的组合（body 锚点；url 与
    base64 互斥、ocr/alt 依赖其一，已在 parse 处校验）。alt 为空时回退
    「图片」。纯解析（仅 body）时图片块移除；url 部件要求链接——该图
    上传失败（无 URL）时丢弃；base64 部件用本地字节渲染 data URI。
    未知占位符替换为空串；装饰图丢弃后的连续空行统一压缩为单个空行。
    返回的清单与产出中的图片块一一对应（ConversionResult.images 的来源）。
    """
    rendered: list[ImageAsset] = []

    def render(match: re.Match) -> str:
        sha = match.group(1)
        asset = store.lookup(sha)
        if asset is None:
            return ""
        has_url = "url" in parts
        has_base64 = "base64" in parts
        # 无呈现部件（纯解析）或 url 部件但上传失败：图片块移除
        if (not has_url and not has_base64) or (has_url and not asset.url):
            return ""
        rendered.append(asset)
        ocr_text = asset.ocr_text or "（图片转写失败）"
        ref = asset.url if has_url else asset.data_uri()
        block = _URL_BLOCK_TEMPLATE.format(
            alt=asset.alt_text or "图片", url=ref
        )
        if "ocr" in parts:
            block += f"\n{ocr_text}\n"
        return block

    markdown = re.sub(r"@@IMG:([0-9a-f]{64})@@", render, markdown)
    # 丢弃的装饰图与占位替换会留下连续空行，压缩为单个空行；
    # 围栏代码块整体跳过——代码内容逐字保留，不做改写
    return _collapse_blank_lines(markdown), rendered


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
                    "本次转换需要文件存储（产出含 url 部件），但 "
                    "STORAGE_PROVIDER 未配置。请在 .env 中配置存储（S3_*），"
                    "或改用 base64 部件（output=body,base64,ocr）——图片内嵌"
                    "产出、VLM 以 base64 直传，不依赖存储。"
                )
            self._uploader = create_uploader(self.config)
        return self._uploader

    @property
    def vision(self):
        """视觉模型客户端——首次转写时才构造并校验凭据（产出不含
        ocr/alt 的转换不校验凭据、不加载 openai SDK）"""
        if self._vlm is None:
            self.config.validate_vlm()
            from .vlm import create_vlm
            self._vlm = create_vlm(self.config)
        return self._vlm

    # ========== 对外入口 ==========
    def convert(self, file_path: Path, output: str | None = None) -> ConversionResult:
        """转换单个文件。

        output 是本次转换的产出组成（逗号分隔多选 body/url/base64/
        ocr/alt；body 必选锚点，url 与 base64 互斥，ocr/alt 依赖其一），
        未设置时取配置的 OUTPUT（缺省 body 纯解析）。
        """
        started = time.perf_counter()
        parts = set(parse_output(output or self.config.output).split(","))

        # 上传只发生在产出含 url 部件时（先确认存储可用——缺配置在拿到
        # 文件前报错，而不是解析完整个文件后才失败）；纯解析与 base64
        # 内嵌不接触存储
        need_upload = "url" in parts
        need_vlm = "ocr" in parts or "alt" in parts
        uploader = self.uploader if need_upload else None
        # 资产库随本次转换创建：同文件去重走内存，跨文件去重走双层磁盘缓存
        store = ImageAssetStore(self.config, self.config.cache_dir, uploader)
        engine = self._get_engine(file_path.suffix.lower())

        markdown = engine.convert(file_path, store)
        markdown = register_images(markdown, store)
        if need_upload or need_vlm:
            self._process_assets(store, parts)
        markdown, rendered = render_blocks(markdown, store, parts)

        images = [
            ImageInfo(
                sha256=a.sha256,
                url=a.url,
                alt_text=a.alt_text,
                ocr_text=a.ocr_text,
            )
            for a in rendered
        ]
        return ConversionResult(
            markdown=markdown,
            images=images,
            image_count=len(rendered),
            char_count=len(markdown),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def _get_engine(self, suffix: str):
        """解析引擎类并返回本线程的实例（构造有成本，线程内复用）。

        第三方引擎实例的线程安全性不受本工具控制，按线程隔离。
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
    def _process_assets(self, store: ImageAssetStore, parts: set[str]) -> None:
        assets = store.all_assets()
        logger.info("待处理图片 %d 张（已去重，产出组成=%s）",
                    len(assets), ",".join(sorted(parts)))
        if not assets:
            return
        # 上传与转写同为 IO 密集，走同一并发框架——两段式（全部上传 →
        # 全部转写）：上传只发生在产出含 url 部件时（渲染与传图都需要
        # 公开链接）；纯 base64 产出无上传阶段
        if "url" in parts:
            self._upload_all(store, assets)
        need_ocr = "ocr" in parts
        need_alt = "alt" in parts
        if not need_ocr and not need_alt:
            return

        # VLM 取图方式由产出组成推导：含 url 部件传公开链接（上传阶段
        # 已完成），否则传 base64 data URI（字节本地持有）
        via_url = "url" in parts

        def image_ref(asset: ImageAsset) -> str:
            return asset.url if via_url else asset.data_uri()

        vision = self.vision   # 在调用线程先构造，线程池内只读

        def work(asset: ImageAsset) -> None:
            # URL 传图时上传失败的图没有可用 ref，跳过转写（该图走失败渲染）
            if via_url and not asset.url:
                return
            if need_ocr:
                cached = store.get_cached_text(asset.sha256, "ocr")
                if cached is not None:
                    asset.ocr_text = cached
                else:
                    raw = vision.ocr(image_ref(asset))
                    # 空结果不落缓存：成功转写必非空（无文字的图片也输出一行描述），
                    # 空串即失败产物——缓存固化失败，重跑无法重试
                    if raw:
                        asset.ocr_text = raw
                        store.save_cached_text(asset.sha256, "ocr", raw)
                    else:
                        # 空内容视同失败：标记串统一字段取值（程序消费方凭
                        # 「（图片转写失败）」判失败，不以空串混淆「未产出」）
                        asset.ocr_text = "（图片转写失败）"
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
                    # 空内容与异常同走此路，不做重试：思考链已消耗本次输出预算，
                    # 重试大概率得到同样结果
                    # 只清理失败子任务：另一子任务已成功（含缓存命中）的结果不覆盖
                    if need_ocr and not asset.ocr_text:
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
