# 架构设计

> **定位**：omnimd 的技术架构——处理管线、抽象层、缓存体系、代码地图。每项关键设计的理由在正文直接给出。
> **读者**：维护者、AI Agent
> **最后更新**：2026-10-11

---

## 一、处理管线（四步）

```
输入文件
  │
  ① 解析：按扩展名路由到解析引擎
  │    ├─ 图片  → ImageEngine（图片注册，产出占位符）
  │    ├─ .pdf  → PyMuPdfEngine（pymupdf4llm，正文+表格+图片内嵌，固定路线）
  │    └─ 其他  → DOC_ENGINE 选定引擎（缺省 markitdown；不支持时回退 anydoc）
  │         ↓ 正文 Markdown（图片以占位符 / data URI 内嵌，引擎不落盘）
  ② 注册：短边<60px 丢弃 → SHA-256 去重 → 替换为占位符 @@IMG:{sha256}@@
  │         ↓
  ③ 处理（两段并发：先全部上传、后全部转写）「URL 缓存命中？→ 上传 ｜ 转写缓存命中？→ VLM 转写 → 写缓存」
  │         ↓（不含 text/alt 时止步于上传，不调 VLM；base64 传输且不含 url 时不上传——VLM 直收图片数据）
  ④ 渲染：占位符 → ![alt 文本](url)（含 alt 时描述进链接位，含 text 时再跟转写文本块），压缩连续空行 → 覆盖写入输出目录
```

编排者 [converter.py](../omnimd/converter.py) 的 `convert()` 依次执行四步，每步职责单一、可独立理解。产出组成是每次 `convert` 的参数（缺省取配置的 `OUTPUT`），与连接配置的生命周期分离——同一个转换器实例可产出任意组成的 Markdown。实例跨调用共享的只有不可变配置与线程安全客户端（存储 / 视觉模型）：**资产库随每次转换创建**（图片字节不跨转换滞留内存，跨文件去重由双层磁盘缓存承担），**解析引擎按线程隔离复用**（第三方引擎实例的线程安全性不受本工具控制）——同一实例可安全并发调用。图片的占位符机制（②→④）让正文文本与图片处理完全解耦——上传/转写的先后不影响正文结构，装饰图丢弃后的空位在渲染时统一清理。

## 二、抽象层

```
                    ┌─────────────────────────┐
                    │    Converter（编排）      │
                    └──┬─────────┬─────────┬──┘
        resolve_engine   │         │         │   create_vlm() / create_uploader()
                       ▼         │         ▼
  ┌──────────────────────┐   │  ┌──────────────────────┐ ┌──────────────────────┐
  │ engines/（解析引擎）    │   │  │  vlm/（视觉模型）      │ │ storage/（文件存储）   │
  │ convert()->正文md     │   │  │  ocr/describe(image_ref) │ │ upload_image()->url  │
  └──────────────────────┘   │  └──────────────────────┘ └──────────────────────┘
```

三个可插拔维度，按**变更原因**划分：

| 层 | 变更原因 | 当前实现 |
|---|---|---|
| engines/ | 格式质量/速度/格式面的选型变化 | pymupdf（PDF 图片保真）+ markitdown（缺省，格式面最宽）+ anydoc（质量选项，DOC_ENGINE 配置） |
| vlm/ | 模型降价/新模型出现/端点切换 | 单协议实现：OpenAI Chat Completions + image_url；任意兼容端点凭据三项直接接入，思考控制方式由 VLM_THINKING 配置（参数表在 vlm/thinking.py） |
| storage/ | 存储后端迁移/桶策略变化 | s3（S3 兼容通用项：自托管 MinIO / OBS / OSS / COS 一套实现） |

**URL 契约**是 vlm 与 storage 的解耦点：存储产出长期公开 URL → VLM 以 URL 传图识别，两层互不感知，任意组合成立。**引擎不感知 VLM/存储/缓存**（只接收 store 注册图片）；依赖方向单向：`converter → engines / vlm / storage / images`，四层无横向调用（images 对 storage 仅一处类型引用，无调用）。

