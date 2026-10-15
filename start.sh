#!/bin/bash

# omnimd demo 启动脚本（仓库根执行；跨平台：Linux / macOS / Windows Git Bash）
#
# 用法: ./start.sh [start|stop|restart|status|dev|logs]
#   start    生产模式：按需安装前端依赖并构建 → 单进程启动后端（托管 dist），访问 :8300
#   stop     停止服务
#   restart  stop + start
#   status   查看运行状态（PID / 端口 / 健康检查）
#   dev      开发模式：后端 8300 (--reload) + 前端 Vite 5173（/api 代理到 8300）
#   logs     跟随后端日志
#
# 可用环境变量覆盖：PORT（后端端口，缺省 8300）、HOST（监听地址，缺省 127.0.0.1）、
# PYTHON（Python 解释器，缺省自动探测 python / python3）
#
# 跨平台说明：进程探测按 lsof → ss → netstat 逐级退化（Git Bash 无 lsof，
# 用 Windows netstat；Linux 无 lsof 时用 ss）；杀进程 kill 优先、taskkill 兜底。

APP_NAME="omnimd demo"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

BACKEND_DIR="$BASE_DIR/demo/backend"
FRONTEND_DIR="$BASE_DIR/demo/frontend"
LOGS_DIR="$BASE_DIR/demo/logs"
PID_FILE="$LOGS_DIR/app.pid"
BACKEND_LOG="$LOGS_DIR/backend.log"
FRONTEND_LOG="$LOGS_DIR/frontend.log"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8300}"
DEV_FE_PORT=5173
DIST_DIR="$FRONTEND_DIR/dist"
# 依赖安装戳：记录上次 npm install 完成时刻。放在 node_modules 里——
# 目录被删/被 npm ci 清空时戳随之消失，自然触发重装，无需额外清理
NPM_STAMP="$FRONTEND_DIR/node_modules/.omnimd-install-stamp"

mkdir -p "$LOGS_DIR"

# ── 解释器探测（Linux 发行版常只有 python3）──────────────

if [ -z "${PYTHON:-}" ]; then
    if command -v python >/dev/null 2>&1; then
        PYTHON=python
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    else
        echo "[错误] 未找到 python（可用 PYTHON=<路径> 环境变量指定）"
        exit 1
    fi
fi

# ── 进程与端口管理 ────────────────────────────────────────

# 读 PID 文件：key=value 行（backend=123 / frontend=456）
pids_alive() {
    [ -f "$PID_FILE" ] || return 1
    # shellcheck disable=SC1090
    . "$PID_FILE"
    return 0
}

# 递归终止进程树（有 pgrep 时连子进程一起杀；没有则只杀主进程）
kill_tree() {
    local pid="$1"
    [ -z "$pid" ] && return 0
    if command -v pgrep >/dev/null 2>&1; then
        local child
        for child in $(pgrep -P "$pid" 2>/dev/null); do
            kill_tree "$child"
        done
    fi
    kill "$pid" 2>/dev/null
    sleep 0.5
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || taskkill //F //PID "$pid" >/dev/null 2>&1
    fi
    return 0
}

# 端口占用检测：lsof → ss（Linux 常见）→ netstat（Windows）→ curl 探活
port_busy() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1 && lsof -ti :"$port" >/dev/null 2>&1; then
        return 0
    fi
    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$port "; then
        return 0
    fi
    # Windows netstat：状态列为 LISTENING（Linux 为 LISTEN，不会误匹配）
    if command -v netstat >/dev/null 2>&1 \
        && netstat -an 2>/dev/null | grep -E ":$port\b" | grep -q "LISTENING"; then
        return 0
    fi
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/" 2>/dev/null && return 0
    return 1
}

# 取占端口进程的 PID：lsof → Windows netstat（LISTENING 行末列）→ pgrep
port_pid() {
    local port="$1" pid
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti :"$port" 2>/dev/null | head -1
        return
    fi
    if command -v netstat >/dev/null 2>&1; then
        pid=$(netstat -ano 2>/dev/null | grep -E ":$port\b" | grep "LISTENING" \
            | head -1 | awk '{print $NF}')
        if [ -n "$pid" ]; then
            echo "$pid"
            return
        fi
    fi
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "uvicorn main:app.*--port $port" 2>/dev/null | head -1
    fi
}

