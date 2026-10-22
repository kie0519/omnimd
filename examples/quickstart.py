"""omnimd Python 库调用示例。

依赖安装：pip install omnimd
直接运行：python quickstart.py（在本文件所在目录执行）
配置说明：段 1 与段 2 无需任何凭据；段 3 起需要 .env（模板见仓库 .env.example）——未配置时库在对应段报错并列出缺失项
换成自己的文件：改下方 INPUT / IMAGE / BATCH_DIR
HTTP 接口封装见同目录 api.py
"""
from pathlib import Path

from omnimd import OmniMD, ConversionResult

# ========== 配置 ==========
# 文档输入（段 1 / 段 2）：缺省用仓库自带的 ROADMAP.md
INPUT = Path(__file__).resolve().parent.parent / "ROADMAP.md"
# 图片输入（段 3 / 段 4）：缺省用仓库自带的演示截图
IMAGE = Path(__file__).resolve().parent.parent / "docs" / "assets" / "demo.png"
# 批量目录（段 2）：缺省用仓库 docs/ 目录
BATCH_DIR = Path(__file__).resolve().parent.parent / "docs"

md = OmniMD()  # 构造一次，之后所有转换复用（引擎与双层缓存在实例内共享）


def show(result: ConversionResult, title: str) -> None:
    print(f"\n===== {title} =====")
    print(f"字符数 {result.char_count} | 图片 {result.image_count} 张 | 耗时 {result.elapsed_ms}ms")
    print("产出前 80 字：", result.markdown[:80].replace("\n", " "))


# ========== 1. 纯解析：无需凭据，正文即产出 ==========
result = md.convert(INPUT, output="body")
show(result, "1. 纯解析（output=body）")

# ========== 2. 目录批量：单文件失败不中断 ==========
# convert_many 返回 {路径: 结果}；失败文件的值是异常对象本身（不吞不改），调用方按 isinstance 分流
results = md.convert_many([BATCH_DIR])
print("\n===== 2. 批量与失败隔离 =====")
for path, r in results.items():
    kind = "成功" if isinstance(r, ConversionResult) else f"失败（{r}）"
    print(f"  {Path(path).name}: {kind}")

# ========== 3. 图片转写，不配存储：产出自包含 ==========
# body,base64,ocr = 正文 + 图片 base64 内嵌 + 转写文本——单文件自带全部内容，离线可读；图片以 base64 直传视觉模型，天然兼容本地端点（Ollama 等）
# 前置：VLM 凭据三项（.env）
result = md.convert(IMAGE, output="body,base64,ocr")
show(result, "3. 免存储转写（body,base64,ocr）")
# images 字段：每张图片的结构化元数据（程序消费方免于解析 Markdown）
for img in result.images:
    print(f"  图片 sha256={img.sha256[:12]}… ocr={img.ocr_text[:30]!r} alt={img.alt_text[:30]!r}")

# ========== 4. 图片公开链接 + 转写 + 描述：全形态 ==========
# body,url,ocr,alt——图片上传对象存储换取长期公开链接，转写与描述齐备；链接可分享、可嵌入任何 Markdown 渲染器
# 前置：VLM + 存储配置（.env）
result = md.convert(IMAGE, output="body,url,ocr,alt")
show(result, "4. 全形态（body,url,ocr,alt）")
for img in result.images:
    print(f"  图片链接 {img.url}")
