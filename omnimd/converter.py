"""转换器：将各类文件转换为 Markdown

五步管线（编译先行——convert 先编译参数再进管线，每步职责单一）：
1. 编译——CLI/库参数 → PipelinePlan（呈现值 + 任务集，纯函数）；类型路由缺省：图片文件且无任务动词 → 注入完整转写
2. 解析——按扩展名路由到解析引擎（pdf 固定 pymupdf4llm；图片直转；其余按 DOC_ENGINE 配置，不支持的格式由另一引擎处理）
3. 注册——图片统一注册到资产库：装饰图过滤、SHA-256 去重、占位符替换
4. 处理——并发上传 + 按任务集调用视觉模型（双层缓存，重跑不产生模型调用；无任务时止步于上传；无呈现无任务时本步整体跳过）
5. 渲染——占位符按呈现值与任务集回填：图片块（link/embed）或任务文本块（drop 时任务产物占图片原位）

参数编译层（compile_plan，纯函数）：CLI / 库参数 → 呈现值 + 任务集。
语法糖等价由结构保证——--transcribe 与 --prompt transcribe 编译为同一 TaskSpec。
类型路由缺省：图片文件且未选图片任务 → 自动完整转写（转写就是图片的「解析」；
默认行为的唯一决定因素是输入类型）。
同一实例可安全并发调用 convert。
"""
import base64
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import Config, ConfigError
from .engines import IMAGE_EXTENSIONS, resolve_engine
from .images import ImageAsset, ImageAssetStore
from .prompts import (
    TaskRegistry,
    TaskSpec,
    effective_system,
    failure_note,
)
from .storage import create_uploader

logger = logging.getLogger(__name__)

# data URI 图片（pymupdf4llm embed_images / markitdown keep_data_uris 输出）
_DATA_URI_PATTERN = re.compile(r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)')

# 图片引用行模板（alt=描述位文本，url=公开链接或 data URI）；任务文本块由渲染代码按任务集追加
_URL_BLOCK_TEMPLATE = """![{alt}]({url})"""

# 呈现值域：drop=不保留图片（缺省）；link=公开链接；embed=base64 内嵌
_PRESENTATIONS = ("drop", "link", "embed")


# ========== 参数编译层（纯函数） ==========
@dataclass(frozen=True)
class PipelinePlan:
    """管线输入：呈现值 + 任务集——CLI 与库参数的编译产物"""

    presentation: str            # drop | link | embed
    tasks: tuple[TaskSpec, ...]  # 按声明顺序执行与渲染


def _normalize_prompts(prompt) -> tuple[str | TaskSpec, ...]:
    """prompt 参数归一：None → ()；str / TaskSpec → 单元组；列表混排原样收"""
    if prompt is None:
        return ()
    if isinstance(prompt, (str, TaskSpec)):
        return (prompt,)
    return tuple(prompt)


def compile_plan(
    registry: TaskRegistry,
    *,
    images: str | None = None,
    transcribe: bool = False,
    describe: bool = False,
    prompts: tuple[str | TaskSpec, ...] = (),
) -> PipelinePlan:
    """编译 CLI / 库参数为管线输入（纯函数，可独立单测）。

    - 呈现：images 取 link / embed，不传 = drop；
    - 任务：--transcribe / --describe 是语法糖（引用注册表同名任务）；
      prompts 值命中注册表 → 任务引用，否则 → 即时任务（提示词本体即任务）。
    - describe 的说明文字写在图片链接里，无保留方式（drop）时报错指引。
    """
    presentation = images or "drop"
    if presentation not in _PRESENTATIONS:
        raise ConfigError(
            f"不支持的 images 取值：{images!r}"
            f"（可选：link、embed；不传 = 丢弃图片只要文字）"
        )
    tasks: list[TaskSpec] = []
    if transcribe:
        tasks.append(registry["transcribe"])
    if describe:
        tasks.append(registry["describe"])
    for item in prompts:
        if isinstance(item, TaskSpec):
            tasks.append(item)
            continue
        value = item.strip()
        if not value:
            raise ConfigError("--prompt 的值不能为空（提示词或任务名）")
        if value in registry:
            tasks.append(registry[value])
        else:
            # 即时任务：名从内容稳定派生（一处定义于 registry——编译层与集成方共用）
            from .prompts import adhoc_task_name
            tasks.append(TaskSpec(name=adhoc_task_name(value), prompt=value))
    if any(t.render == "attribute" for t in tasks) and presentation == "drop":
        raise ConfigError(
            "--describe 的说明文字写在图片链接里，要先保留图片——"
            "加 --images link 或 --images embed"
        )
    return PipelinePlan(presentation=presentation, tasks=tuple(tasks))


