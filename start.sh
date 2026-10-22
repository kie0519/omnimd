#!/bin/bash
# omnimd demo 服务脚本（仓库根执行；跨平台：Linux / macOS / Windows Git Bash，本地与服务器同一份脚本同一套命令）
#
# 用法: ./start.sh [start|stop|status|logs]
#   start（缺省）  以当前代码运行服务：未运行则启动，已运行则重启
#   stop           停止服务（已停止则无事发生）
#   status         查看运行状态与健康检查（只读）
#   logs           跟随后端日志（只读）
#
# 可用环境变量覆盖：PORT（后端端口，缺省 8300）、APP_HOST（监听地址，缺省 127.0.0.1）、PYTHON（Python 解释器，缺省自动探测 python / python3）
#
# 跨平台说明：进程与端口探测按 lsof → ss → netstat 逐级退化（Git Bash 无 lsof，用 Windows netstat；Linux 无 lsof 时用 ss）；
# 停止服务时按进程树递归终止，脱离进程树的残留进程按端口归属反查清理
set -u   # 未定义变量即错：拼错的变量名在启动时就暴露，不静默展开为空

# 本脚本使用 bash 语法（数组等），用 sh 执行时给出正确执行方式
if [ -z "${BASH_VERSION:-}" ]; then
    echo "本脚本需要 bash 执行：./start.sh 或 bash start.sh"
    exit 1
fi

APP_NAME="omnimd demo"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

BACKEND_DIR="$BASE_DIR/demo/backend"
FRONTEND_DIR="$BASE_DIR/demo/frontend"
LOGS_DIR="$BASE_DIR/demo/logs"
PID_FILE="$LOGS_DIR/app.pid"
LOCK_DIR="$LOGS_DIR/.lock"
BACKEND_LOG="$LOGS_DIR/backend.log"

PORT="${PORT:-8300}"
APP_HOST="${APP_HOST:-127.0.0.1}"
DIST_DIR="$FRONTEND_DIR/dist"
# 依赖安装戳：记录上次 npm install 完成时刻。
# 放在 node_modules 里——目录被删/被 npm ci 清空时戳随之消失，自然触发重装，无需额外清理
NPM_STAMP="$FRONTEND_DIR/node_modules/.omnimd-install-stamp"

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

# PID 的命令行（空输出 = 进程不存在或不可查）。
# 重定向顺序：stderr 抑制须在 < 之前建立，否则 /proc 打开失败的报错会泄到终端（Windows 无 /proc，此路径常态触发）
proc_cmdline() {
    local pid="$1"
    tr '\0' ' ' 2>/dev/null < "/proc/$pid/cmdline" ||
        powershell -NoProfile -Command \
        "(Get-CimInstance Win32_Process -Filter \"ProcessId=$pid\").CommandLine" \
        2>/dev/null | tr -d '\r'
    return 0
}

# 校验 PID 对应进程确为本应用（uvicorn）——防服务停止后 PID 被系统复用给无关进程时的误杀
proc_is_ours() {
    proc_cmdline "$1" | grep -qi "uvicorn\|main:app"
}

# 校验 PID 是本脚本的另一个实例（锁持有者）
proc_is_launcher() {
    proc_cmdline "$1" | grep -q "start\.sh"
}

# 递归终止进程树。
# 有 pgrep 时逐子进程递归；没有（Git Bash）且是 Windows 时用 taskkill //T 整树终止；两者都不可用时退化为只杀主进程。
# 树杀漏掉的由 do_stop 的端口归属反查兜底
kill_tree() {
    local pid="$1"
    [ -z "$pid" ] && return 0
    # Git Bash 的 $! 是 MSYS pid，taskkill 只认 Windows pid——经 /proc 的 winpid 转换后再交 taskkill，否则整树终止静默失效
    local winpid
    winpid=$(cat "/proc/$pid/winpid" 2>/dev/null || echo "$pid")
    if command -v pgrep >/dev/null 2>&1; then
        local child
        for child in $(pgrep -P "$pid" 2>/dev/null); do
            kill_tree "$child"
        done
        kill "$pid" 2>/dev/null
    elif command -v taskkill >/dev/null 2>&1; then
        taskkill //F //T //PID "$winpid" >/dev/null 2>&1
        kill -0 "$pid" 2>/dev/null || return 0
        kill "$pid" 2>/dev/null
    else
        kill "$pid" 2>/dev/null
    fi
    sleep 0.5
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || taskkill //F //PID "$pid" >/dev/null 2>&1
    fi
    return 0
}

