#!/usr/bin/env bash
set -Eeuo pipefail

# One-click uninstall. Credentials are preserved by default.
# Set REMOVE_DATA=1 to remove credentials and local deployment data too.

APP_DIR="${GCLI2API_DIR:-$HOME/gcli2api}"
REMOVE_DATA="${REMOVE_DATA:-0}"

if [ ! -d "$APP_DIR" ]; then
  printf '未找到部署目录: %s\n' "$APP_DIR"
  exit 0
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [ -f "$APP_DIR/docker-compose.yml" ]; then
  docker compose -f "$APP_DIR/docker-compose.yml" down --remove-orphans || true
fi

if [ "$REMOVE_DATA" = "1" ]; then
  rm -rf "$APP_DIR"
  printf '已卸载并删除部署目录及凭证数据: %s\n' "$APP_DIR"
else
  rm -f "$APP_DIR/.env"
  rm -rf "$APP_DIR/.git" "$APP_DIR"/source-inspect
  printf '已卸载服务；凭证数据保留在: %s/data/creds\n' "$APP_DIR"
  printf '如需删除凭证和全部数据，请执行: REMOVE_DATA=1 %s\n' "$0"
fi
