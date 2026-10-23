"""
Main Web Integration - Integrates all routers and modules
集合router并开启主服务
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from log import log

# Import managers and utilities
# credential_manager 已重构为无状态函数模块（无需初始化/关闭）

# Import all routers
from src.router.antigravity.openai import router as antigravity_openai_router
from src.router.antigravity.gemini import router as antigravity_gemini_router
from src.router.antigravity.anthropic import router as antigravity_anthropic_router
from src.router.antigravity.model_list import router as antigravity_model_list_router
from src.panel import router as panel_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    log.info("启动 AGY2API 主服务")

    # 初始化配置缓存（优先执行）
    try:
        import config
        await config.init_config()
        log.info("配置缓存初始化成功")
    except Exception as e:
        log.error(f"配置缓存初始化失败: {e}")

    # 预热数据库连接（提前暴露配置问题）
    try:
        from src.storage.database import get_database
        await get_database()
        log.info("数据库连接就绪")
    except Exception as e:
        log.error(f"数据库初始化失败: {e}")

    # 初始化会话凭证缓存（Redis 可选，未配置时使用内存后端）
    try:
        import os as os_module
        from src import session_cache

        redis_url = os_module.getenv("REDIS_URL", "")
        if redis_url:
            await session_cache.init_redis(redis_url)
        else:
            log.info("未配置 REDIS_URL，会话凭证缓存使用内存后端")
    except Exception as e:
        log.warning(f"会话凭证缓存初始化失败（使用内存后端）: {e}")

    # OAuth回调服务器将在需要时按需启动

    yield

    # 清理资源
    log.info("开始关闭 AGY2API 主服务")

    # 关闭凭证管理器与数据库
    try:
        from src.storage.database import close_database
        await close_database()
        log.info("数据库已关闭")
    except Exception as e:
        log.error(f"关闭数据库时出错: {e}")

    # 关闭会话凭证缓存
    try:
        from src import session_cache
        await session_cache.close()
    except Exception as e:
        log.debug(f"关闭会话缓存时出错: {e}")

    log.info("AGY2API 主服务已停止")


class IPWhitelistMiddleware:
    """
    IP 白名单中间件（纯 ASGI，覆盖面板与全部 API 路由）

    配置 ip_whitelist 非空时，仅允许命中白名单的客户端 IP 访问，
    其余请求直接返回 403。白名单为空时不做任何限制（热更新即时生效）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import config

        try:
            enabled, rules = await config.get_ip_whitelist_rules()
        except Exception as e:
            # 配置读取失败时放行，避免锁定全部流量
            log.error(f"读取 IP 白名单配置失败，已放行本次请求: {e}")
            await self.app(scope, receive, send)
            return

        if enabled:
            client_ip = (scope.get("client") or (None, None))[0]
            if not client_ip or not config.ip_in_whitelist(client_ip, rules):
                log.warning(f"IP 白名单拦截请求: {client_ip} -> {scope.get('path', '')}")
                headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
                accept = headers.get("accept", "")
                response: JSONResponse | PlainTextResponse
                if "application/json" in accept or scope["path"].startswith(("/v1/", "/config/")):
                    response = JSONResponse(
                        status_code=403,
                        content={"error": {"message": "IP not allowed", "code": 403}},
                    )
                else:
                    response = PlainTextResponse("403 Forbidden: IP not allowed", status_code=403)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


# 创建FastAPI应用
app = FastAPI(
    title="AGY2API",
    description="Antigravity API proxy with OpenAI / Gemini / Claude compatibility",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# IP 白名单中间件（需在最外层，先于 CORS 与所有路由执行）
app.add_middleware(IPWhitelistMiddleware)

# 挂载路由器
# Antigravity路由 - 处理OpenAI格式请求并转换为Antigravity API
app.include_router(antigravity_openai_router, prefix="", tags=["Antigravity OpenAI API"])

# Antigravity路由 - 处理Gemini格式请求并转换为Antigravity API
app.include_router(antigravity_gemini_router, prefix="", tags=["Antigravity Gemini API"])

# Antigravity模型列表路由 - 处理Gemini格式的模型列表请求
app.include_router(antigravity_model_list_router, prefix="", tags=["Antigravity Model List"])

# Antigravity Anthropic Messages 路由 - Anthropic Messages 格式兼容
app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

# Panel路由 - 包含认证、凭证管理和控制面板功能
app.include_router(panel_router, prefix="", tags=["Panel Interface"])

# 静态文件路由 - 服务docs目录下的文件（目录不存在时跳过）
if os.path.isdir("docs"):
    app.mount("/docs", StaticFiles(directory="docs"), name="docs")


def main():
    """主启动函数：uvicorn 直接运行 ASGI 应用"""
    import uvicorn

    port = int(os.environ.get("PORT", 7861))
    host = os.environ.get("HOST", "0.0.0.0")
    workers = int(os.environ.get("WORKERS", 1))

    log.info("=" * 60)
    log.info("启动 AGY2API")
    log.info("=" * 60)
    log.info(f"控制面板: http://127.0.0.1:{port}")
    if workers > 1:
        log.info(f"Worker 数量: {workers}")
    log.info("=" * 60)

    uvicorn.run(
        "web:app",
        host=host,
        port=port,
        workers=workers if workers > 1 else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()
