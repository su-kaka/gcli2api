"""
Main Web Integration - Integrates all routers and modules
集合router并开启主服务
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

# Import all routers
from src.openai_router import router as openai_router
from src.gemini_router import router as gemini_router
from src.web_routes import router as web_router

# Import managers and utilities
from src.credential_manager import CredentialManager
from config import get_server_host, get_server_port
from log import log

# 全局凭证管理器
global_credential_manager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global global_credential_manager
    
    log.info("启动 GCLI2API 主服务")
    
    # 初始化全局凭证管理器
    try:
        global_credential_manager = CredentialManager()
        await global_credential_manager.initialize()
        log.info("凭证管理器初始化成功")
    except Exception as e:
        log.error(f"凭证管理器初始化失败: {e}")
        global_credential_manager = None
    
    # 自动从环境变量加载凭证
    try:
        from src.auth_api import auto_load_env_credentials_on_startup
        auto_load_env_credentials_on_startup()
    except Exception as e:
        log.error(f"自动加载环境变量凭证失败: {e}")
    
    # OAuth回调服务器将在需要时按需启动
    
    yield
    
    # 清理资源
    if global_credential_manager:
        await global_credential_manager.close()
    
    log.info("GCLI2API 主服务已停止")

# 创建FastAPI应用
app = FastAPI(
    title="GCLI2API",
    description="Gemini API proxy with OpenAI compatibility",
    version="2.0.0",
    lifespan=lifespan
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由器
# OpenAI兼容路由 - 处理OpenAI格式请求
app.include_router(
    openai_router,
    prefix="",
    tags=["OpenAI Compatible API"]
)

# Gemini原生路由 - 处理Gemini格式请求
app.include_router(
    gemini_router,
    prefix="",
    tags=["Gemini Native API"]
)

# Web路由 - 包含认证、凭证管理和控制面板功能
app.include_router(
    web_router,
    prefix="",
    tags=["Web Interface"]
)

# 保活接口（仅响应 HEAD）
@app.head("/keepalive")
async def keepalive() -> Response:
    return Response(status_code=200)

def get_credential_manager():
    """获取全局凭证管理器实例"""
    return global_credential_manager

# 导出给其他模块使用
__all__ = ['app', 'get_credential_manager']

if __name__ == "__main__":
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    
    # 从环境变量或配置获取端口和主机
    port = get_server_port()
    host = get_server_host()
    
    log.info("=" * 60)
    log.info("🚀 启动 GCLI2API")
    log.info("=" * 60)
    log.info(f"🔧 控制面板: http://127.0.0.1:{port}")
    log.info("=" * 60)
    log.info("🔗 API端点:")
    log.info(f"   OpenAI兼容: http://127.0.0.1:{port}/v1")
    log.info(f"   Gemini原生: http://127.0.0.1:{port}")

    # 配置hypercorn
    config = Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = "-"
    config.errorlog = "-"
    config.loglevel = "INFO"
    config.use_colors = True
    
    # 设置请求体大小限制为100MB
    config.max_request_body_size = 100 * 1024 * 1024
    
    # 设置连接超时
    config.keep_alive_timeout = 300  # 5分钟
    config.read_timeout = 300  # 5分钟读取超时
    config.write_timeout = 300  # 5分钟写入超时

    asyncio.run(serve(app, config))