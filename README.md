<p align="center">
  <img src="https://raw.githubusercontent.com/kie0519/omnimd/main/docs/assets/logo.png" width="136" alt="omnimd logo">
</p>

<h1 align="center">omnimd</h1>

<p align="center">
  任意文件 → 信息完整的 Markdown
</p>

<p align="center">
  <a href="https://github.com/kie0519/omnimd/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/kie0519/omnimd/tests.yml" alt="tests"></a>
  <a href="https://pypi.org/project/omnimd/"><img src="https://img.shields.io/pypi/v/omnimd?color=0f6b6b" alt="pypi version"></a>
  <a href="https://github.com/kie0519/omnimd/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-2e7d43" alt="license MIT"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776ab" alt="python">
  <img src="https://img.shields.io/badge/formats-38-8a6b15" alt="formats">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-5c6470" alt="platform">
</p>

PDF、Office 文档、图片等 38 种格式，统一转换为一份信息完整的 Markdown：正文逐字保留，图片既有可点开的公开链接或内嵌形态，也有可检索的转写文本。转换结果可直接进入 coding agent 的上下文、RAG 知识库与检索系统。

文档里最直观的信息往往以图片形式承载——架构图、界面截图、参数表。转换时若直接丢弃，模型与检索系统拿到的语料便不完整。omnimd 在解析之外补全图片链路：托管、转写、去重、缓存，让一份 Markdown 同时满足人读与机器读。

## 转换效果

一份带架构图的 PDF（`--images link --transcribe --describe`），转换后在图片位置产出：

```markdown
![图片：系统架构图——三个模块的连接关系与数据流向](https://<存储域名>/<桶名>/<前缀><sha256>.png)
**图：系统架构**
- 接入网关 → 业务核心：HTTPS，标注「同步调用」
- 业务核心 → 数据层：PostgreSQL（持久化）与 Redis（缓存）两条连接线
- 各模块下方标注实例数：网关 ×2、核心 ×4、数据层 ×2
```

一张柱状图（`--images link --transcribe`），转换后逐点成为可检索的数据：

```markdown
![图片](https://<存储域名>/<桶名>/<前缀><sha256>.png)
- 图表：2026 年季度营收（单位：万元，10k CNY）
- 图表类型：柱状图（蓝色柱形）
- Y 轴：数值，刻度 0–400，间隔 100
- X 轴：季度（Q1、Q2、Q3、Q4）
- 数据：
  - Q1: 230
  - Q2: 310
  - Q3: 285
  - Q4: 360
- 趋势：Q1 至 Q2 上升，Q3 回落，Q4 达到全年最高
```

正文逐字保留，图片挂在对象存储上随时可点开；架构图的模块关系、图表的轴刻度与数据点都进入文本——图片不再是检索盲区，RAG 与分析工具可直接取用。

## 特性

- **两组选项自由搭配**——图片怎么放（丢弃 / 链接 / 内嵌）与对图片做什么（转写 / 描述 / 自定义提示词）独立选择，全不传时按文件类型处理
- **不带图片参数也能转**——文档只提取正文，不需要任何凭据；图片文件自动完整转写
- **自定义图片任务**——`--prompt` 引用 `prompts/` 目录的任务文件或直接写一段提示词，走查、配色分析等任意意图开箱即得
- **38 种输入格式**——PDF 走图片保真管线，文档引擎可切换，图片直接输入
- **转写按图分策略**——表格逐格、图表逐点、图示重建 Mermaid
- **双层缓存**——同配置重跑，不调模型、不上传
- **内容寻址去重**——同一张图只上传、转写一次，多处引用共享 URL
- **引擎、模型、存储均可切换**——改 `.env` 即换端点，不改代码

## 环境要求

- Python ≥ 3.10
- 视觉模型（VLM）：任意 OpenAI 兼容端点（OpenAI / 智谱 GLM / 本地 Ollama / vLLM 等）；本地端点用 `--images embed` 即可（base64 直传，天然兼容）
- 对象存储：S3 兼容（MinIO / OBS / OSS / COS 等，自托管或云厂商）；用 `--images link` 时需要，内嵌或不带图片参数时不接触

## 安装

```bash
pip install omnimd           # 或：uv tool install omnimd
```

