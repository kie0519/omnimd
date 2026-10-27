# 架构设计

> **定位**：omnimd 的技术架构——处理管线、抽象层、缓存体系、代码地图。每项关键设计的理由在正文直接给出。
> **读者**：人类专家、AI Agent
> **最后更新**：2026-10-27

---

## 一、处理管线（五步）

```
输入文件
  │
  ① 编译：CLI/库参数 → PipelinePlan（呈现值 + 任务集，纯函数）；
  │        类型路由缺省：图片文件且无任务动词 → 注入完整转写（转写就是图片的「解析」）
  ② 解析：按扩展名路由到解析引擎
  │    ├─ 图片  → ImageEngine（图片注册，产出占位符）
  │    ├─ .pdf  → PyMuPdfEngine（pymupdf4llm，正文+表格+图片内嵌，固定路线）
  │    └─ 其他  → DOC_ENGINE 选定引擎（缺省 markitdown；不支持时由 anydoc 处理）
  │         ↓ 正文 Markdown（图片以占位符 / data URI 内嵌，引擎不落盘）
  ③ 注册：短边<60px 丢弃 → SHA-256 去重 → 替换为占位符 @@IMG:{sha256}@@
  │         ↓
  ④ 处理（两段并发：先全部上传、后逐图逐任务）「缓存未命中 → 上传 / VLM 调用，成功 → 写缓存；命中则跳过对应调用」
  │         ↓（无任务时止步于上传，不调 VLM；非 link 呈现时无上传阶段——VLM 直收 base64 图片数据；无呈现无任务时整体跳过）
  ⑤ 渲染：占位符 → ![描述](url 或 data URI)（describe 进链接位，block 任务跟文本块；drop 时任务文本独立成段），压缩连续空行 → 覆盖写入输出目录
```

编排者 [converter.py](../omnimd/converter.py) 的 `convert()` 依次执行，每步职责单一、可独立理解。呈现与任务是每次 `convert` 的参数（编译层产物），与连接配置的生命周期分离——同一个转换器实例可产出任意组合的 Markdown；缺省由输入类型决定（配置不承载产出语义）。实例跨调用共享的只有不可变配置、任务注册表与线程安全客户端（存储 / 视觉模型）：**资产库随每次转换创建**（图片字节不跨转换滞留内存，跨文件去重由双层磁盘缓存承担），**解析引擎按线程隔离复用**（第三方引擎实例的线程安全性不受本工具控制）——同一实例可安全并发调用。图片的占位符机制让正文文本与图片处理完全解耦——上传/任务的先后不影响正文结构，装饰图丢弃后的空位在渲染时统一清理。

## 二、抽象层

```
                    ┌─────────────────────────┐
                    │    Converter（编排）      │
                    └──┬─────────┬─────────┬──┘
        resolve_engine   │  create_vlm()  create_uploader()
                       ▼         ▼         ▼
  ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
  │ engines/（解析引擎）    │ │  vlm/（视觉模型）      │ │ storage/（文件存储）   │
  │ convert()->正文md     │ │  run(VisionRequest)   │ │ upload_image()->url  │
  │                      │ │    ->CompletionResult │ │                      │
  └──────────────────────┘ └──────────────────────┘ └──────────────────────┘
```

三个可独立替换的维度，按**变更原因**划分：

| 层 | 变更原因 | 实现 |
|---|---|---|
| engines/ | 格式质量/速度/格式面的选型变化 | pymupdf（PDF 图片保真）+ markitdown（缺省，格式面最宽）+ anydoc（质量选项，DOC_ENGINE 配置） |
| vlm/ | 模型降价/新模型出现/端点切换 | 单协议实现：OpenAI Chat Completions + image_url；任意兼容端点凭据三项直接接入，思考控制方式由 VLM_THINKING 配置（参数表在 vlm/thinking.py） |
| storage/ | 存储端点迁移/桶策略变化 | S3 兼容单协议（自托管 MinIO / OBS / OSS / COS 一套实现，凭据五项直接接入） |

**URL 契约**是 vlm 与 storage 的解耦点：存储产出长期公开 URL → VLM 以 URL 传图识别，两层互不感知。VLM 取图方式由呈现值推导（link 传 URL，其余传 base64）——不是配置项，图片字节本地持有使两种取图方式都零额外成本。**引擎不感知 VLM/存储/缓存**（只接收 store 注册图片）；依赖方向单向：`converter → engines / vlm / storage / images / prompts`，各层无横向调用（images 对 storage 仅一处类型引用，无调用）；vlm 不 import prompts——任务知识住在注册表，协议层只认请求对象。

### 2.1 engines/（解析引擎）

