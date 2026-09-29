#!/bin/bash
# 自动更新脚本 - 由 /version/update API 触发，在独立进程中执行
# 用法: bash update.sh <project_dir> <python_bin>

PROJECT_DIR="${1}"
PYTHON_BIN="${2:-python}"
LOG_FILE="${PROJECT_DIR}/update.log"

log() { echo "[update] $(date '+%H:%M:%S') $*" | tee -a "$LOG_FILE"; }

cd "$PROJECT_DIR" || { echo "无法进入目录: $PROJECT_DIR"; exit 1; }

# 清空上次日志
> "$LOG_FILE"

log "等待旧服务退出释放端口..."
sleep 4

log "开始更新代码..."

if [ -d ".git" ]; then
    log "从 origin 拉取最新代码..."
    if ! git fetch origin 2>> "$LOG_FILE"; then
        log "git fetch 失败，放弃更新"
        exit 1
    fi
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    git reset --hard "origin/${BRANCH}" 2>> "$LOG_FILE"
    log "代码更新完成，当前分支: $BRANCH"
else
    ORIGIN_URL="${GCLI2API_REPO_URL:-https://github.com/su-kaka/gcli2api.git}"
    log "非 Git 仓库，初始化并克隆: $ORIGIN_URL"
    git init 2>> "$LOG_FILE"
    git remote add origin "$ORIGIN_URL" 2>> "$LOG_FILE"
    if ! git fetch origin master 2>> "$LOG_FILE"; then
        log "git fetch 失败，放弃更新"
        exit 1
    fi
    git reset --hard origin/master 2>> "$LOG_FILE"
    log "克隆完成"
fi

# 同步依赖（如果安装了 uv）
if command -v uv > /dev/null 2>&1; then
    log "同步依赖..."
    uv sync --quiet 2>> "$LOG_FILE" || true
fi

log "重启服务..."
exec "$PYTHON_BIN" web.py
