"""omnimd 的 Python 库接口

公开的类：OmniMD（转换客户端）、连接配置对象（VlmConfig / S3Config）、转换结果 ConversionResult。异常见 exceptions.py。

用法与大模型 SDK 一致：先构造客户端，后转换。
构造时 vlm / storage 给显式配置对象则以其为准，缺省读环境变量 / .env（与命令行同一套配置）；
convert() 只传文件与本次转换的选项——产出组成（output）是每次转换的参数，缺省取配置的 OUTPUT。
构造成功即配置完整，实例可跨文件复用（引擎与双层缓存在实例内自动共享）。
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .config import load
from .converter import ConversionResult, Converter, ImageInfo
from .exceptions import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VlmConfig:
    """视觉模型连接配置（OpenAI 兼容端点，凭据三项 + 可选思考控制方式）"""

    api_key: str
    base_url: str
    model: str
    # 以下字段不给（None）则回落环境值；给了以对象为准
    max_tokens: int | None = None    # 单图转写输出上限
    concurrency: int | None = None   # 转写并发数
    thinking: str | None = None      # 思考控制方式


@dataclass(frozen=True)
class S3Config:
    """S3 兼容对象存储连接配置（云厂商与自托管 MinIO 通用）"""

    endpoint: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    # 不给（None）则回落环境值
    key_prefix: str | None = None    # 对象 key 前缀，用于桶内目录隔离，可为空


def _vlm_fields(vlm: VlmConfig) -> dict:
    """把显式 VlmConfig 翻译为 Config 字段，思考方式取值不合法即报错"""
    if not isinstance(vlm, VlmConfig):
        raise ConfigError(f"vlm 需要 VlmConfig，得到 {type(vlm).__name__}")
    from .vlm.thinking import THINKING_EXTRA_BODY
    if vlm.thinking is not None and vlm.thinking not in THINKING_EXTRA_BODY:
        raise ConfigError(
            f"不支持的 thinking: {vlm.thinking}"
            f"（可选：{', '.join(THINKING_EXTRA_BODY)}）"
        )
    overrides = dict(
        vlm_api_key=vlm.api_key,
        vlm_base_url=vlm.base_url, vlm_model=vlm.model,
    )
    # 可选字段仅在显式给出时覆盖（None = 回落环境值）
    if vlm.max_tokens is not None:
        overrides["vlm_max_tokens"] = vlm.max_tokens
    if vlm.concurrency is not None:
        overrides["vlm_concurrency"] = vlm.concurrency
    if vlm.thinking is not None:
        overrides["vlm_thinking"] = vlm.thinking
    return overrides


def _storage_fields(storage: S3Config) -> dict:
    """把显式存储配置翻译为 Config 字段，类型不合法即报错"""
    if isinstance(storage, S3Config):
        overrides = dict(
            storage_provider="s3",
            s3_endpoint_url=storage.endpoint, s3_region=storage.region,
            s3_bucket=storage.bucket, s3_access_key_id=storage.access_key_id,
            s3_secret_access_key=storage.secret_access_key,
        )
        if storage.key_prefix is not None:
            overrides["s3_key_prefix"] = storage.key_prefix
        return overrides
    raise ConfigError(f"storage 需要 S3Config，得到 {type(storage).__name__}")


class OmniMD:
    """文件转 Markdown 的转换客户端。

    连接配置在构造时给定（显式配置对象，或缺省读环境变量 / .env），
    产出组成是 convert 的参数；实例可重复用于多个文件。
    """

    def __init__(
        self,
        vlm: VlmConfig | None = None,
        storage: S3Config | None = None,
        *,
        doc_engine: str | None = None,
        cache_dir: str | Path | None = None,
        concurrency: int | None = None,
    ):
        """构造转换客户端。

        vlm / storage：显式连接配置对象，给定的维度以对象为准、其余读环境变量；都不给时完全按环境变量。
        doc_engine / cache_dir / concurrency：运行参数，显式值优先于环境值（DOC_ENGINE / CACHE_DIR / VLM_CONCURRENCY，各字段语义见 .env.example）。
        """
        # 环境配置先加载（跳过必填校验），显式对象覆盖，最后统一校验
        config = load(
            doc_engine=doc_engine,
            cache_dir=str(cache_dir) if cache_dir else None,
            concurrency=concurrency,
            validate=False,
        )
        if vlm is not None or storage is not None:
            overrides = {}
            if vlm is not None:
                overrides.update(_vlm_fields(vlm))
            if storage is not None:
                overrides.update(_storage_fields(storage))
            config = replace(config, **overrides)
        config.validate()
        self._converter = Converter(config)

    # ========== 转换 ==========
    def convert(self, file_path: str | Path, *, output: str | None = None) -> ConversionResult:
        """转换单个文件，返回 ConversionResult（.markdown 为产出全文）。

        output 是本次转换的产出组成（逗号分隔多选 body/url/base64/ocr/alt；body 必选锚点省略自动包含，url 与 base64 互斥，ocr/alt 依赖其一；缺省取配置的 OUTPUT，未设置为 body 纯解析）。
        """
        return self._converter.convert(Path(file_path), output=output)

    def convert_many(
        self, inputs: Iterable[str | Path], *, output: str | None = None,
        max_workers: int = 1,
    ) -> dict[str, ConversionResult | Exception]:
        """批量转换。inputs 为文件路径或目录的可迭代对象。

        返回 {完整路径: ConversionResult}（键唯一，同名文件不互相覆盖）；
        单个文件失败不中断整批，其值为捕获到的异常对象本身（不吞不改）——调用方以 isinstance(result, ConversionResult) 区分成败。

        max_workers 是文件级并发数（缺省 1 = 串行，行为与逐个 convert 一致）；总视觉模型并发约为 max_workers × VLM_CONCURRENCY，按端点限流酌情设置。
        """
        files = []
        for item in inputs:
            path = Path(item)
            if path.is_dir():
                files.extend(sorted(f for f in path.iterdir() if f.is_file()))
            else:
                files.append(path)
        # 键用绝对路径：dir 与 ./dir、大小写变体不产生重复条目；同一路径重复出现（文件 + 其所在目录混传）去重，只转换一次
        files = list(dict.fromkeys(f.resolve() for f in files))
        results: dict[str, ConversionResult | Exception] = {}

        def run(file_path: Path):
            return file_path, self.convert(file_path, output=output)

        if max_workers <= 1:
            for file_path in files:
                try:
                    _, result = run(file_path)
                    results[str(file_path)] = result
                except Exception as e:
                    logger.exception("转换失败: %s", file_path.name)
                    results[str(file_path)] = e
            return results

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(run, f): f for f in files}
            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    _, result = future.result()
                    results[str(file_path)] = result
                except Exception as e:
                    logger.exception("转换失败: %s", file_path.name)
                    results[str(file_path)] = e
        return results