- `__init__.py`：`resolve_engine(suffix, doc_engine)` 路由——图片与 PDF 固定路由（语义唯一）；其余按 DOC_ENGINE 选择，不支持的格式回退另一引擎（格式面取并集）。注册表只持有引擎的定位信息（模块/类名/格式面），引擎模块在首次解析时导入
- `image_engine.py`：单张图片输入——图片注册为占位符，产出形态与文档内嵌图片一致
- `base.py`：契约 `convert(file_path, store) -> str`。图片出口只有两种形态——占位符（引擎直接注册）或 data URI（converter 接管），引擎不落盘
- `pymupdf_engine.py`：PDF 专线。`embed_images=True` 图片直接内嵌正文（不落盘，源文件名的特殊字符不影响产出）、`use_layout(False)` Windows 兼容、picture-text 标记清理
- `markitdown_engine.py`：缺省引擎。`keep_data_uris=True` 保留内嵌图、插件显式关闭、用官方推荐字段 `.markdown`
- `anydoc_engine.py`：质量选项（DOC_ENGINE=anydoc）。`to_document` 拿文档模型后**自渲染**为 GFM（anydoc 的现成 Markdown 输出对图片有损）——嵌入图字节经 `store.register()` 进占位符管线；渲染约定与 anydoc 官方序列化器一致

新增引擎 = 注册表一项（定位符 + 支持格式声明）+ 实现子类，配置校验不用改。

### 2.2 vlm/（视觉模型）

- `base.py`：契约 `run(VisionRequest, image_ref) -> CompletionResult`——VisionRequest（system/user/max_tokens）进、文本+用量出；image_ref 是公开 URL 或 base64 data URI，由呈现值推导：link 传 URL，其余传 base64。协议层零任务知识（任务组装在编排层），校对等未来视觉调用都是同一契约的又一次 run
- `thinking.py`：思考控制方式 → extra_body 参数表，按「开/关 × 协议」对称展开，取值表即本模块注册表（`THINKING_EXTRA_BODY`）。接入新端点不改代码；出现新的思考控制协议才加一对取值
- `openai_compat.py`：唯一实现——OpenAI Chat Completions 协议骨架（客户端构造、消息组装、按 VLM_THINKING 注入思考参数）。四条工程立场：**超时显式化**（`VLM_TIMEOUT` 缺省 300s 注入 SDK，缺省 600s 挂起时无反馈）；**重试不叠加**（SDK 内置 2 次指数退避够用，应用层不包重试——并发 × 双层重试 = 限流风暴）；**采样参数不发送**（端点缺省值是端点作者对自家模型的调校，多端点工具替端点猜值是制造不可预测）；**错误按状态码转译**（401/403 凭据、404 模型名或路径——状态码是协议语义非文案猜测；其余透传）。端点不支持 URL 传图（Ollama 等本地端点，URL 形态 + 400 组合判定）时报错指引两条出路：改用 embed 呈现或换端点
- `__init__.py`：`create_vlm(config)` 工厂（骨架类延迟导入——config 层校验取值表时不得经过依赖 config 的骨架模块，防循环导入）

### 2.2a prompts/（任务注册表）

- `registry.py`：任务知识的唯一注册处——`TaskSpec`（name/prompt/system/render/max_tokens）、`build_registry`（内置 → 工作目录 `prompts/` 发现 → 显式对象三层并入，同名覆盖）、`task_digest`（生效提示词摘要，缓存键成分——骨架更新即缓存失效）、通用骨架（任务无关的「看图回答」底座）
- `transcribe.py` / `describe.py`：内置任务母本（普适动词集——每任务对任意图片成立；场景化任务是用户域）
- 任务文件解析（frontmatter 子集：name 单值 + system 块标量，正文 = 提示词必填）只支持这两键，其他键报错附支持清单

### 2.3 storage/（文件存储）

- `base.py`：契约 `upload_image(image_data, filename) -> url`
- `s3.py`：S3 兼容实现，一套代码覆盖自托管 MinIO / 华为 OBS / 阿里 OSS / 腾讯 COS。硬约束：SigV4 签名 + path 寻址；对象 key = `{S3_KEY_PREFIX}{sha256}.png`（内容寻址，重复上传幂等）；URL 直接拼接，无签名无过期（MinIO 桶需开放匿名只读策略，云厂商桶同理开放公开读）
- `__init__.py`：`create_uploader(config)`——与 VLM 层同一形态：单协议（S3 兼容）+ 凭据直接接入，无厂商概念；必填五项缺一报全清单

### 2.4 配置层

[config.py](../omnimd/config.py) 的 `load()` 从 .env / 环境变量构造不可变 `Config`，加载即校验；.env 按查找链合并（进程环境变量 > 工作目录 .env > 包根 .env），读取时机即 load() 调用时点。校验分层——运行参数在构造期，连接凭据按消费方惰性（纯解析不需要凭据）：

