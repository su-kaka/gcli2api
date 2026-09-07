#!/usr/bin/env bash
set -Eeuo pipefail

# One-click deployment for qingan123/gcli2api.
# Usage: curl -fsSL https://raw.githubusercontent.com/qingan123/gcli2api/master/install-gcli2api.sh | bash

APP_DIR="${GCLI2API_DIR:-$HOME/gcli2api}"
PORT="${PORT:-7861}"
PASSWORD="${PASSWORD:-584248}"
REPO_URL="${GCLI2API_REPO:-https://github.com/qingan123/gcli2api.git}"
BRANCH="${GCLI2API_BRANCH:-master}"

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' '错误：未检测到 Docker，请先安装 Docker Engine 和 Docker Compose。' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf '%s\n' '错误：未检测到 Docker Compose v2。' >&2
  exit 1
fi

mkdir -p "$APP_DIR/data/creds"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  tmp_dir="${APP_DIR}.tmp.$$"
  rm -rf "$tmp_dir"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$tmp_dir"
  if [ -d "$APP_DIR/data/creds" ]; then
    cp -a "$APP_DIR/data/creds/." "$tmp_dir/data/creds/" 2>/dev/null || true
  fi
  rm -rf "$APP_DIR"
  mv "$tmp_dir" "$APP_DIR"
fi

mkdir -p "$APP_DIR/data/creds"
cat > "$APP_DIR/.env" <<EOF
PASSWORD=$PASSWORD
API_PASSWORD=$PASSWORD
PANEL_PASSWORD=$PASSWORD
PORT=$PORT
HOST=0.0.0.0
EOF
chmod 600 "$APP_DIR/.env"

cd "$APP_DIR"
docker compose build --pull
docker compose up -d --force-recreate
sleep 3
curl -fsS -H "Authorization: Bearer $PASSWORD" "http://127.0.0.1:$PORT/antigravity/v1/models" >/dev/null

printf '\n部署完成\n'
printf '目录: %s\n' "$APP_DIR"
printf '面板: http://%s:%s\n' "$(hostname -I | awk '{print $1}')" "$PORT"
printf 'Antigravity API: http://%s:%s/antigravity/v1\n' "$(hostname -I | awk '{print $1}')" "$PORT"
printf '密码: %s\n' "$PASSWORD"
printf '验证: HTTP 200\n'
