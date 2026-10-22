"""把 omnimd 封装成同步文件解析 API 的示例：上传文件 + 指定产出组成，等待转换完成，一次返回全部结果。

适合作为自有解析服务的最小骨架；带任务队列与历史管理的完整产品形态见仓库 demo/。
依赖安装：pip install omnimd fastapi uvicorn python-multipart
启动：在本文件所在目录执行 uvicorn api:app --port 8000；凭据从运行目录的 .env 读取（模板见仓库 .env.example）
调用：curl -F "file=@doc.pdf" -F "output=body,url,ocr" http://localhost:8000/convert
"""
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile

from omnimd import OmniMD
from omnimd.config import ConfigError, parse_output
from omnimd.exceptions import UnsupportedFormatError

app = FastAPI(title="omnimd file parsing API")

# 进程级单例：构造一次跨请求复用，引擎与双层缓存在实例内共享
md = OmniMD()


@app.post("/convert")
def convert(file: UploadFile, output: str = Form("body")) -> dict:
    """同步转换：上传文件，等待完成，返回完整结果。

    入参：file（multipart 文件）；output（产出组成，逗号分隔多选，与库同源校验，缺省 body 纯解析）。
    出参：markdown（产出全文）；images（每张图片的结构化元数据）；image_count / char_count / elapsed_ms（统计）。
    """
    try:
        output = parse_output(output)
    except ConfigError as e:
        raise HTTPException(400, str(e))

    # 上传落唯一命名的临时文件（convert 收文件路径），转换完成即删——服务器不留用户文件
    import os
    fd, tmp_path = tempfile.mkstemp(suffix=Path(file.filename or "").suffix, prefix="omnimd-api-")
    os.close(fd)
    tmp = Path(tmp_path)
    with tmp.open("wb") as out:
        out.write(file.file.read())
    try:
        result = md.convert(tmp, output=output)
    except UnsupportedFormatError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "markdown": result.markdown,
        "images": [
            {"sha256": img.sha256, "url": img.url, "alt_text": img.alt_text, "ocr_text": img.ocr_text}
            for img in result.images
        ],
        "image_count": result.image_count,
        "char_count": result.char_count,
        "elapsed_ms": result.elapsed_ms,
    }
