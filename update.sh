#!/bin/bash
# 自动更新脚本 - 由 /version/update API 触发，在独立进程中执行
# 用法: bash update.sh [project_dir] [python_bin]

PROJECT_DIR="${1:-$(pwd)}"

cd "$PROJECT_DIR" || { echo "无法进入目录: $PROJECT_DIR"; exit 1; }

LOG_FILE="update.log"
log() { echo "[update] $(date '+%H:%M:%S') $*" | tee -a "$LOG_FILE"; }

> "$LOG_FILE"

log "等待旧服务退出释放端口..."
sleep 4

# ── 确保 uv 在 PATH 中 ──────────────────────────
if ! command -v uv > /dev/null 2>&1; then
    for env_file in "$HOME/.local/bin/env" "$HOME/.cargo/env"; do
        if [ -f "$env_file" ]; then
            # shellcheck source=/dev/null
            source "$env_file"
            break
        fi
    done
fi

# ── 拉取最新代码 ─────────────────────────────────
log "开始更新代码..."

if [ -d ".git" ]; then
    log "从 origin 拉取最新代码..."
    if ! git fetch origin 2>> "$LOG_FILE"; then
        log "git fetch 失败，放弃更新"
        exit 1
    fi
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    git reset --hard "origin/${BRANCH}" 2>> "$LOG_FILE"
    log "代码更新完成 (branch: $BRANCH)"
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

# ── 同步依赖 ─────────────────────────────────────
if command -v uv > /dev/null 2>&1; then
    log "同步依赖 (uv sync)..."
    if ! uv sync 2>> "$LOG_FILE"; then
        log "依赖同步失败，尝试继续重启..."
    fi
else
    log "未找到 uv，跳过依赖同步"
fi

# ── 选择 Python 解释器 ───────────────────────────
# 优先用项目 venv（与 install.sh / start.sh 保持一致）
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -n "${2:-}" ] && [ -x "${2}" ]; then
    PYTHON="${2}"
else
    PYTHON="python3"
fi

log "重启服务 ($PYTHON)..."
exec "$PYTHON" web.py