# 按端口强杀（PID 文件丢失时的兜底），并等待端口释放。
# 仅当占端口进程确为本应用（uvicorn main:app）时才杀——避免误伤
# 恰好占用同一端口的无关服务；无法确认时提示人工处理。
kill_port() {
    local port="$1" pid
    pid=$(port_pid "$port")
    [ -z "$pid" ] && return 0
    local cmdline
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null ||
        powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"ProcessId=$pid\").CommandLine" 2>/dev/null | tr -d '\r' | grep -i uvicorn)
    if ! echo "$cmdline" | grep -qi "uvicorn\|main:app"; then
        echo "[警告] 端口 $port 被无关进程占用（PID $pid），请手动处理或改用 PORT=... 启动"
        return 1
    fi
    kill -9 "$pid" 2>/dev/null || taskkill //F //PID "$pid" >/dev/null 2>&1
    for _ in $(seq 1 10); do
        port_busy "$port" || return 0
        sleep 0.3
    done
    echo "[警告] 端口 $port 未能释放"
    return 1
}

# 等待健康检查通过；超时后打出日志尾部帮助排查
wait_healthy() {
    local url="http://127.0.0.1:$PORT/api/health"
    for i in $(seq 1 30); do
        if curl -sf -o /dev/null --max-time 2 "$url" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "[错误] 后端 30 秒内未通过健康检查（$url），日志尾部："
    tail -20 "$BACKEND_LOG"
    return 1
}

# ── 环境预检 ──────────────────────────────────────────────

check_env() {
    local missing=0
    if ! "$PYTHON" -c "import fastapi, uvicorn, multipart" >/dev/null 2>&1; then
        echo "[错误] 后端依赖缺失，请先在仓库根执行：pip install -r demo/backend/requirements.txt"
        missing=1
    fi
    if ! "$PYTHON" -c "import omnimd" >/dev/null 2>&1; then
        echo "[错误] 未安装 omnimd 库，请在仓库根执行：pip install -e ."
        missing=1
    fi
    [ "$missing" = 1 ] && exit 1
}

check_node() {
    command -v npm >/dev/null 2>&1 || {
        echo "[错误] 未找到 npm（构建/开发模式需要 Node.js ≥ 18）"
        exit 1
    }
}

# ── 前端依赖与构建（按需）──────────────────────────────────

# 需要重新 npm install 吗：node_modules 缺失（首次/被清理），
# 或 package-lock.json 比安装戳新（依赖版本变更过，如 git pull 之后）
need_install() {
    [ -d "$FRONTEND_DIR/node_modules" ] || return 0
    [ -f "$NPM_STAMP" ] || return 0
    [ "$FRONTEND_DIR/package-lock.json" -nt "$NPM_STAMP" ]
}

# 按需安装依赖——新环境 clone 下来 ./start.sh 即一键启动；
# lockfile 未变时静默跳过，日常启动零开销
ensure_deps() {
    need_install || return 0
    check_node
    if [ -f "$NPM_STAMP" ]; then
        echo "[前端] package-lock.json 有更新，重新安装依赖..."
    else
        echo "[前端] 首次使用，安装依赖 (npm install)..."
    fi
    (cd "$FRONTEND_DIR" && npm install --no-fund --no-audit) || {
        echo "[错误] npm install 失败"
        exit 1
    }
    touch "$NPM_STAMP"
}

# 输出需要重新构建的原因；无输出（返回 1）表示无需构建。
# 触发条件：dist 缺失（首次构建/被清理），或 src/构建配置比 dist 产物新
build_reason() {
    if [ ! -f "$DIST_DIR/index.html" ]; then
        echo "dist 尚未构建"
        return 0
    fi
    local changed count first
    changed=$(find "$FRONTEND_DIR/src" "$FRONTEND_DIR/index.html" \
        "$FRONTEND_DIR/package.json" "$FRONTEND_DIR/package-lock.json" \
        "$FRONTEND_DIR/tsconfig.json" "$FRONTEND_DIR/vite.config.ts" \
        -type f -newer "$DIST_DIR/index.html" 2>/dev/null)
    [ -n "$changed" ] || return 1
    count=$(( $(printf '%s\n' "$changed" | grep -c .) ))
    first=$(printf '%s\n' "$changed" | head -1)
    first="${first#"$FRONTEND_DIR"/}"
    echo "$count 个源码/构建配置文件比 dist 新（如 $first）"
    return 0
}

