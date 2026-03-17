#!/bin/bash

# 避免交互式提示
export DEBIAN_FRONTEND=noninteractive

if [ "$(whoami)" = "root" ]; then
    echo "检测到root用户，正在退出..."
    exit
fi

echo "检查Termux镜像源配置..."

# 检查当前镜像源是否已经是Cloudflare镜像
target_mirror="https://packages-cf.termux.dev/apt/termux-main"
fallback_mirror="https://packages.termux.dev/apt/termux-main"
if [ -f "$PREFIX/etc/apt/sources.list" ] && grep -q "$target_mirror" "$PREFIX/etc/apt/sources.list"; then
    echo "✅ 镜像源已经配置为Cloudflare镜像，跳过修改"
else
    echo "正在设置Termux镜像为Cloudflare镜像..."
    
    # 备份原始sources.list文件
    if [ -f "$PREFIX/etc/apt/sources.list" ]; then
        echo "备份原始sources.list文件..."
        cp "$PREFIX/etc/apt/sources.list" "$PREFIX/etc/apt/sources.list.backup.$(date +%s)"
    fi
    
    # 写入新的镜像源
    echo "写入新的镜像源配置..."
    cat > "$PREFIX/etc/apt/sources.list" << 'EOF'
# Cloudflare镜像源
deb https://packages-cf.termux.dev/apt/termux-main stable main
EOF
    
    echo "✅ 镜像源已更新为: $target_mirror"
fi

ensure_dpkg_ready() {
    echo "检查并修复 dpkg/apt 状态..."
    # 等待可能存在的 apt/dpkg 进程结束
    if pgrep -f "apt|dpkg" >/dev/null 2>&1; then
        echo "检测到 apt/dpkg 正在运行，等待其结束..."
        while pgrep -f "apt|dpkg" >/dev/null 2>&1; do sleep 1; done
    fi
    # 清理可能残留的锁（若无进程）
    for f in "$PREFIX/var/lib/dpkg/lock" \
             "$PREFIX/var/lib/apt/lists/lock" \
             "$PREFIX/var/cache/apt/archives/lock"; do
        [ -e "$f" ] && rm -f "$f"
    done
    # 尝试继续未完成的配置
    dpkg --configure -a || true
}

# 更新包列表并检查错误
echo "正在更新包列表..."
ensure_dpkg_ready
apt_output=$(apt update 2>&1)
if [ $? -ne 0 ]; then
    if echo "$apt_output" | grep -qi "is not signed"; then
        echo "⚠️ 检测到仓库未签名，尝试切换到官方镜像并修复 keyring..."
        # 切换到官方镜像
        sed -i "s#${target_mirror}#${fallback_mirror}#g" "$PREFIX/etc/apt/sources.list" || true
        # 清理列表与锁
        rm -rf "$PREFIX/var/lib/apt/lists/"* || true
        rm -f "$PREFIX/var/lib/dpkg/lock" "$PREFIX/var/lib/apt/lists/lock" "$PREFIX/var/cache/apt/archives/lock" || true
        # 重新安装 termux-keyring（若已安装则强制重装）
        apt-get install --reinstall -y termux-keyring || true
        # 再次更新
        ensure_dpkg_ready
        apt update
    else
        echo "apt update 失败，错误信息："
        echo "$apt_output" | head -20
        exit 1
    fi
else
    echo "$apt_output"
fi

echo "✅ Termux镜像设置完成！"
echo "📁 原始配置已备份到: $PREFIX/etc/apt/sources.list.backup.*"
echo "🔄 如需恢复原始镜像，可以运行:"
echo "   cp \$PREFIX/etc/apt/sources.list.backup.* \$PREFIX/etc/apt/sources.list && apt update"

# 检查是否需要更新包管理器和安装软件
need_update=false
packages_to_install=""

# 检查 uv 是否已安装
if ! command -v uv &> /dev/null; then
    need_update=true
    packages_to_install="$packages_to_install uv"
fi

# 检查 python 是否已安装
if ! command -v python &> /dev/null; then
    need_update=true
    packages_to_install="$packages_to_install python"
fi

# 检查 nodejs 是否已安装
if ! command -v node &> /dev/null; then
    need_update=true
    packages_to_install="$packages_to_install nodejs"
fi

# 检查 git 是否已安装
if ! command -v git &> /dev/null; then
    need_update=true
    packages_to_install="$packages_to_install git"
fi

# 如果需要安装软件，则更新包管理器并安装
if [ "$need_update" = true ]; then
    echo "正在更新包管理器..."
    ensure_dpkg_ready
    echo "正在安装缺失的软件包: $packages_to_install"
    pkg install $packages_to_install -y
else
    echo "所需软件包已全部安装，跳过更新和安装步骤"
fi

# 检查 pm2 是否已安装
if ! command -v pm2 &> /dev/null; then
    echo "正在安装 pm2..."
    npm install pm2 -g
else
    echo "pm2 已安装，跳过安装"
fi

# 项目目录处理逻辑
if [ -f "./web.py" ]; then
    # Already in target directory; skip clone and cd
    echo "已在目标目录中，跳过克隆操作"
elif [ -f "./gcli2api/web.py" ]; then
    echo "进入已存在的 gcli2api 目录"
    cd ./gcli2api
else
    echo "克隆项目仓库..."
    git clone https://github.com/su-kaka/gcli2api.git
    cd ./gcli2api
fi

echo "强制同步项目代码，忽略本地修改..."
git fetch --all
git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)

# 只在不存在时创建
if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    rm pyproject.toml
    uv init
    uv venv
else
    echo "虚拟环境已存在，跳过创建"
fi

echo "安装 Python 依赖..."
uv add -r requirements-termux.txt

echo "激活虚拟环境并启动服务..."
source .venv/bin/activate
pm2 start .venv/bin/python --name web -- web.py
cd ..