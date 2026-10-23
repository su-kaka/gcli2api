"""
Panel模块 - 整合所有控制面板路由
"""

from fastapi import APIRouter

from . import auth, creds, config_routes, version, root, usage_logs


def create_router() -> APIRouter:
    """创建并返回整合所有子路由的主路由器"""
    router = APIRouter()

    # 包含所有子路由
    router.include_router(root.router)
    router.include_router(auth.router)
    router.include_router(creds.router)
    router.include_router(config_routes.router)
    router.include_router(version.router)
    router.include_router(usage_logs.router)

    return router


# 导出主路由器
router = create_router()

__all__ = ["router"]
