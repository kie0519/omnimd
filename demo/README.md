# omnimd demo

任意文件 → Markdown 的最小完整产品演示：FastAPI 后端 + React 前端。
单进程启动，一次走完「上传 → 转换 → 渲染 → 下载」全流程。

## 前置条件

- Python ≥ 3.10，已安装 omnimd（`pip install omnimd`；参与开发则仓库根执行 `pip install -e .`）
- `.env`（项目根）已配置 VLM / 存储凭据——转换依赖真实服务
- Node.js ≥ 18（仅开发/构建前端需要）

## 运行

**方式一：启动脚本（推荐，在仓库根执行）**

```bash
./start.sh                   # start：按需构建前端 → 单进程启动，访问 http://127.0.0.1:8300
./start.sh status            # 查看运行状态与健康检查
./start.sh restart           # 重启（自动替换旧进程）
./start.sh stop              # 停止
./start.sh dev               # 开发模式：后端 --reload + Vite 5173（/api 代理）
./start.sh logs              # 跟随后端日志
```

脚本位于仓库根；Linux / macOS / Windows Git Bash 均可运行。自带环境预检
（Python / 后端依赖 / omnimd 库）、前端依赖按需安装（`node_modules` 缺失或
`package-lock.json` 变更时才 `npm install`）、增量构建（源码未变跳过
`npm run build`）、启动后健康检查把关、孤儿进程兜底清理。可用 `PORT=8302` /
`HOST=0.0.0.0` / `PYTHON=python3` 环境变量覆盖缺省值。

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

开发模式（可选）：`./start.sh dev`，或手动 `cd demo/frontend && npm run dev`
后访问 5173 端口，`/api` 已代理到 8300；后端 CORS 已放行该来源。

## 接口行为

- `POST /api/tasks`：202 创建；不支持扩展名 400（附完整支持列表）；
  空文件 400；超 50MB 400；并发占满（running+pending ≥ 3）429
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
