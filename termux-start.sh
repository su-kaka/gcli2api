echo "强制同步项目代码，忽略本地修改..."
git fetch --all
git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)
echo "创建虚拟环境..."
PYTHON_VERSION=$(python -V 2>&1 | grep -oP '\d+\.\d+' | head -1)
if [ -n "$PYTHON_VERSION" ]; then
    echo "检测到 Python $PYTHON_VERSION，固定版本..."
    uv python pin "$PYTHON_VERSION"
fi
uv add -r requirements-termux.txt
source .venv/bin/activate
pm2 start .venv/bin/python --name gcli2api -- web.py
