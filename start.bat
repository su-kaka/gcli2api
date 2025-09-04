git fetch --all
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set branch=%%b
git reset --hard origin/%branch%
uv sync
call .venv\Scripts\activate.bat
python web.py
pause