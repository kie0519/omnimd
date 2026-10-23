# Python 库接口

> **定位**：omnimd 作为 Python 库使用的完整说明——安装、构造、转换、参数与错误处理。命令行用法见根 [README.md](../README.md)，配置项语义见 [.env.example](../.env.example)。
> **读者**：人类专家、AI Agent
> **最后更新**：2026-10-19

---

## 一、安装

```bash
pip install omnimd           # 或：uv tool install omnimd
```

Python ≥ 3.10。依赖（解析引擎、openai SDK、boto3 等）随包安装，无额外步骤。从源码安装（参与开发）见根 README「参与开发」。

## 二、模型：先构造客户端，后转换

1. **构造**：构造一个 `OmniMD` 客户端（连接配置缺省读环境变量 / .env——纯解析无需凭据即可构造，凭据在首次转写/上传时才校验）；
2. **转换**：`convert()` 只传文件与本次转换的产出组成——连接配置不出现在业务调用里。

```python
from omnimd import OmniMD

md = OmniMD()                    # 读环境变量 / .env（缺省构造方式）
result = md.convert("报告.pdf")   # → ConversionResult
result.markdown                  # 完整 Markdown 文本（str）
```

构造即校验运行参数（DOC_ENGINE / OUTPUT 等取值不合法在构造时报错并列出全部问题）；连接凭据按消费方惰性校验——首次转写/描述时查 VLM 凭据三项，首次上传时查存储配置（见 §六），缺配置给出带指引的报错。实例可重复用于任意多个文件——引擎与模型客户端在实例内建立，多文件转换不重复初始化；转写/URL 缓存落磁盘，跨实例、跨进程共享。

## 三、构造方式

### 3.1 从环境变量构造（推荐用于脚本与本机工具）

```python
md = OmniMD()
```

读取的环境变量与命令行完全一致（见 [.env.example](../.env.example)）——同一份配置同时驱动 CLI 与库。注意：导入 omnimd 即会加载当前目录与包所在目录的 .env 到进程环境（逐个补缺、不覆盖已设值）。部署面按产出组成分级：纯解析（`body`）无需凭据；转写（`ocr`/`alt` 部件）需 VLM 凭据三项（KEY/URL/MODEL，任意 OpenAI 兼容端点直接接入；`VLM_THINKING` 可选思考控制方式、缺省 none），配合不含 `url` 部件的产出即不需要存储（图以 base64 直传视觉模型）；产出含 `url` 部件时才要求存储配置。构造参数可覆盖环境值：

```python
md = OmniMD(
    doc_engine="anydoc",       # 运行参数覆盖（解析引擎 / 缓存目录 / 并发）
    concurrency=4,
)
```

### 3.2 显式传连接配置（推荐用于服务端集成）

连接配置全部来自代码，不经过环境变量——适合密钥来自你自己的配置中心 / 密钥管理服务的场景。给定的维度以对象为准，未给定的维度仍读环境变量：

```python
from omnimd import OmniMD, VlmConfig, S3Config

md = OmniMD(
    vlm=VlmConfig(
        api_key="<密钥>",
        base_url="<接口地址>",   # 任意 OpenAI 兼容端点，如本地 Ollama：http://localhost:11434/v1
        model="<模型名>",
        max_tokens=8192,         # 可选：单图转写输出上限
        concurrency=8,           # 可选：转写并发数
        thinking="none",         # 可选：思考控制方式（缺省 none，取值表见 .env.example）
    ),
    storage=S3Config(
        endpoint="<S3 端点>",   # 自托管 MinIO / OBS / OSS / COS 通用
        region="<区域>", bucket="<桶名>",
        access_key_id="<AK>", secret_access_key="<SK>",
        key_prefix="your-prefix/",   # 可选：桶内目录隔离
    ),
)
```

## 四、转换

### 4.1 单文件

```python
result = md.convert("报告.pdf")
result.markdown        # 完整 Markdown 文本，与 CLI 写出的文件内容一致
result.images          # 图片清单：[ImageInfo(sha256, url, alt_text, ocr_text)]
result.image_count     # 产出中的图片块数
result.char_count      # 产出字符数
result.elapsed_ms      # 转换耗时（毫秒）
```

`images` 是产出中每张图片的结构化元数据——程序消费方（RAG 管线、agent）免于解析 Markdown；字段值反映管线结果而非产出组成（url 为空 = 未上传或上传失败；ocr_text 为空 = 本次产出不含该部件或该图被跳过，转写失败时为「（图片转写失败）」标记串；alt_text 为空 = 不含该部件或生成失败，渲染回退「图片」）。

`output` 参数指定本次转换的产出组成（逗号分隔多选，未设置时取配置的 `OUTPUT`）。取值域五个部件：

| 部件 | 产出形态 | 依赖 | 成本 |
|---|---|---|---|
| `body` | 文件正文 Markdown（必选锚点，省略时自动包含） | 无 | 解析引擎 |
| `url` | `![alt](图片公开链接)`——图片可看 | 存储；与 `base64` 互斥 | 上传（同图去重，重跑不再上传） |
| `base64` | `![alt](data:image/...)`——图片内嵌自包含 | 与 `url` 互斥 | 产出体积膨胀 |
| `ocr` | 图片下方的完整转写文本块——内容可检索 | 视觉模型；依赖 `url` 或 `base64` | 每图一次视觉模型调用 |
| `alt` | 链接内的一句话描述文本——无障碍/检索锚点 | 视觉模型；依赖 `url` 或 `base64` | 每图一次视觉模型调用 |

