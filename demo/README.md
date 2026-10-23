# omnimd demo

任意文件 → Markdown 的最小完整产品演示：FastAPI 后端 + React 前端。单进程启动，一次走完「上传 → 转换 → 渲染 → 下载」全流程。

## 前置条件

- Python ≥ 3.10，已安装 omnimd（`pip install omnimd`；参与开发则仓库根执行 `pip install -e .`）
- `.env`（项目根）按产出组成配置：纯解析无需任何凭据；转写/描述需 VLM 凭据，图片链接需存储配置（首次此类转换时校验）
- Node.js ≥ 18（仅开发/构建前端需要）

## 运行

**方式一：启动脚本（推荐，在仓库根执行）**

```bash
./start.sh                   # 以当前代码运行：未运行则启动，已运行则重启（按需构建前端）
./start.sh stop              # 停止服务（已停止则无事发生）
./start.sh status            # 查看运行状态与健康检查
./start.sh logs              # 跟随后端日志
```

重复执行同一条命令是安全的：`start` 在服务已运行时自动重启，`stop` 在服务已停止时直接成功。脚本位于仓库根；本地与服务器、Linux / macOS /Windows Git Bash 均可运行。启动前做环境预检（Python / 后端依赖 /omnimd 库，缺失时给出修复命令），前端依赖与构建按需执行（日常启动零开销），启动后经健康检查确认就绪，异常退出的残留进程自动清理。可用 `PORT=8302` /`APP_HOST=0.0.0.0` / `PYTHON=python3` 环境变量覆盖缺省值。

**方式二：手动**

```bash
# 1. 后端依赖
pip install -r demo/backend/requirements.txt

# 2. 前端构建（生产形态：后端直接托管 dist）
cd demo/frontend && npm install && npm run build && cd ../..

# 3. 启动（单进程，端口 8300）
cd demo/backend && python -m uvicorn main:app --port 8300
```

打开 <http://localhost:8300>。

## 接口行为

- `POST /api/tasks`：202 创建；`output` Form 字段为产出组成（五部件
  规范序 CSV，缺省 `body` 纯解析），非法取值 / url 与 base64 同选 /ocr·alt 缺呈现部件均 400（附完整可选取值）；不支持扩展名 400（附完整支持列表）；空文件 400；超 50MB 400；并发占满（running+pending ≥ 3）429
- `GET /api/health`：`{"status": "ok"}`——纯探活，给部署检查与前端
  离线检测反复调用
- `GET /api/formats`：`{"formats": [...]}`——支持格式（能力数据），
  前端上传预检与格式提示共用
- `GET /api/tasks`：列表 + 搜索（`q` 文件名子串，不区分大小写）；
  进行中记录统计字段（含 cache_hit）为 null
- `GET /api/tasks/{id}`：详情含 markdown/error；404 未知 ID；
  时间戳 ISO 8601 UTC（`T` 分隔、`Z` 后缀）
- `DELETE /api/tasks/{id}`：204 删除；重复删除 404
- 状态机 pending → running → done/failed；服务重启后遗留任务标记为
  失败（错误信息「服务重启导致任务中断，请重新上传该文件转换。」），不会永久转圈
- 上传的临时文件在任务终态后即删（finally 保证）
- 生产模式：`demo/frontend/dist` 存在时后端直接托管前端，单进程完整演示

## 数据与产物

- SQLite 库：`demo/data/demo.db`（`DEMO_DB` 环境变量可覆盖）
- 上传临时文件：系统 temp 目录，转换完成即删