### 2.1 engines/（解析引擎）

- `__init__.py`：`resolve_engine(suffix, doc_engine)` 路由——图片与 PDF 固定路由（语义唯一）；其余按 DOC_ENGINE 选择，不支持的格式回退另一引擎（格式面取并集）。注册表只持有引擎的定位信息（模块/类名/格式面），引擎模块在首次解析时才导入
- `image_engine.py`：单张图片输入——图片注册为占位符，产出形态与文档内嵌图片一致
- `base.py`：契约 `convert(file_path, store) -> str`。图片出口只有两种形态——占位符（引擎直接注册）或 data URI（converter 接管），引擎不落盘
- `pymupdf_engine.py`：PDF 专线。`embed_images=True` 图片直接内嵌正文（不落盘，源文件名的特殊字符不影响产出）、`use_layout(False)` Windows 兼容、picture-text 标记清理
- `markitdown_engine.py`：缺省引擎。`keep_data_uris=True` 保留内嵌图、插件显式关闭、用官方推荐字段 `.markdown`
- `anydoc_engine.py`：质量选项（DOC_ENGINE=anydoc）。`to_document` 拿文档模型后**自渲染**为 GFM（anydoc 的现成 Markdown 输出对图片有损）——嵌入图字节经 `store.register()` 进占位符管线；渲染约定对齐 anydoc 官方序列化器

新增引擎 = 注册表一项（定位符 + 支持格式声明）+ 实现子类，配置校验不用改。

### 2.2 vlm/（视觉模型）

- `base.py`：契约 `ocr(image_ref) -> str`（完整转写）与 `describe(image_ref) -> str`（alt 文本）——image_ref 是公开 URL 或 base64 data URI，由 VLM_IMAGE_INPUT 决定
- `thinking.py`：思考控制方式 → extra_body 参数表，按「开/关 × 协议」对称展开：`none`（缺省，不发参数）/ `thinking-off|on` / `template-off|on` / `effort-off|on` / `both-off|on`。接入新端点不改代码；出现新的思考控制协议才加一对取值
- `openai_compat.py`：唯一实现——OpenAI Chat Completions 协议骨架（客户端构造、消息组装、按 VLM_THINKING 注入思考参数；瘦客户端：调用、返回内容、异常透传）
- `__init__.py`：`create_vlm(config)` 工厂（骨架类延迟导入——config 层校验取值表时不得经过依赖 config 的骨架模块，防循环导入）

### 2.3 storage/（文件存储）

- `base.py`：契约 `upload_image(data, filename) -> url`
- `s3.py`：S3 兼容实现，一套代码覆盖自托管 MinIO / 华为 OBS / 阿里 OSS / 腾讯 COS。硬约束：SigV4 签名 + path 寻址；对象 key = `{S3_KEY_PREFIX}{sha256}.png`（内容寻址，重复上传幂等）；URL 直接拼接，无签名无过期（MinIO 桶需开放匿名只读策略，云厂商桶同理开放公开读）
- `__init__.py`：`create_uploader(config)` 工厂 + 注册表

### 2.4 配置层

[config.py](../omnimd/config.py) 的 `load()` 从 .env / 环境变量构造不可变 `Config`，加载即校验。校验按知识归属分层：

- config 校验自己拥有的知识：必填凭据（VLM KEY/URL/MODEL 三项）是否齐全、运行参数（`OUTPUT` / `DOC_ENGINE` / `VLM_IMAGE_INPUT` / `VLM_THINKING`）取值是否合法——合法取值从各层注册表导入（`parse_output` 内置取值表、`DOC_ENGINES` 来自 engines、`THINKING_EXTRA_BODY` 来自 vlm/thinking.py），不在文档另抄一份
- 存储按需必填：产出含图片链接、或 URL 传图模式下的视觉任务需要上传时，在 `Converter.uploader` 惰性构造并校验（`STORAGE_PROVIDER` 是否支持、该后端声明的 `REQUIRED_ENV_VARS` 是否齐全），缺配置给出带指引的报错

