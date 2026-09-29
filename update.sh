#!/bin/bash
# 自动更新脚本 - 由 /version/update API 触发，在独立进程中执行
# 用法: bash update.sh <project_dir> <python_bin>
set -e

PROJECT_DIR="${1}"
PYTHON_BIN="${2:-python}"
UPSTREAM_URL="https://github.com/su-kaka/gcli2api.git"

cd "$PROJECT_DIR" || { echo "[update] 无法进入项目目录: $PROJECT_DIR"; exit 1; }

echo "[update] 等待旧服务退出释放端口..."
sleep 4

echo "[update] 开始更新代码..."

if [ -d ".git" ]; then
    # 确保 upstream remote 存在（指向原仓库）
    if ! git remote get-url upstream > /dev/null 2>&1; then
        echo "[update] 添加 upstream remote: $UPSTREAM_URL"
        git remote add upstream "$UPSTREAM_URL"
    fi

    echo "[update] 从原仓库拉取最新代码..."
    git fetch upstream
    git reset --hard upstream/master

    # 如果配置了 GITHUB_TOKEN，将更新同步推送到 fork
    if [ -n "$GITHUB_TOKEN" ]; then
        echo "[update] 同步推送到 fork..."
        ORIGIN_URL=$(git remote get-url origin 2>/dev/null || echo "")
        if [ -n "$ORIGIN_URL" ]; then
            # 将 token 注入 URL 进行推送
            AUTH_URL=$(echo "$ORIGIN_URL" | sed "s|https://|https://x-access-token:${GITHUB_TOKEN}@|")
            git push "$AUTH_URL" master --force 2>/dev/null || echo "[update] 推送到 fork 失败，跳过"
        fi
    fi
else
    echo "[update] 非 Git 仓库，克隆原仓库..."
    git init
    git remote add origin "$UPSTREAM_URL"
    git fetch origin master
    git reset --hard origin/master
fi

# 同步依赖（如果安装了 uv）
if command -v uv > /dev/null 2>&1; then
    echo "[update] 同步依赖..."
    uv sync --quiet 2>/dev/null || true
fi

echo "[update] 重启服务..."
exec "$PYTHON_BIN" web.py
