echo "强制同步项目代码，忽略本地修改..."
git fetch --all
git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)
pip install -r requirements-termux.txt
pm2 start python --name web -- web.py