ensure_frontend() {
    ensure_deps
    local reason
    reason=$(build_reason) || reason=""
    if [ -z "$reason" ]; then
        echo "[前端] 依赖与 dist 均已最新，跳过安装与构建"
        return 0
    fi
    check_node   # 需要构建时才要求 Node.js，报错指向根因而非「构建失败」
    echo "[前端] $reason，重新构建..."
    (cd "$FRONTEND_DIR" && npm run build) || {
        echo "[错误] 前端构建失败"
        exit 1
    }
}

# ── 动作 ──────────────────────────────────────────────────

do_stop() {
    pids_alive || true
    for name in frontend backend; do
        eval "pid=\${$name:-}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill_tree "$pid"
            echo "[$name] 已停止 (PID: $pid)"
        fi
    done
    # 兜底：PID 文件丢失但端口仍被占（脚本被外部杀过 / 上次异常退出）
    if port_busy "$PORT"; then
        kill_port "$PORT" && echo "[backend] 释放了无主进程占用的端口 $PORT"
    fi
    rm -f "$PID_FILE"
}

do_start() {
    echo "========== 启动 $APP_NAME =========="
    check_env
    do_stop
    ensure_frontend

    echo "[backend] 启动 uvicorn ($HOST:$PORT)..."
    cd "$BACKEND_DIR"
    nohup "$PYTHON" -m uvicorn main:app --host "$HOST" --port "$PORT" \
        > "$BACKEND_LOG" 2>&1 &
    backend_pid=$!
    cd - >/dev/null

    echo "backend=$backend_pid" > "$PID_FILE"

    if ! wait_healthy; then
        do_stop
        exit 1
    fi
    echo "[backend] 就绪 (PID: $backend_pid)，日志: $BACKEND_LOG"
    echo "======================================"
    echo "访问地址: http://$HOST:$PORT"
    echo "停止服务: ./start.sh stop"
}

do_dev() {
    echo "========== 开发模式 $APP_NAME =========="
    check_env
    check_node
    ensure_deps   # 开发模式的 npx vite 同样依赖 node_modules
    do_stop

    cd "$BACKEND_DIR"
    nohup "$PYTHON" -m uvicorn main:app --reload --host "$HOST" --port "$PORT" \
        > "$BACKEND_LOG" 2>&1 &
    backend_pid=$!
    cd "$FRONTEND_DIR"
    nohup npx vite --port "$DEV_FE_PORT" > "$FRONTEND_LOG" 2>&1 &
    frontend_pid=$!
    cd - >/dev/null

    printf 'backend=%s\nfrontend=%s\n' "$backend_pid" "$frontend_pid" > "$PID_FILE"

    if ! wait_healthy; then
        do_stop
        exit 1
    fi
    echo "[backend] http://$HOST:$PORT（--reload）日志: $BACKEND_LOG"
    echo "[frontend] http://localhost:$DEV_FE_PORT 日志: $FRONTEND_LOG"
    echo "======================================"
    echo "浏览器访问: http://localhost:$DEV_FE_PORT（/api 已代理到 $PORT）"
    echo "停止服务: ./start.sh stop"
}

do_status() {
    pids_alive || true
    local any_running=0
    for name in backend frontend; do
        eval "pid=\${$name:-}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "[$name] 运行中 (PID: $pid)"
            any_running=1
        else
            echo "[$name] 未在运行"
        fi
    done
    if [ "$any_running" = 1 ]; then
        if curl -sf -o /dev/null --max-time 2 "http://$HOST:$PORT/api/health" 2>/dev/null; then
            echo "[health] http://$HOST:$PORT/api/health → ok"
        fi
        # 生产模式由后端托管前端，本就没有独立 frontend 进程
        eval "fpid=\${frontend:-}"
        if [ -z "$fpid" ] || ! kill -0 "$fpid" 2>/dev/null; then
            echo "（生产模式下前端由后端托管，无独立前端进程属正常）"
        fi
    fi
}

do_logs() {
    if [ -f "$BACKEND_LOG" ]; then
        tail -f "$BACKEND_LOG"
    else
        echo "暂无日志（$BACKEND_LOG 不存在）"
    fi
}

case "${1:-start}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    dev)     do_dev ;;
    logs)    do_logs ;;
    *)       echo "用法: $0 {start|stop|restart|status|dev|logs}" ;;
esac