# ========== 结果结构 ==========
@dataclass(frozen=True)
class ImageInfo:
    """产出中一张图片的结构化元数据（程序消费方免解析 Markdown）

    texts 按任务名给出各任务产物；字段值反映管线结果而非参数面。
    """

    sha256: str                          # 内容哈希（同图去重的标识）
    url: str = ""                        # 对象存储公开链接（未上传或上传失败时为空）
    texts: dict = field(default_factory=dict)  # {任务名: 产物}；describe 产物即描述位文本


@dataclass(frozen=True)
class ConversionResult:
    """单文件转换结果（convert 的返回值）

    markdown 是产出全文；images 是每张图片的结构化元数据；usage 是本次转换
    的视觉模型 token 用量（无调用时为 None）；其余字段是转换统计。
    """

    markdown: str             # 完整 Markdown 文本
    images: list[ImageInfo]   # 图片清单（元数据，与产出中的图片块一一对应）
    image_count: int          # 产出中的图片块数（链接或内嵌形态均计数；drop 为 0）
    char_count: int           # 产出字符数
    elapsed_ms: int           # 转换耗时（毫秒）
    usage: object = None      # vlm.Usage | None（成本可见性）


# ========== 第二步：图片注册（过滤 + 去重），模块级纯函数 ==========
def register_images(markdown: str, store: ImageAssetStore) -> str:
    """把 markdown 中的 data URI 图片注册到资产库，替换为 sha256 占位符。
    引擎已直接注册的图片（@@IMG:{sha256}@@）与外链图原样保留；装饰性图片（注册返回 None）替换为空串。
    """

    def replace_data_uri(match: re.Match) -> str:
        data = base64.b64decode(match.group(3))
        asset = store.register(match.group(0)[:60], data)
        return f"@@IMG:{asset.sha256}@@" if asset else ""

    return _DATA_URI_PATTERN.sub(replace_data_uri, markdown)


# ========== 第四步：渲染图片块，模块级纯函数 ==========
def render_blocks(
    markdown: str, store: ImageAssetStore, plan: PipelinePlan,
) -> tuple[str, list[ImageAsset]]:
    """把 sha256 占位符按呈现值与任务集渲染，返回 (markdown, 渲染块清单)。

    - drop：图片块移除；block 型任务产物独立成段占图片原位（纯解析无任务时产出无图片痕迹）；
    - link / embed：图片块 ![描述](ref)；attribute 型任务产物（describe）填描述位，
      空值回落「图片」；block 型任务产物依次跟在图片块后；
    - link 且上传失败（无 URL）：该图整块丢弃。
    任务产物渲染无任何标签——它是正文流的一段，多任务并存靠内容自身区分。
    未知占位符替换为空串；装饰图丢弃后的连续空行统一压缩为单个空行。
    返回的清单与产出中的图片块一一对应（ConversionResult.images 的来源）。
    """
    block_tasks = tuple(t for t in plan.tasks if t.render == "block")
    attr_tasks = tuple(t for t in plan.tasks if t.render == "attribute")
    rendered: list[ImageAsset] = []

    def render(match: re.Match) -> str:
        sha = match.group(1)
        asset = store.lookup(sha)
        if asset is None:
            return ""
        if plan.presentation == "drop":
            # 图片不保留；任务产物占图片原位（无任务 = 纯解析，图片痕迹移除）
            return "\n".join(
                asset.texts.get(t.name, "") for t in block_tasks
            )
        if plan.presentation == "link" and not asset.url:
            return ""
        rendered.append(asset)
        attr_value = "".join(
            asset.texts.get(t.name, "") for t in attr_tasks
        )
        ref = asset.url if plan.presentation == "link" else asset.data_uri()
        block = _URL_BLOCK_TEMPLATE.format(
            alt=attr_value or "图片", url=ref
        )
        for t in block_tasks:
            block += f"\n{asset.texts.get(t.name, '')}\n"
        return block

    markdown = re.sub(r"@@IMG:([0-9a-f]{64})@@", render, markdown)
    # 丢弃的装饰图与占位替换会留下连续空行，压缩为单个空行；围栏代码块整体跳过——代码内容逐字保留，不做改写
    return _collapse_blank_lines(markdown), rendered


