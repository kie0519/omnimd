# Python 库接口

> **定位**：omnimd 作为 Python 库使用的完整说明——安装、构造、转换、参数与错误处理。命令行用法见根 [README.md](../README.md)，配置项语义见 [.env.example](../.env.example)。
> **读者**：人类专家、AI Agent
> **最后更新**：2026-10-27

---

## 一、安装

```bash
pip install omnimd           # 或：uv tool install omnimd
```

Python ≥ 3.10。依赖（解析引擎、openai SDK、boto3 等）随包安装，无额外步骤。从源码安装（参与开发）见根 README「参与开发」。

## 二、模型：先构造客户端，后转换

1. **构造**：构造一个 `OmniMD` 客户端（连接配置缺省读环境变量 / .env——文字类裸命令无需凭据即可构造，凭据在首次图片任务/上传时才校验）；
2. **转换**：`convert()` 只传文件与本次的呈现与任务动词（参数与 CLI 同名同义）——连接配置不出现在业务调用里。

```python
from omnimd import OmniMD

md = OmniMD()                    # 读环境变量 / .env（缺省构造方式）
result = md.convert("报告.pdf")   # → ConversionResult；缺省由输入类型决定
result.markdown                  # 完整 Markdown 文本（str）
```

构造即校验运行参数（DOC_ENGINE 等取值不合法在构造时报错并列出全部问题）与任务定义（命名/非空）；连接凭据按消费方惰性校验——首次图片任务时查 VLM 凭据三项，首次上传时查存储配置（见 §六），缺配置给出带指引的报错。实例可重复用于任意多个文件——引擎与模型客户端在实例内建立，多文件转换不重复初始化；任务产物/URL 缓存落磁盘，跨实例、跨进程共享。

## 三、构造方式

### 3.1 从环境变量构造（推荐用于脚本与本机工具）

```python
md = OmniMD()
```

读取的环境变量与命令行完全一致（见 [.env.example](../.env.example)）——同一份配置同时驱动 CLI 与库。注意：构造 `OmniMD()` 时按查找链加载 .env（进程环境变量优先，逐个补缺、不覆盖已设值）——import 无文件读取副作用。部署面分级：文字类裸命令无需凭据；图片任务需 VLM 凭据三项（KEY/URL/MODEL，任意 OpenAI 兼容端点直接接入；`VLM_THINKING` 可选思考控制方式、缺省 none），非 link 呈现不需要存储（图以 base64 直传视觉模型）；`images="link"` 时才要求存储配置。构造参数可覆盖环境值：

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
        timeout=300.0,           # 可选：单次调用超时（秒）
        concurrency=8,           # 可选：图片任务并发数
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

### 3.3 任务定义（随代码管理）

`tasks` 参数注册可复用的自定义任务（构造期校验命名与提示词）；工作目录 `prompts/` 下的任务文件也自动发现，显式定义覆盖同名文件：

```python
from omnimd import OmniMD, TaskPrompt

walk = TaskPrompt(
    name="walk",
    prompt="完整走查这个界面的布局、组件与配色",   # 必填——任务本体
    system=None,                                  # 可选；None = 内置通用骨架
)
md = OmniMD(tasks=[walk])
md.convert("设计稿.png", prompt=[walk, "重点看配色"])   # TaskPrompt 与即时提示词混排
```

## 四、转换

### 4.1 单文件

```python
result = md.convert("报告.pdf")
result.markdown        # 完整 Markdown 文本，与 CLI 写出的文件内容一致
result.images          # 图片清单：[ImageInfo(sha256, url, texts)]
result.image_count     # 产出中的图片块数
result.char_count      # 产出字符数
result.elapsed_ms      # 转换耗时（毫秒）
result.usage           # vlm.Usage | None——本次转换的 token 用量（无调用时 None）
```

`images` 是产出中每张图片的结构化元数据——程序消费方（RAG 管线、agent）免于解析 Markdown；`texts` 按任务名给出各任务产物（`texts["transcribe"]` 为转写、`texts["describe"]` 为描述位文本——描述失败为空串，渲染回落「图片」；产物为独立文本段的任务（转写与自定义）的失败值为「（图片转写失败）」样式的标记串）；`url` 为空 = 未上传或上传失败。

转换参数（与 CLI 同名同义）：