CLI 只覆盖运行参数（产出组成/并发/缓存目录），不提供端点切换——凭据三项（key/url/model）随端点整体更换，避免新旧配置混用。

## 三、缓存体系（持久化数据模型）

工作目录默认取各平台用户缓存目录（Linux `~/.cache/omnimd/`、macOS `~/Library/Caches/omnimd/`、Windows `%LOCALAPPDATA%\omnimd\Cache`，由 `platformdirs` 解析）。以 Linux 为例：

```
~/.cache/omnimd/
├── ocr/{thinking}--{model}--{input}--{kind}--{prompt_digest}/{sha256}.{kind}.md  # 视觉产物缓存（存转写与描述，文档统称转写缓存），按思考控制方式+模型+传图方式+任务+提示词摘要隔离
└── urls/{provider}--{端点与桶的摘要}/{sha256}.url  # 上传 URL，按存储端点与桶隔离
```

引擎不落盘：解析中间产物只在内存流转（data URI / 占位符），不产生磁盘中间产物。

**隔离维度的设计理由**：转写结果依赖模型（换模型强制重新转写，便于评估），URL 依赖存储后端（换后端旧 URL 不污染产出）。

**三级查找**（`ensure_uploaded`）：进程内存 → URL 缓存文件 → 实际上传（后写缓存）。重跑任意文件，双层缓存全命中时 API 调用为 0。

## 四、并发与幂等

- 实例级并发：`convert` 可在同一实例上被多线程并发调用——共享的只有不可变配置与线程安全客户端（boto3 / openai 客户端），资产库按转换创建、引擎按线程隔离复用
- 上传并发：与转写同一 `ThreadPoolExecutor` 框架，两段式（全部上传 → 全部转写；URL 传图时转写输入依赖上传结果，base64 直传时两段各自独立）；并发数读 `VLM_CONCURRENCY`（缺省 8），单张上传失败不中断（该图从产出中删除，与装饰图同处置）
- 转写并发：同一池；单图失败不中断整批，该图渲染为失败提示
- 上传幂等：对象 key 内容寻址（sha256 命名），重复上传覆盖的是逐字节相同的内容
- 视觉模型返回空内容不做诊断与重试：按转写失败渲染（链接保留，文本位写「（图片转写失败）」）；API 异常透传不改写。上传失败与转写失败的产出形态不同：前者整图块删除，后者链接照常
- 整批语义：单文件转换失败记日志、退出码置 1，不中断其余文件

## 五、依赖加载

各第三方依赖的导入开销差异大——markitdown 的模块导入会加载全部格式
转换器（pandas / onnxruntime / magika），是最大单项——且多数依赖与
单次运行无关。因此重依赖（解析引擎、openai SDK、boto3）不进模块导入路径，一律在使用处导入；dotenv/Pillow 等轻依赖在模块级导入，
一次运行只加载实际用到的部分：

| 运行场景 | 加载的依赖 |
|---|---|
| `--help` / 参数错误 | 仅标准库 |
| 配置错误 | + config / 引擎注册表（轻量） |
| 仅含图片链接的转换（output=url） | + 实际用到的引擎 + boto3（不加载 openai） |
| 含转写/描述的转换（URL 传图） | + boto3 + openai SDK |
| 含转写/描述的转换（base64 传图） | + openai SDK（不加载 boto3，也不依赖存储） |

引擎注册表只持有定位符（模块路径/类名/格式面声明），`resolve_engine`
解析成功后才导入引擎模块；支持格式声明归注册表所有，引擎类不重复声明。

## 六、代码地图

