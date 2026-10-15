---
name: omnimd
description: 文件转 Markdown。当用户要求把 PDF/DOCX/PPTX/XLSX 等任意文件转成 Markdown、提取文件正文与图片、转写图片内容、为 RAG/知识库/Agent 上下文准备语料时使用。需要本机 Python 3.10+ 与已配置的 VLM 凭据（图片链接产出另需存储凭据）。
---

# 文件转 Markdown（omnimd）

任意文件转 Markdown：正文、图片链接、图片转写文本三合一产出。
图片既可看（对象存储公开链接），内容也可检索（视觉模型转写的文本块）。

## 首次使用（一次性）

1. 确认本机 Python ≥ 3.10；
2. 执行 `python "${CLAUDE_SKILL_DIR}/scripts/setup.py"`——安装 omnimd 包，
   并在 skill 目录生成 `.env` 模板（幂等，可重复执行）；
3. 编辑 `${CLAUDE_SKILL_DIR}/.env` 填入：
   - `VLM_API_KEY` / `VLM_BASE_URL` / `VLM_MODEL`（凭据三项，任意 OpenAI 兼容端点
     直接接入；`VLM_THINKING` 可选——思考控制方式，不设置即 none）；
   - 存储凭据：`STORAGE_PROVIDER=s3` 填 `S3_*` 系列；
4. 运行 `python "${CLAUDE_SKILL_DIR}/scripts/run.py" --help` 自检，
   能打印用法即就绪。

## 前置确认（执行命令前确认，未确认先引导完成）

确认 `.env` 已配置（首次使用时按上节配置）。缺配置的报错分两处，都列出
缺失项与补救方式——出现该报错时引导用户补齐后重跑，不要重试：

- 缺 VLM 凭据三项（KEY/URL/MODEL）→ 启动直接报错；
- 缺存储配置 → 首次需要上传图片时报错（产出含链接，或以 URL 传图时）。

## 执行

```bash
# 单个文件（产出 <输出目录>/<文件名>.md）
python "${CLAUDE_SKILL_DIR}/scripts/run.py" <文件路径>.pdf -o <输出目录>/

# 整个目录（按名排序逐个转换，可同时传多个路径）
python "${CLAUDE_SKILL_DIR}/scripts/run.py" <输入目录>/ -o <输出目录>/

# 可选控制（续行行尾不放注释，参数说明见命令下方）
python "${CLAUDE_SKILL_DIR}/scripts/run.py" <文件路径> -o <输出目录>/ \
  --output url \
  --concurrency 4
# --output：产出组成，逗号分隔多选——url=图片链接，text=转写文本，
#   alt=链接描述（依赖 url）；缺省读 .env 的 OUTPUT，未设置为 url,text
# --concurrency：图片转写并发数
```

自然语言 → 参数映射：
用户说「图片只要链接、别转写」→ `--output url`；
「只要转写文本，不要链接」→ `--output text`；
「图片和转写都要」→ `--output url,text`（或不传该参数，即缺省）；
「链接要有描述文字」→ `--output url,alt`；
「并发低一点/高一点」→ `--concurrency 4` / `--concurrency 16`。

输出目录始终显式传 `-o`（缺省会落到当前目录的 `docs/` 下）。

## 输出处理

- 每个输入文件产出 `<输出目录>/<文件名>.md`（同批同名冲突自动加序号
  `a-1.md`，以日志「已写入」行显示的实际路径为准）；把产出路径转述给用户；
- 单文件转换失败不影响其余文件：失败文件打印异常日志，退出码非 0。
  向用户分开报告成功与失败清单；
- 不支持的格式一次性报全（错误信息列出全部不支持的文件名与可选格式）；
- 转换大量文件或图片密集的文件耗时较长，超过 10 分钟时改用后台方式
  运行，完成后向用户汇报。

## 限制

- 切换（VLM 端点 / 存储后端 / 解析引擎）不提供命令行参数，只改 `.env`
  （凭据三项 KEY/URL/MODEL 随端点整体更换）；
- 不修改输入文件，产物只写入 `-o` 指定目录。