# 端口占用检测：lsof → ss（Linux 常见）→ netstat（Windows）→ curl 探活
port_busy() {
    local port="$1"
    # lsof 加 -sTCP:LISTEN：裸端口匹配会把到该端口的 ESTABLISHED 客户端连接也算占用（macOS 有 lsof 的平台会误报）
    if command -v lsof >/dev/null 2>&1 && lsof -ti :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
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
        lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null | head -1
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

# 按端口强杀（进程树杀不全时的兜底），并等待端口释放。
# 仅当占端口进程确为本应用（uvicorn）时才杀——避免误伤恰好占用同一端口的无关服务；无法确认时提示人工处理。
kill_port() {
    local port="$1" pid
    pid=$(port_pid "$port")
    [ -z "$pid" ] && return 0
    if ! proc_is_ours "$pid"; then
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

# 健康探测地址：跟随 APP_HOST——绑 0.0.0.0 时本地回环仍可达，用 127.0.0.1；绑具体网卡 IP 时只有该地址可达，必须用它探测
probe_host() {
    if [ "$APP_HOST" = "0.0.0.0" ] || [ "$APP_HOST" = "::" ]; then
        echo 127.0.0.1
    else
        echo "$APP_HOST"
    fi
}

# 等待健康检查通过。
# 进程中途退出（端口被非 HTTP 服务占用、依赖崩溃）时立刻打日志尾部失败——不干等满 30 秒才报一个无关的超时
wait_healthy() {
    local url="http://$(probe_host):$PORT/api/health" pid="$1"
    for _ in $(seq 1 30); do
        if curl -sf -o /dev/null --max-time 2 "$url" 2>/dev/null; then
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "[错误] 后端进程已退出（端口冲突或依赖异常），日志尾部："
            tail -20 "$BACKEND_LOG"
            return 1
        fi
        sleep 1
    done
    echo "[错误] 后端 30 秒内未通过健康检查（$url），日志尾部："
    tail -20 "$BACKEND_LOG"
    return 1
}

# ── 并发防护（变更类动作互斥；mkdir 是三平台唯一等价的原子锁原语）──

acquire_lock() {
    # 父目录（logs/）可能不存在（首跑/被清理）——先幂等就位；
    # 锁目录本身必须用裸 mkdir：-p 在已存在时也返回成功，会破坏「只有一个进程能创建」的原子性，锁就失效了
    mkdir -p "$LOGS_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo $$ > "$LOCK_DIR/pid"
        return 0
    fi
    local holder
    holder=$(cat "$LOCK_DIR/pid" 2>/dev/null)
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && proc_is_launcher "$holder"; then
        echo "[错误] 另一个 $APP_NAME 脚本实例（PID $holder）正在执行变更动作，请等它完成后再试"
        exit 1
    fi
    echo "[警告] 接管陈旧锁（持锁进程已不在）"
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || { echo "[错误] 无法创建锁目录 $LOCK_DIR"; exit 1; }
    echo $$ > "$LOCK_DIR/pid"
}

with_lock() {
    acquire_lock
    trap 'rm -rf "$LOCK_DIR"' EXIT   # 正常与异常退出都释放锁
    "$@"
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
        echo "[错误] 未找到 npm（构建前端需要 Node.js ≥ 18）"
        exit 1
    }
}

# 启动 uvicorn 并登记 PID 到 BACKEND_PID。服务进程必须脱离启动它的终端会话（关掉终端服务照常运行）：
# - stdout/stderr 重定向日志文件、stdin 接 /dev/null——三个标准流全部与终端脱钩（终端关闭时管道断裂不会波及进程）
# - nohup 忽略 SIGHUP + disown 移出 shell 作业表——shell 退出不级联
# PYTHONUTF8=1：Windows 下子进程缺省按 locale（GBK）写重定向输出，后端中文日志会乱码落盘——强制 UTF-8 与仓库文本编码一致
start_backend() {
    cd "$BACKEND_DIR"
    PYTHONUTF8=1 nohup "$PYTHON" -m uvicorn main:app \
        --host "$APP_HOST" --port "$PORT" \
        > "$BACKEND_LOG" 2>&1 < /dev/null &
    BACKEND_PID=$!
    disown
    cd - >/dev/null
}

# ── 前端依赖与构建（按需）──────────────────────────────────

# 需要重新 npm install 吗：node_modules 缺失（首次/被清理），或 package-lock.json 比安装戳新（依赖版本变更过，如 git pull 之后）
need_install() {
    [ -d "$FRONTEND_DIR/node_modules" ] || return 0
    [ -f "$NPM_STAMP" ] || return 0
    [ "$FRONTEND_DIR/package-lock.json" -nt "$NPM_STAMP" ]
}

# 按需安装依赖——新环境 clone 下来 ./start.sh 即一键启动；lockfile 未变时静默跳过，日常启动零开销
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

# ── PID 文件 ──────────────────────────────────────────────

# 读 PID 文件（key=value 行）。
# 只接受 word=digits 行——PID 文件是运行产物，内容被污染时按「无 PID 文件」处理，不 source 任意内容
pids_read() {
    [ -f "$PID_FILE" ] || return 1
    if grep -qvE '^[a-z]+=[0-9]+$' "$PID_FILE"; then
        echo "[警告] PID 文件内容异常，忽略并重建：$PID_FILE"
        return 1
    fi
    # shellcheck disable=SC1090
    . "$PID_FILE"
    return 0
}

pid_of() {
    echo "${backend:-}"
}

# ── 动作 ──────────────────────────────────────────────────

do_stop() {
    pids_read || true
    local pid
    pid=$(pid_of)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        # PID 复用防护：进程还在但已不是本应用（服务停止后 PID 被复用）
        if proc_is_ours "$pid"; then
            kill_tree "$pid"
            echo "[backend] 已停止 (PID: $pid)"
        else
            echo "[backend] PID $pid 已被其他进程复用，跳过（端口兜底清理见下）"
        fi
    fi
    # 兜底：PID 文件丢失或进程树杀不全但端口仍被占——按端口清
    if port_busy "$PORT"; then
        kill_port "$PORT" && echo "释放了无主进程占用的端口 $PORT"
    fi
    rm -f "$PID_FILE"
}

# 启动逻辑：以当前代码运行服务——检测到运行中的实例先说明再重启
do_start() {
    echo "========== 启动 $APP_NAME =========="
    mkdir -p "$LOGS_DIR"   # 日志目录由变更动作创建，只读动作（status/logs）无写副作用
    check_env
    pids_read || true
    local old
    old=$(pid_of)
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null && proc_is_ours "$old"; then
        echo "检测到运行中的实例 (PID: $old)，重启以加载当前代码"
    fi
    do_stop
    ensure_frontend

    echo "[backend] 启动 uvicorn ($APP_HOST:$PORT)..."
    start_backend
    echo "backend=$BACKEND_PID" > "$PID_FILE"

    if ! wait_healthy "$BACKEND_PID"; then
        do_stop
        exit 1
    fi
    echo "[backend] 就绪 (PID: $BACKEND_PID)，日志: $BACKEND_LOG"
    echo "======================================"
    echo "访问地址: http://$APP_HOST:$PORT"
    echo "停止服务: ./start.sh stop（服务运行中再次执行本命令即重启）"
}

do_status() {
    pids_read || true
    local pid
    pid=$(pid_of)
    # PID 复用防护：进程还在但已不是本应用时报「未在运行」，不误报
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && proc_is_ours "$pid"; then
        echo "[backend] 运行中 (PID: $pid)"
        if curl -sf -o /dev/null --max-time 2 "http://$(probe_host):$PORT/api/health" 2>/dev/null; then
            echo "[health] http://$APP_HOST:$PORT/api/health → ok"
        fi
    else
        echo "[backend] 未在运行"
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
    start)            with_lock do_start ;;
    stop)             with_lock do_stop ;;
    status)           do_status ;;
    logs)             do_logs ;;
    *)                echo "用法: $0 {start|stop|status|logs}" ;;
esac