```
omnimd/
├── library.py         # Python 库接口：OmniMD 客户端（构造 + convert/convert_many）+ ConversionResult，见 library.md
├── exceptions.py      # 异常体系：OmniMDError → ConfigError / UnsupportedFormatError
├── cli.py            # 命令行入口：参数解析、配置加载、逐文件转换调度
├── config.py         # 配置加载与校验（连接配置只认 .env，缺啥报啥）
├── converter.py      # 转换编排：四步管线（解析→注册→处理→渲染），产出组成是 convert 参数，并发安全
├── images.py         # 图片资产：装饰过滤、SHA-256 去重、双层缓存、上传调度
├── prompts/          # 提示词包：每个视觉任务一个模块（ocr.py 完整转写 / alt.py 一句话描述），
│                     #   提示词正文只在这里定义（改提示词只改此处）
├── engines/          # 解析引擎层
│   ├── __init__.py   #   resolve_engine 路由（PDF 固定；其余按 DOC_ENGINE，格式回退）+ 格式面定义处
│   ├── base.py       #   契约：convert(file_path, store) -> str（引擎不落盘）
│   ├── image_engine.py    # 单张图片输入：注册为占位符
│   ├── pymupdf_engine.py  # PDF：pymupdf4llm，embed_images 内嵌图片
│   ├── markitdown_engine.py  # 缺省引擎：markitdown（格式面最宽）
│   └── anydoc_engine.py  # 质量选项：anydoc（to_document + 自渲染）
├── vlm/              # 视觉模型层
│   ├── __init__.py   #   create_vlm 工厂（骨架类延迟导入，防循环依赖）
│   ├── base.py       #   契约：ocr(image_ref) / describe(image_ref)
│   ├── thinking.py   #   思考控制方式 → extra_body 参数表（取值枚举见 §2.2）
│   └── openai_compat.py  # OpenAI Chat Completions 唯一实现（瘦客户端）
└── storage/          # 文件存储层
    ├── __init__.py   #   create_uploader 工厂 + 后端注册表
    ├── base.py       #   契约：upload_image(data, filename) -> url
    └── s3.py         #   S3 兼容（MinIO/OBS/OSS/COS 一套实现）

tests/                # 回归测试（python -m pytest tests/）：
├── test_config.py   #   OUTPUT 取值域、load 校验分支、存储按需必填
├── test_render.py   #   渲染/注册纯函数：产出形态、失败路径、计数口径
├── test_images.py   #   图片资产库：过滤/去重/缓存三级查找/传图方式隔离
├── test_library.py  #   库接口：构造覆盖链、convert_many 路径键与异常透传
├── test_pipeline.py #   管线集成：产出组成 × 传图方式矩阵（stub 上传/视觉模型）
├── test_vlm.py      #   VLM 瘦客户端契约：内容归一/空串/异常透传
├── test_cli.py      #   CLI 参数解析、退出码、产出落盘与同名序号
└── test_demo_api.py #   demo API 契约（stub 转换器，不联网）
```

## 七、外部参考资产

外部依赖的行为以上游发布页为准，只链接不复制：

| 外部依赖 | 上游 |
|---|---|
| markitdown（缺省解析引擎） | https://pypi.org/project/markitdown/ |
| pymupdf4llm（PDF 引擎） | https://pypi.org/project/pymupdf4llm/ |
| firecrawl-anydoc（质量选项引擎） | https://pypi.org/project/firecrawl-anydoc/ |

## 八、已知边界

| 边界 | 说明 |
|---|---|
| 转写质量依赖 VLM | 提示词要求无损转写（表格/流程图/界面逐项还原），但模型能力决定上限；艺术字、低分辨率文字可能标注 `[?]` |
| 缓存目录随模型增长 | 每换一个模型或思考控制方式，`ocr/` 下新增一个目录并全量重新转写（属隔离设计的预期行为——便于对比评估） |
| 单进程 | 无服务化、无 API——CLI 与 Python 库即核心使用面（demo/ 与 skills/ 是基于库的演示/集成形态） |
| anydoc 引擎外链图不转写 | 文档内的外链图已是公开 URL，原样保留不进托管/转写管线 |
| 传图方式默认走 URL | 缺省 `VLM_IMAGE_INPUT=url` 以公开链接传图（存储与转写解耦）；`base64` 直传可不配存储运行（换传图方式强制重新转写） |
