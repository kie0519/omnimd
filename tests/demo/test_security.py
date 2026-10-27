"""安全不变量回归（demo）：上传文件名路径遍历防护的持续锁定。"""
import tempfile
from pathlib import Path


def test_upload_filename_traversal_rejected(client):
    """上传文件名带路径遍历（../../x.txt）：扩展名提取按最后一个点，恶意路径形态不得 5xx、不得在临时目录越界落盘——被格式校验拒绝或以 uuid 文件名安全落盘"""
    resp = client.post(
        "/api/tasks",
        files={"file": ("../../etc/passwd.txt", b"hello", "text/plain")},
        data={},
    )
    assert resp.status_code in (202, 400)

    tmp = Path(tempfile.gettempdir())
    leaked = [p for p in tmp.rglob("*") if "etc" in str(p) and p.is_file()
              and p.stat().st_mtime > (Path(__file__).stat().st_mtime - 60)]
    assert not leaked   # 无遍历落盘产物