def _collapse_blank_lines(markdown: str) -> str:
    """连续空行压缩为单个空行，围栏代码块（``` / ~~~）内的行原样保留。"""
    result: list[str] = []
    blank_run = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in markdown.split("\n"):
        stripped = line.strip()
        # 开围栏可带 info string（```python）；闭围栏必须是纯标记行且长度
        # 不小于开标记（CommonMark 规则——外层 ```` 包裹内层 ``` 示例时不误闭合）
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            fence_char = stripped[0]
            fence_len = len(re.match(r"^(`{3,}|~{3,})", stripped).group(1))
            blank_run = 0
        elif (in_fence and len(stripped) >= fence_len
              and re.fullmatch(rf"{fence_char}{{3,}}", stripped)):
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
    def __init__(self, config: Config, registry: TaskRegistry):
        self.config = config
        self.registry = registry
        # 存储客户端按需构造
        self._uploader = None
        self._vlm = None       # 视觉模型客户端（惰性构造，见 vision 属性）
        self._engines = threading.local()  # 线程内复用的引擎实例（见 _get_engine）
        # token 用量累计（跨 convert 累计，read 属性供上层汇总）
        self._usage_lock = threading.Lock()
        self._usage_totals = {"prompt": 0, "completion": 0, "total": 0}

    @property
    def uploader(self):
        """存储客户端——首次需要上传时才构造；未配置存储给出指引性报错"""
        if self._uploader is None:
            if not self.config.s3_endpoint_url:
                raise ConfigError(
                    "本次转换需要对象存储（--images link），但存储未配置"
                    "（S3_ENDPOINT_URL 为空）。请在 .env 中配齐 S3_* 五项"
                    "（S3 兼容端点），或改用 --images embed（图片内嵌产出、"
                    "VLM 直传 base64，不依赖存储）。"
                )
            self._uploader = create_uploader(self.config)
        return self._uploader

    @property
    def vision(self):
        """视觉模型客户端——首次调用图片任务时才构造并校验凭据（无图片任务的转换不校验凭据、不加载 openai SDK）"""
        if self._vlm is None:
            self.config.validate_vlm()
            from .vlm import create_vlm
            self._vlm = create_vlm(self.config)
        return self._vlm

    def _usage_snapshot(self) -> dict[str, int]:
        """当前累计用量的快照（convert 前后各取一次，差值即本次用量）"""
        with self._usage_lock:
            return dict(self._usage_totals)

    # ========== 对外入口 ==========
    def convert(
        self,
        file_path: Path,
        *,
        images: str | None = None,
        transcribe: bool = False,
        describe: bool = False,
        prompt=None,
    ) -> ConversionResult:
        """转换单个文件。

        images 决定图片怎么保留（link / embed；不传 = 丢弃图片）；
        transcribe / describe 是内置图片任务（--prompt transcribe / describe 的快捷写法）；
        prompt 接受任务名、即时提示词文本、TaskSpec 或它们的列表。
        不带图片参数时：图片文件自动完整转写，文档只提取正文（绝不注入额外任务）。
        """
        started = time.perf_counter()
        usage_before = self._usage_snapshot()
        plan = compile_plan(
            self.registry, images=images, transcribe=transcribe,
            describe=describe, prompts=_normalize_prompts(prompt),
        )
        # 类型路由缺省：图片文件没有「不处理」的退路（像素里没有可提取文字），
        # 转写就是图片的解析——自动注入完整转写；文档文件绝不注入
        if not plan.tasks and file_path.suffix.lower() in IMAGE_EXTENSIONS:
            plan = replace(plan, tasks=(self.registry["transcribe"],))

        # 上传只发生在呈现为 link 时；drop 与 embed 不接触存储
        need_upload = plan.presentation == "link"
        uploader = self.uploader if need_upload else None
        # 资产库随本次转换创建：同文件去重走内存，跨文件去重走双层磁盘缓存
        store = ImageAssetStore(
            self.config, self.config.cache_dir, uploader, tasks=plan.tasks,
        )
        engine = self._get_engine(file_path.suffix.lower())

        markdown = engine.convert(file_path, store)
        markdown = register_images(markdown, store)
        if need_upload or plan.tasks:
            self._process_assets(store, plan)
        markdown, rendered = render_blocks(markdown, store, plan)

        images_meta = [
            ImageInfo(sha256=a.sha256, url=a.url, texts=dict(a.texts))
            for a in rendered
        ]
        after = self._usage_snapshot()
        delta = {k: after[k] - usage_before[k] for k in after}
        usage = None
        if delta["total"] > 0:
            from .vlm.base import Usage
            usage = Usage(
                prompt_tokens=delta["prompt"],
                completion_tokens=delta["completion"],
                total_tokens=delta["total"],
            )
        return ConversionResult(
            markdown=markdown,
            images=images_meta,
            image_count=len(rendered),
            char_count=len(markdown),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
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

    # ========== 第三步：上传 + 按任务集调用视觉模型（并发） ==========
    def _process_assets(self, store: ImageAssetStore, plan: PipelinePlan) -> None:
        assets = store.all_assets()
        logger.info(
            "待处理图片 %d 张（已去重，呈现=%s，任务=%s）",
            len(assets), plan.presentation,
            ",".join(t.name for t in plan.tasks) or "无",
        )
        if not assets:
            return
        # 两段式（全部上传 → 全部任务）：上传只发生在呈现为 link 时；drop 与 embed 无上传阶段
        if plan.presentation == "link":
            self._upload_all(store, assets)
        if not plan.tasks:
            return

        # VLM 取图方式由呈现值推导：link 传公开链接（上传阶段已完成），drop/embed 传 base64 data URI（字节本地持有）
        via_url = plan.presentation == "link"

        def image_ref(asset: ImageAsset) -> str:
            return asset.url if via_url else asset.data_uri()

        vision = self.vision   # 在调用线程先构造，线程池内只读
        config = self.config

        def work(asset: ImageAsset) -> None:
            # URL 传图时上传失败的图没有可用 ref，跳过任务（该图走失败渲染）
            if via_url and not asset.url:
                return
            for spec in plan.tasks:
                cached = store.get_cached_text(spec, asset.sha256)
                if cached is not None:
                    asset.texts[spec.name] = cached
                    continue
                request = self._build_request(spec, config)
                result = vision.run(request, image_ref(asset))
                if result.usage is not None:
                    self._add_usage(result.usage)
                text = result.text
                # 空结果不落缓存：成功产物必非空（无文字的图片也输出一行描述），
                # 空串即失败产物——缓存固化失败，重跑无法重试
                if not text:
                    text = "" if spec.render == "attribute" else failure_note(spec)
                    asset.texts[spec.name] = text
                    continue
                asset.texts[spec.name] = text
                store.save_cached_text(spec, asset.sha256, text)

        with ThreadPoolExecutor(max_workers=self.config.vlm_concurrency) as pool:
            futures = {pool.submit(work, a): a for a in assets}
            done = 0
            for future in as_completed(futures):
                asset = futures[future]
                done += 1
                try:
                    future.result()
                    logger.info("[%d/%d] 图片处理完成: %s",
                                done, len(assets), asset.sha256[:12])
                except Exception:
                    logger.exception("图片处理失败: sha=%s", asset.sha256[:12])
                    # 异常为终态，不重试；只补齐未完成的任务产物
                    # （已成功任务（含缓存命中）的结果不覆盖）
                    for spec in plan.tasks:
                        if spec.name not in asset.texts:
                            asset.texts[spec.name] = (
                                "" if spec.render == "attribute"
                                else failure_note(spec)
                            )

    @staticmethod
    def _build_request(spec: TaskSpec, config: Config):
        """任务 → 视觉请求：max_tokens 与 system 在此解析缺省（协议层只见生效值）"""
        from .vlm.base import VisionRequest
        return VisionRequest(
            system=effective_system(spec),
            user=spec.prompt,
            max_tokens=spec.max_tokens if spec.max_tokens is not None
            else config.vlm_max_tokens,
        )

    def _add_usage(self, usage) -> None:
        """线程安全累计 token 用量"""
        with self._usage_lock:
            self._usage_totals["prompt"] += usage.prompt_tokens
            self._usage_totals["completion"] += usage.completion_tokens
            self._usage_totals["total"] += usage.total_tokens

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