```python
md.convert("报告.pdf")                          # 纯解析（body，未设置时取此值）
md.convert("报告.pdf", output="body,url")        # 正文 + 图片链接
md.convert("报告.pdf", output="body,base64,ocr") # 正文 + 内嵌 + 转写
md.convert("报告.pdf", output="body,url,ocr,alt")   # 全形态
```

组合规则：`url` 与 `base64` 互斥（图片只能有一种呈现）；`ocr`/`alt` 依赖 `url` 或 `base64`（图片内容需有载体）；`body` 为必选锚点，省略时自动包含。

同一个实例可交替使用不同产出组成——视觉模型客户端惰性构造，仅含转写或描述的转换才调用视觉模型（产生调用成本）；转写与描述的结果各自独立缓存，重跑不再调用。

### 4.2 批量

```python
results = md.convert_many(["a.pdf", "b.docx", "某个目录/"])
# → {完整路径: ConversionResult, ...}

for path, result in results.items():
    if isinstance(result, Exception):
        print(f"{path} 失败: {result}")
```

- 输入为文件路径与目录的任意混合；目录按文件名排序展开
- 返回 `{完整路径: ConversionResult}`（键唯一，不同目录的同名文件不互相覆盖）；**单个文件失败不中断整批**，其值为捕获到的异常对象本身（不吞不改）
- `output` 参数语义与 `convert` 一致
- `max_workers` 文件级并发数（缺省 1 = 串行）；总视觉模型并发约为 max_workers × VLM_CONCURRENCY，按端点限流酌情设置

## 五、运行参数

构造时（`OmniMD(...)` 的关键字参数）可覆盖环境值：

| 参数 | 缺省 | 说明 |
|---|---|---|
| `doc_engine` | `"markitdown"` | 非 PDF 格式的解析引擎；`"anydoc"` 正文质量优先 |
| `cache_dir` | 系统用户缓存目录 | 转写/URL 缓存目录；相同输入重复转换不再调用模型 |
| `concurrency` | `8` | 图片转写并发数 |

产出组成不在构造参数里——它是每次 `convert` 的参数（`output=`），缺省取配置的 `OUTPUT`。参数含义与 .env 变量一一对应（`DOC_ENGINE` / `CACHE_DIR` / `VLM_CONCURRENCY` / `OUTPUT`）。缓存目录缺省取各平台用户缓存目录：Linux `~/.cache/omnimd/`、macOS `~/Library/Caches/omnimd/`、Windows `%LOCALAPPDATA%\omnimd\Cache`。

VLM 取图方式无配置项——由产出组成推导：含 `url` 部件传公开 URL（图片已上传），不含则传 base64（字节本地持有，天然兼容本地端点）。存储按需不发生作用：产出不含 `url` 部件时不校验、不上传，已配置的存储不接触。端点不支持 URL 传图（Ollama 等本地端点）且产出含 `url` 部件时报错，指引改用 `base64` 部件或换端点。

## 六、错误处理

异常分两类：omnimd 自有异常（`OmniMDError` 及其子类，`from omnimd import ...` 可直接捕获）与上游原始异常（解析引擎 / openai SDK / boto3，透传不改写）。

| 时机 | 异常 | 含义 |
|---|---|---|
| 构造 / 转换 | `OmniMDError` | omnimd 全部自有异常的基类，兜底捕获用它 |
| 构造 / 首次转换 | `ConfigError` | 配置错误：缺必填凭据、运行参数取值不合法、产出组成取值非法、STORAGE_PROVIDER 拼写错误（构造期报）；存储凭据缺失在产出需要上传时（转换期）报 |
| `convert` | `UnsupportedFormatError` | 文件格式不受支持（报错列出全部可选格式） |
| `convert` | 上游异常 | 解析引擎或存储的原始异常，调用方按需捕获 |

`convert_many` 不抛异常——单文件失败的结果值为该文件捕获到的异常对象。

## 七、典型集成示例

RAG 知识库入库前把文件批量转为 Markdown：

```python
from omnimd import OmniMD, ConversionResult

md = OmniMD()                     # 配置来自部署环境的 .env

def ingest(documents_dir: str) -> dict[str, str]:
    """目录下全部文件 → {路径: Markdown}，失败的文件不进结果"""
    return {
        path: r.markdown
        for path, r in md.convert_many([documents_dir]).items()
        if isinstance(r, ConversionResult)
    }
```

coding agent 上下文准备：

```python
def context_for_agent(files: list[str]) -> str:
    parts = []
    for path, result in md.convert_many(files).items():
        if isinstance(result, ConversionResult):
            parts.append(f"# 来源：{path}\n\n{result.markdown}")
    return "\n\n---\n\n".join(parts)
```

图片信息走结构化清单（免解析 Markdown）：

```python
result = md.convert("报告.pdf", output="body,url,ocr,alt")
for img in result.images:          # ImageInfo(sha256, url, alt_text, ocr_text)
    index_by_sha[img.sha256] = img.url   # RAG 多模态入库示例
```
