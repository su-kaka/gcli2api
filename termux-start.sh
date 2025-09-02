echo "强制同步项目代码，忽略本地修改..."
git fetch --all
git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)
uv add -r requirements-termux.txt
source .venv/bin/activate
pm2 start .venv/bin/python --name web -- web.py