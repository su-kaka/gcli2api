REM git fetch --all
REM for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set branch=%%b
REM git reset --hard origin/%branch%
uv sync
call .venv\Scripts\activate.bat
python web.py
pause