- CACHE_DIR 锚点跟随来源：相对值锚定提供它的配置级目录（进程注入锚工作目录，.env 锚其所在目录），绝对路径原样，未配置用系统用户缓存目录；指向已存在文件时构造期报错附值与来源 .env 路径
- 构造期校验运行参数：`DOC_ENGINE` / `VLM_THINKING` 取值是否合法、数值变量类型（`VLM_TIMEOUT` / `VLM_MAX_TOKENS` / `VLM_CONCURRENCY` 正数）——合法取值从各层注册表导入（`DOC_ENGINES` 来自 engines、`THINKING_EXTRA_BODY` 来自 vlm/thinking.py），不在文档另抄一份
- VLM 凭据按需必填：本次有图片任务时，在 `Converter.vision` 惰性构造并校验（凭据三项 KEY/URL/MODEL 是否齐全、base_url 是否完整 URL），缺配置给出带指引的报错
- 存储按需必填：link 呈现时，在 `Converter.uploader` 惰性构造并校验（`S3_ENDPOINT_URL` 为空 = 不使用存储；非空则 `REQUIRED_ENV_VARS` 声明的五项齐全，缺一报全清单），缺配置给出带指引的报错

CLI 只覆盖产出语义（呈现/任务动词）与运行项（并发/缓存目录），不提供端点切换——凭据三项（key/url/model）随端点整体更换，避免新旧配置混用；产出语义不进 `.env`（裸命令在任何环境跑出同一产出）。

## 三、缓存体系（持久化数据模型）

工作目录默认取各平台用户缓存目录（Linux `~/.cache/omnimd/`、macOS `~/Library/Caches/omnimd/`、Windows `%LOCALAPPDATA%\omnimd\Cache`，由 `platformdirs` 解析）。以 Linux 为例：

```
~/.cache/omnimd/
├── {任务名}/{thinking}--{model}--{任务名}--{digest}/{sha256}.md  # 任务产物缓存（每任务一顶层目录），按思考控制方式+模型+任务+生效提示词摘要隔离
└── urls/s3--{端点与桶与前缀的摘要}/{sha256}.url  # 上传 URL，按存储端点、桶与对象前缀隔离（link 呈现时写入）
```

引擎不落盘：解析中间产物只在内存流转（data URI / 占位符），不产生磁盘中间产物。

**隔离维度的设计理由**：任务产物依赖模型与提示词（换模型、换任务或改提示词强制重新生成，便于评估），URL 依赖存储端点（换端点旧 URL 不污染产出）；同图不同任务两目录，互不串缓存。

**故障策略**：缓存是可再生的加速器，不是转换的依赖——目录不可写（权限/网络盘掉线/磁盘满）时停用缓存并警告一次，转换与上传照常；输出目录不可写仍硬失败。

**三级查找**（`ensure_uploaded`）：进程内存 → URL 缓存文件 → 实际上传（后写缓存）。重跑任意文件，双层缓存全命中时 API 调用为 0。

## 四、并发与幂等

- 实例级并发：`convert` 可在同一实例上被多线程并发调用——共享的只有不可变配置与线程安全客户端（boto3 / openai 客户端），资产库按转换创建、引擎按线程隔离复用
- 上传并发：与任务调用同一 `ThreadPoolExecutor` 框架，两段式（全部上传 → 全部任务；link 呈现时任务输入依赖上传结果，其余无上传阶段）；并发数读 `VLM_CONCURRENCY`（缺省 8），单张上传失败不中断（该图从产出中删除，与装饰图同处置）
- 任务并发：同一池，每图逐任务顺序执行；单图失败不中断整批，该图渲染为失败提示
- 上传幂等：对象 key 内容寻址（sha256 命名），重复上传覆盖的是逐字节相同的内容
- 视觉模型返回空内容不做诊断与重试：按任务失败渲染（呈现照常，block 任务文本位写失败占位、describe 回落「图片」）；401/403/404 按状态码转译为配置指引，其余 API 异常透传不改写。上传失败与任务失败的产出形态不同：前者整图块删除，后者呈现（链接/内嵌）照常、文本位写失败提示
- 整批语义：单文件转换失败记日志、退出码置 1，不中断其余文件

## 五、依赖加载

各第三方依赖的导入开销差异大——markitdown 的模块导入会加载全部格式转换器（pandas / onnxruntime / magika），是最大单项——且多数依赖与单次运行无关。因此重依赖（解析引擎、openai SDK、boto3）不进模块导入路径，一律在使用处导入；dotenv/Pillow 等轻依赖在模块级导入，一次运行只加载实际用到的部分：