| 参数 | 取值 | 说明 |
|---|---|---|
| `images` | `"link"` / `"embed"` / 不传 | 图片呈现：link=公开链接（需存储）、embed=base64 内嵌；不传 = 丢弃图片只要文字 |
| `transcribe` | `bool` | 完整转写每张图片（等价 `prompt="transcribe"`） |
| `describe` | `bool` | 一句话描述填入链接描述位（等价 `prompt="describe"`；需 `images`） |
| `prompt` | `str` / `TaskPrompt` / 两者的列表 | 任务名（内置或 `prompts/` 文件名）→ 引用任务；即时提示词 → 即时任务；可叠加 |

```python
md.convert("报告.pdf")                                  # 缺省由输入类型决定（文字类 = 仅正文）
md.convert("手册.pdf", images="link", transcribe=True)  # 正文 + 链接 + 转写
md.convert("手册.pdf", transcribe=True)                 # 纯文本库：不留图，每图变文字
md.convert("截图.png", prompt=[walk, "重点看配色"])       # 自定义任务叠加
```

缺省由输入类型决定：文字类裸命令只提取正文；**图片文件裸命令注入完整转写**（转写就是图片的解析）。`describe` 依赖 `images`（描述填在链接描述位）——缺呈现时报 `ConfigError` 并给指引；`transcribe` / `prompt` 不依赖呈现（drop 时任务文本独立成段，占图片原位）。

同一个实例可交替使用不同参数组合——视觉模型客户端惰性构造，仅含任务的转换才调用视觉模型（产生调用成本）；各任务产物独立缓存（按模型、思考方式、任务与提示词摘要隔离），重跑不再调用。

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
- 转换参数（`images` / `transcribe` / `describe` / `prompt`）与 `convert` 一致
- `max_workers` 文件级并发数（缺省 1 = 串行）；总视觉模型并发约为 max_workers × VLM_CONCURRENCY，按端点限流酌情设置

## 五、运行参数

构造时（`OmniMD(...)` 的关键字参数）可覆盖环境值：

| 参数 | 缺省 | 说明 |
|---|---|---|
| `doc_engine` | `"markitdown"` | 非 PDF 格式的解析引擎；`"anydoc"` 正文质量优先 |
| `cache_dir` | 系统用户缓存目录 | 任务产物/URL 缓存目录；相同输入重复转换不再调用模型。取自 .env 的相对路径锚定 .env 所在目录（显式参数相对进程工作目录） |
| `concurrency` | `8` | 图片任务并发数 |

产出语义（呈现、任务）不在构造参数里——它是每次 `convert` 的参数；`.env` 只承载环境与引擎（参数含义与 .env 变量一一对应：`DOC_ENGINE` / `CACHE_DIR` / `VLM_CONCURRENCY`）。缓存目录缺省取各平台用户缓存目录：Linux `~/.cache/omnimd/`、macOS `~/Library/Caches/omnimd/`、Windows `%LOCALAPPDATA%\omnimd\Cache`；`CACHE_DIR` 相对值相对于定义它的 .env 所在目录解析（不经 .env 的环境变量注入则相对于当前目录），绝对路径原样。

VLM 取图方式无配置项——由呈现值推导：`link` 传公开 URL（图片已上传），其余传 base64（字节本地持有，天然兼容本地端点）。存储按需不发生作用：非 link 呈现时不校验、不上传，已配置的存储不接触。端点不支持 URL 传图（Ollama 等本地端点）且 link 呈现时报错，指引改用 `embed` 或换端点。

## 六、错误处理

异常分两类：omnimd 自有异常（`OmniMDError` 及其子类，`from omnimd import ...` 可直接捕获）与上游原始异常（解析引擎 / openai SDK / boto3，透传不改写）。

| 时机 | 异常 | 含义 |
|---|---|---|
| 构造 / 转换 | `OmniMDError` | omnimd 全部自有异常的基类，兜底捕获用它 |
| 构造 / 首次转换 | `ConfigError` | 配置错误：缺必填凭据、运行参数或呈现值取值不合法、`describe` 缺呈现、任务名非法或提示词为空（构造期报）；存储凭据缺失在 link 呈现需要上传时（转换期）报；VLM 端点 401/403/404 按状态码转译为指引 |
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
result = md.convert("报告.pdf", images="link", transcribe=True, describe=True)
for img in result.images:          # ImageInfo(sha256, url, texts)
    index_by_sha[img.sha256] = img.url   # RAG 多模态入库示例
```
