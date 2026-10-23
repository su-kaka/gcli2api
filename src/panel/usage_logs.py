"""
请求日志路由模块 - 处理 /usage-logs/* 相关的HTTP请求
查询 usage_log 表：分页列表、聚合统计、清理
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from config import get_usage_log_enabled, get_usage_log_retention_days
from log import log
from src.storage.database import get_database
from src.usage_log import clear_usage_logs
from src.utils import verify_panel_token


# 创建路由器
router = APIRouter(prefix="/usage-logs", tags=["usage-logs"])


@router.get("/list")
async def get_usage_logs(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    start_timestamp: Optional[float] = None,
    end_timestamp: Optional[float] = None,
    model_name: str = "",
    credential_email: str = "",
    api_format: str = "",
    status: str = "all",
    is_stream: Optional[bool] = None,
):
    """分页查询请求日志（新 -> 旧）"""
    try:
        if limit < 1 or limit > 1000:
            raise HTTPException(status_code=400, detail="limit 必须在 1-1000 之间")
        if offset < 0:
            raise HTTPException(status_code=400, detail="offset 不能为负数")

        database = await get_database()
        items, total = await database.query_usage_logs(
            offset=offset,
            limit=limit,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            model_name=model_name.strip(),
            credential_email=credential_email.strip(),
            api_format=api_format.strip(),
            status=status,
            is_stream=is_stream,
        )
        return JSONResponse(content={"items": items, "total": total})
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"查询请求日志失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_usage_stats(
    token: str = Depends(verify_panel_token),
    start_timestamp: Optional[float] = None,
    end_timestamp: Optional[float] = None,
    model_name: str = "",
    credential_email: str = "",
    api_format: str = "",
):
    """聚合统计：请求数 / 成功失败 / token 汇总 / 平均耗时 / 每模型分布"""
    try:
        database = await get_database()
        stats = await database.usage_log_stats(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            model_name=model_name.strip(),
            credential_email=credential_email.strip(),
            api_format=api_format.strip(),
        )
        return JSONResponse(content=stats)
    except Exception as e:
        log.error(f"查询日志统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear")
async def clear_logs(
    token: str = Depends(verify_panel_token),
    days: int = 30,
):
    """清理 N 天前的日志，返回删除条数"""
    try:
        if days < 0:
            raise HTTPException(status_code=400, detail="天数不能为负数")
        if days == 0:
            # 0 = 删除所有（cutoff = now）
            deleted = await clear_usage_logs(0)
            return JSONResponse(content={"message": "已清理全部日志", "deleted": deleted})
        deleted = await clear_usage_logs(days)
        return JSONResponse(content={"message": f"已清理 {days} 天前的日志", "deleted": deleted})
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"清理日志失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/truncate")
async def truncate_logs(token: str = Depends(verify_panel_token)):
    """清空日志：直接 DROP 整张 usage_log 表并重建，不留任何残留"""
    from fastapi import Response as FastAPIResponse

    try:
        from src.storage.database import UsageLog
        from sqlalchemy import text

        database = await get_database()
        # 先统计当前条数用于返回
        _, total = await database.query_usage_logs(offset=0, limit=1)

        async with database.session() as s:
            await s.execute(text(f"DROP TABLE IF EXISTS {UsageLog.__tablename__}"))

        # 重新建表（走统一的建表逻辑，保证结构最新）
        async with database._engine.begin() as conn:
            await conn.run_sync(database._create_tables)

        return JSONResponse(content={
            "message": "已清空全部日志（表已重建）",
            "deleted": total,
        })
    except Exception as e:
        log.error(f"清空日志失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_usage_log_config(token: str = Depends(verify_panel_token)):
    """获取日志开关与保留天数配置"""
    return JSONResponse(content={
        "enabled": await get_usage_log_enabled(),
        "retention_days": await get_usage_log_retention_days(),
    })