| 运行场景 | 加载的依赖 |
|---|---|
| `--help` / 参数错误 | 仅标准库 |
| 配置错误 | + config / 引擎注册表（轻量） |
| 文字类裸命令 | + 实际用到的引擎 |
| link 呈现无任务的转换（文字文件 + `--images link`） | + 实际用到的引擎 + boto3（不加载 openai） |
| 含图片任务的转换（link 呈现，URL 传图） | + boto3 + openai SDK |
| 含图片任务的转换（drop / embed，base64 传图） | + openai SDK（不加载 boto3，也不依赖存储） |

引擎注册表只持有定位符（模块路径/类名/格式面声明），`resolve_engine`解析成功后导入引擎模块；支持格式声明归注册表所有，引擎类不重复声明。

## 六、代码地图

```
omnimd/
├── library.py         # Python 库接口：OmniMD 客户端（构造 + convert/convert_many）+ ConversionResult，见 library.md
├── exceptions.py      # 异常体系：OmniMDError → ConfigError / UnsupportedFormatError
├── cli.py            # 命令行入口：参数解析、配置加载、逐文件转换调度
├── config.py         # 配置加载与校验（连接配置只认 .env，缺啥报啥）
├── converter.py      # 转换编排：参数编译层（纯函数）+ 管线（解析→注册→处理→渲染）+ 类型路由缺省，并发安全
├── images.py         # 图片资产：装饰过滤、SHA-256 去重、双层缓存、上传调度
├── prompts/          # 任务注册表 + 提示词母本：任务知识唯一注册处
│   ├── registry.py   #   TaskSpec / TaskRegistry / build_registry / task_digest / 通用骨架 / 任务文件解析
│   ├── transcribe.py #   完整转写母本（内置任务）
│   └── describe.py   #   一句话描述母本（内置任务）
├── engines/          # 解析引擎层
│   ├── __init__.py   #   resolve_engine 路由（图片与 PDF 固定；其余按 DOC_ENGINE，格式回退）+ 格式面定义处
│   ├── base.py       #   契约：convert(file_path, store) -> str（引擎不落盘）
│   ├── image_engine.py    # 单张图片输入：注册为占位符
│   ├── pymupdf_engine.py  # PDF：pymupdf4llm，embed_images 内嵌图片
│   ├── markitdown_engine.py  # 缺省引擎：markitdown（格式面最宽）
│   └── anydoc_engine.py  # 质量选项：anydoc（to_document + 自渲染）
├── vlm/              # 视觉模型层（纯协议：零任务知识）
│   ├── __init__.py   #   create_vlm 工厂（骨架类延迟导入，防循环依赖）
│   ├── base.py       #   契约：run(VisionRequest, image_ref) -> CompletionResult + Usage
│   ├── thinking.py   #   思考控制方式 → extra_body 参数表（注册表 THINKING_EXTRA_BODY 即取值枚举）
│   └── openai_compat.py  # OpenAI Chat Completions 唯一实现（四条工程立场见 §2.2）
└── storage/          # 文件存储层
    ├── __init__.py   #   create_uploader（必填五项缺一报全清单）
    ├── base.py       #   契约：upload_image(image_data, filename) -> url
    └── s3.py         #   S3 兼容（MinIO/OBS/OSS/COS 一套实现）

tests/                # 回归测试（python -m pytest tests/），按被测形态分目录：
├── library/         #   Python 库：config（校验分支/超时/凭据惰性）/ registry（任务注册表/文件发现）
│                    #   / compile（参数编译层纯函数）/ render+anydoc（渲染纯函数）
│                    #   / images（资产库/缓存隔离）/ vlm（纯协议契约/状态码转译）
│                    #   / pipeline（组合矩阵/类型路由/用量汇总）/ api（公开接口）
│                    #   / benchmark（性能基准）/ security（密钥不变量）
├── cli/             #   CLI 参数解析、退出码、迁移报错与产出落盘
└── demo/            #   demo API 契约与路径遍历防护（stub 转换器，不联网）
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
| 转写质量依赖 VLM | 提示词要求无损转写（表格/流程图/界面逐项还原），但模型能力决定上限；不确定性的识别不依赖模型自评——验证管线（转写与校对分离、代码裁决）在规划中 |
| 缓存目录随模型与任务增长 | 每换一个模型、思考控制方式或任务提示词，对应任务目录下新增一个子目录并全量重新生成（属隔离设计的预期行为——便于对比评估） |
| 单进程 | 无服务化、无 API——CLI 与 Python 库即核心使用面（demo/ 与 skills/ 是基于库的演示/集成形态） |
| anydoc 引擎外链图不转写 | 文档内的外链图已是公开 URL，原样保留不进托管/任务管线 |
| 传图方式由呈现值决定 | link 呈现走公开链接传图（存储与任务解耦）；drop / embed 直传 base64 可不配存储运行；文字类裸命令不处理图片 |
