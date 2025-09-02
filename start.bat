echo "强制同步项目代码，忽略本地修改..."
git fetch --all
git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)
uv sync
call .venv\Scripts\activate.bat
python web.py
pause