运行前需在运行目录配置 `.env`（见[配置](#配置)，仓库内附带 `.env.example` 模板）；从源码安装见[参与开发](#参与开发)。

## 快速开始

```bash
omnimd <文件路径>.pdf -o output/                 # 文档只提取正文，不需要任何配置
omnimd <截图>.png -o output/                     # 图片自动完整转写（需要 .env 视觉模型配置）
omnimd <输入目录>/ -o output/                    # 目录批处理，图片与文档可混排
omnimd <文件路径>.pdf -o output/ --images link --transcribe   # 正文 + 图片链接 + 转写文本
```

## 图片怎么处理：两组选项

**呈现——图片在产出里怎么放**（`--images`，缺省丢弃）：

| 取值 | 产出形态 | 需要 | 成本 |
|---|---|---|---|
| （不传） | 图片不保留，只有文字 | 无 | 零 |
| `link` | `![描述](公开链接)`——图片可看 | 对象存储 | 上传一次（同图去重，重跑不再上传） |
| `embed` | `![描述](data:image/...)`——内嵌自包含 | 无 | 产出体积膨胀 |

**任务——对图片做什么**（可并存）：

| 参数 | 产出 | 成本 |
|---|---|---|
| `--transcribe` | 每图完整转写——图片全部信息的 Markdown，内容可检索 | 每图一次模型调用 |
| `--describe` | 每图一句话身份——填入图片链接的描述位（需 `--images`） | 每图一次模型调用 |
| `--prompt <提示词或任务名>` | 提示词驱动——你说了算；可重复、可引用自定义任务 | 每任务每图一次调用 |

两组选项自由组合：`--images link --transcribe`（知识库全量）、`--transcribe`（不留图，每图变文字的纯文本库）、`--images embed --prompt walk`（设计稿变走查文档）……各参数各自生效，组合就是效果相加。

## 自定义图片任务

一个任务就是一段提示词。三种来源按场景选：

- **任务文件**——工作目录 `prompts/<任务名>.md`，正文写提示词，`--prompt <任务名>` 引用；frontmatter 可选 `name`（缺省文件名）与 `system`（缺省用内置通用骨架）。适合命名的、可复用的意图：

  ````markdown
  ---
  name: walk
  system: |
    你是资深 UI/UX 走查专家，只陈述界面可见内容。
  ---
  完整走查这个界面的布局结构、组件清单与配色体系……
  ````
- **即时提示词**——`--prompt "重点转写图表数值"`，一句话当场生效
- **代码定义**——库侧 `TaskPrompt(name="walk", prompt="…")`，任务随代码管理

文件定义同名任务可覆盖内置（提示词就是任务的本体）；`--transcribe` / `--describe` 是 `--prompt transcribe` / `--prompt describe` 的快捷写法——高频路径一键直达。

## 使用方式

### CLI

适合命令行批处理与脚本集成。参数与支持格式：

| 参数 | 说明 |
|------|------|
| `-o, --out-dir` | 输出目录（缺省 `docs/`）；同批同名冲突自动加序号，同名重跑时覆盖刷新 |
| `--images` | 图片呈现：`link` 公开链接 / `embed` base64 内嵌；不传 = 丢弃图片只要文字 |
| `--transcribe` | 完整转写每张图片（等价 `--prompt transcribe`） |
| `--describe` | 一句话描述填入链接描述位（等价 `--prompt describe`；需 `--images`） |
| `--prompt` | 以提示词驱动图片任务：值是任务名（内置或 `prompts/` 目录文件）时引用该任务，否则作为提示词文本即时生效；可重复 |
| `--concurrency` | 图片任务并发数，缺省读 `VLM_CONCURRENCY`（未配置时为 8） |
| `--cache-dir` | 缓存目录（任务产物/URL 缓存；相对路径相对当前目录），缺省读 `CACHE_DIR`（未配置为系统用户缓存目录） |
| `-v` | 调试日志 |

**支持格式**（38 种）：
文档 `.pdf .docx .docm .doc .odt .ods .odp .ppt .pptx .pptm .ppsx .ppsm .rtf .epub .xlsx .xlsm .xlsb .xls .csv .html .htm .json .xml .zip .txt .md .rst .ipynb`
图片 `.png .jpg .jpeg .gif .bmp .webp .tif .tiff .svg .ico`

### Python 库

适合应用集成与数据管道。使用节奏：构造客户端时一次配齐连接配置（缺省读环境变量 / .env），之后每次 `convert()` 只传文件与本次的图片选项（参数与 CLI 同名同义）：

```python
from omnimd import OmniMD, VlmConfig, S3Config, TaskPrompt

md = OmniMD()                               # 方式一：读环境变量 / .env
md = OmniMD(                                # 方式二：显式传连接配置
    vlm=VlmConfig(api_key="<密钥>", base_url="<接口地址>", model="<模型名>"),
    storage=S3Config(endpoint="<S3 端点>", region="<区域>", bucket="<桶名>",
                     access_key_id="<AK>", secret_access_key="<SK>"),
)

result = md.convert("<文件路径>.pdf")          # → ConversionResult；不带图片参数时文档只提取正文
result.markdown                              # 产出全文（str）
result.images                               # 图片清单：sha256 / url / texts（各任务产物）
result.image_count, result.char_count, result.elapsed_ms   # 转换统计
md.convert("<手册>.pdf", images="link", transcribe=True)    # 正文 + 链接 + 转写

walk = TaskPrompt(name="walk", prompt="完整走查这个界面的布局、组件与配色")
md2 = OmniMD(tasks=[walk])                   # 任务随代码管理（prompt 可引用）
md2.convert("<截图>.png", prompt=[walk, "重点看配色"])       # 任务可叠加
results = md2.convert_many(["<文件路径>.pdf", "<输入目录>/"])  # 批量
```

实例可重复使用；单文件失败在 `convert_many` 的结果中为异常对象，`convert` 抛 `OmniMDError` 子类（配置/格式错误）或上游原始异常。可运行示例见 [examples/](https://github.com/kie0519/omnimd/tree/main/examples)（脚本直调与服务化封装两种形态），完整接口说明见 [library.md](https://github.com/kie0519/omnimd/blob/main/docs/library.md)。

### Claude Code skill

适合在 Claude Code 对话里一句话转换文件——skill 部署后直接说「把这个 PDF 转成 Markdown」。skill 是纯指令形态（教 Agent 安装、配置与调用 `omnimd` 命令；工具本体发布在 PyPI），目录在本仓库 [skills/omnimd/](https://github.com/kie0519/omnimd/tree/main/skills/omnimd)，复制到 Claude Code 的 skill 目录即可使用：用户级 `~/.claude/skills/`（任意会话可用）或项目级 `<项目>/.claude/skills/`。仓库内开发可一键部署：

```bash
python scripts/sync_skill.py            # 缺省 → 仓库内 .claude/skills/omnimd
python scripts/sync_skill.py <目标目录>
```

使用前确保 `omnimd` 已安装（`pip install omnimd`），并在工作目录配置 `.env`（模板见 skill 目录的 `.env.example`）。指令与参数映射见 [skills/omnimd/SKILL.md](https://github.com/kie0519/omnimd/blob/main/skills/omnimd/SKILL.md)。

### Web 演示

适合可视化浏览与快速体验。FastAPI + React 实现的完整产品演示——上传、转换、渲染、下载全流程可用，历史记录可搜索、可回看，支持一次多选批量转换：

<p align="center">
  <img src="https://raw.githubusercontent.com/kie0519/omnimd/main/docs/assets/demo.png" width="860" alt="omnimd Web 演示：转换结果视图，图片链接与转写文本并排呈现">
</p>

仓库根执行：

```bash
./start.sh start    # 按需构建前端 → 单进程启动，访问 http://127.0.0.1:8300
```

脚本跨平台（Linux / macOS / Windows Git Bash），另有 stop / status / logs 子命令（再次执行 start 即重启加载最新代码）；部署细节见 [demo/README.md](https://github.com/kie0519/omnimd/blob/main/demo/README.md)。

## 配置

复制 [.env.example](https://github.com/kie0519/omnimd/blob/main/.env.example) 为 `.env` 填入密钥——每个变量的含义、可选值与缺省值都以该文件为准。`.env` 只放连接与环境（凭据、模型、思考方式、超时、并发、缓存）；图片怎么处理由命令参数决定——不带图片参数时任何环境跑出的结果都一样，纯文档转换不需要任何密钥；图片任务调用视觉模型时 VLM 凭据三项必填，首次此类转换时报错并列出全部缺失项。骨架：

```ini
# ===== 视觉模型（图片任务时必填）=====
VLM_API_KEY=<密钥>
VLM_BASE_URL=<接口地址>
VLM_MODEL=<模型名>
VLM_TIMEOUT=300       # 单次调用超时（秒）
```

存储按需配置：`--images link` 时需要；用内嵌或不带图片参数时可免。

`VLM_THINKING` 控制思考参数的下发方式——OpenAI 兼容生态有多种协议形态，取值与端点适配速查见 [.env.example](https://github.com/kie0519/omnimd/blob/main/.env.example)。两点说明：转写是提取任务，通常关闭思考（`*-off`）以省输出预算，开启（`*-on`）用于复杂图表的质量实验；OpenAI 官方推理模型（o 系 / GPT-5）要求 `max_completion_tokens` 而非 `max_tokens`，与本实现不兼容。

## 处理管线

一份文件经四步成为 Markdown：

1. **解析**——正文提取为 Markdown。PDF 固定走 pymupdf4llm（页面图片保真导出），10 种图片格式固定走图片引擎，其余 27 种文档格式按 `DOC_ENGINE` 选定引擎
2. **图片注册**——短边小于 60px 的装饰图丢弃；其余按 SHA-256 内容寻址去重，同一张图只处理一次，多处引用共享
3. **上传与任务**——`--images link` 时图片并发上传至对象存储取得公开 URL；指定了转写 / 描述 / `--prompt` 时每图并发调用视觉模型，图以 URL 或 base64 传入（link 传 URL，其余传 base64；既无 `--images` 也无任务时跳过本步）
4. **渲染写出**——按呈现与任务拼装图片块或任务文本块，与正文合成写入输出目录

成本模型：首次转换每任务每图一次模型调用、链接呈现再每图一次上传（纯解析两者皆无）；同配置重跑由双层缓存全数命中，不调模型、不上传。缓存按任务隔离（模型、思考控制方式、任务与提示词摘要进缓存键）——换模型即重转，改提示词即失效，同图不同任务不串缓存。设计细节见[架构文档](https://github.com/kie0519/omnimd/blob/main/docs/architecture.md)。

## 路线图

| 方向 | 状态 |
|---|---|
| 文档与图片 → 信息完整的 Markdown（38 种格式） | ✅ 现已支持 |
| 转写验证管线（转写与校对分离、代码裁决） | 规划中 |
| 场景化任务包（走查、解读、提取、诊断、转码等预置任务文件） | 已随 Web 演示提供，PyPI 分发规划中 |
| 音频转写（ASR 文本） | 规划中 |
| 视频转写（抽帧描述 + 音轨转写） | 规划中 |

详见 [ROADMAP.md](https://github.com/kie0519/omnimd/blob/main/ROADMAP.md)。

## 文档

| 想了解 | 去哪 |
|---|---|
| 产品规格、产出契约与验收标准 | [prd.md](https://github.com/kie0519/omnimd/blob/main/docs/prd.md) |
| Python 库完整接口（构造 / 转换 / 错误处理） | [library.md](https://github.com/kie0519/omnimd/blob/main/docs/library.md) |
| 架构、缓存体系与代码地图 | [architecture.md](https://github.com/kie0519/omnimd/blob/main/docs/architecture.md) |

## 参与开发

欢迎经 [issue](https://github.com/kie0519/omnimd/issues) 报告问题、以 PR 递交改进。本地开发环境的搭建与验证：

```bash
pip install -e ".[dev]"        # 库本体 + 测试依赖（Python ≥ 3.10）
python -m pytest tests/        # 全量回归，不联网：上传器与视觉模型均为 stub
```

PR 合并前 CI 需全绿——每个 push 运行三操作系统 × 四 Python 版本的测试矩阵，外加 demo 前端的类型检查与构建。改动涉及前端时，先在 `demo/frontend/` 本地执行 `npx tsc --noEmit && npx vite build` 完成同样验证。

## 致谢

解析能力基于三个开源项目：[markitdown](https://pypi.org/project/markitdown/)、[pymupdf4llm](https://pypi.org/project/pymupdf4llm/)、[firecrawl-anydoc](https://pypi.org/project/firecrawl-anydoc/)。

## License

[MIT](https://github.com/kie0519/omnimd/blob/main/LICENSE)
